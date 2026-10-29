import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.database import get_db
from app.models.auth import (
    AuditEventType,
    AuditReasonCode,
    AuditResult,
    SecurityAuditEventRecord,
    SessionRevokeReason,
    UserRecord,
    UserRole,
    UserSessionRecord,
)
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateBindingStatus,
)
from app.schemas.auth import (
    AuthMeResponse,
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    UserResponse,
)
from app.services.security import (
    canonicalize_email,
    dummy_verify_password,
    generate_secure_token,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
)

logger = logging.getLogger("ai_hiring.api.auth")

router = APIRouter(
    prefix="/api/v1/auth",
    tags=["auth"],
)

GENERIC_AUTH_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={
        "code": "INVALID_CREDENTIALS",
        "message": "Unable to sign in with the provided credentials.",
    },
)

UNAUTHENTICATED_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={
        "code": "UNAUTHENTICATED",
        "message": "Authentication required. Session is invalid, expired, or missing.",
    },
)

CSRF_DENIED_ERROR = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail={
        "code": "CSRF_DENIED",
        "message": "Security validation failed. Missing, invalid, or mismatched CSRF token.",
    },
)


def _set_session_cookie(response: Response, raw_token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        max_age=settings.session_absolute_lifetime_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def _set_candidate_session_cookie(response: Response, raw_token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.candidate_session_cookie_name,
        value=raw_token,
        max_age=settings.session_absolute_lifetime_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def _clear_candidate_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.candidate_session_cookie_name,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def _record_audit_event(
    db: Session,
    event_type: AuditEventType,
    result: AuditResult,
    reason_code: AuditReasonCode,
    actor_user_id: Optional[uuid.UUID] = None,
    resource_type: Optional[str] = "user",
    resource_id: Optional[str] = None,
    safe_metadata: Optional[dict] = None,
) -> None:
    audit_rec = SecurityAuditEventRecord(
        event_id=uuid.uuid4(),
        actor_user_id=actor_user_id,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        reason_code=reason_code,
        safe_metadata=safe_metadata or {},
        occurred_at=datetime.now(timezone.utc),
    )
    db.add(audit_rec)


class AuthenticatedContext:
    def __init__(self, user: UserRecord, session: UserSessionRecord):
        self.user = user
        self.session = session


async def get_authenticated_context(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedContext:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise UNAUTHENTICATED_ERROR

    token_digest = hash_token(raw_token)
    now = datetime.now(timezone.utc)

    stmt = (
        select(UserSessionRecord)
        .options(selectinload(UserSessionRecord.user))
        .where(UserSessionRecord.token_hash == token_digest)
    )
    user_session = db.execute(stmt).scalar_one_or_none()

    if user_session is None or user_session.revoked_at is not None:
        raise UNAUTHENTICATED_ERROR

    # Check absolute and idle expiration
    # Ensure datetimes are timezone aware for comparison
    abs_exp = user_session.absolute_expires_at
    if abs_exp.tzinfo is None:
        abs_exp = abs_exp.replace(tzinfo=timezone.utc)

    idle_exp = user_session.idle_expires_at
    if idle_exp.tzinfo is None:
        idle_exp = idle_exp.replace(tzinfo=timezone.utc)

    if now >= abs_exp or now >= idle_exp:
        # Mark expired in DB
        reason = SessionRevokeReason.EXPIRED_ABSOLUTE if now >= abs_exp else SessionRevokeReason.EXPIRED_IDLE
        user_session.revoked_at = now
        user_session.revoke_reason = reason
        _record_audit_event(
            db=db,
            event_type=AuditEventType.SESSION_REVOKED,
            result=AuditResult.SUCCESS,
            reason_code=AuditReasonCode.SESSION_EXPIRED,
            actor_user_id=user_session.user_id,
            resource_type="user_session",
            resource_id=str(user_session.session_id),
            safe_metadata={"revoke_reason": reason.value},
        )
        db.commit()
        raise UNAUTHENTICATED_ERROR

    user = user_session.user
    if user is None or not user.is_active:
        raise UNAUTHENTICATED_ERROR

    # Update last_seen_at and slide idle_expires_at (bounded by absolute_expires_at)
    # Throttle updates if last_seen_at was updated within the last 60 seconds
    last_seen = user_session.last_seen_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)

    if (now - last_seen).total_seconds() > 60:
        user_session.last_seen_at = now
        new_idle = now + timedelta(seconds=settings.session_idle_lifetime_seconds)
        user_session.idle_expires_at = min(new_idle, abs_exp)
        db.commit()

    return AuthenticatedContext(user=user, session=user_session)


async def require_active_user(
    auth_ctx: AuthenticatedContext = Depends(get_authenticated_context),
) -> AuthenticatedContext:
    """
    Enforces that the authenticated user is active and does NOT have an outstanding forced password change.
    If must_change_password is true, access to protected platform routes is denied with 403.
    """
    if not auth_ctx.user.is_active:
        raise UNAUTHENTICATED_ERROR

    if auth_ctx.user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PASSWORD_CHANGE_REQUIRED",
                "message": "Password change required before accessing platform features.",
            },
        )

    return auth_ctx


def require_role(allowed_roles: set[UserRole]):
    """
    FastAPI dependency factory enforcing that the authenticated user possesses one of the allowed roles.
    Denies access with 403 FORBIDDEN and logs a security audit event on unauthorized attempt.
    """
    async def _role_checker(
        auth_ctx: AuthenticatedContext = Depends(require_active_user),
        db: Session = Depends(get_db),
    ) -> AuthenticatedContext:
        if auth_ctx.user.role not in allowed_roles:
            _record_audit_event(
                db=db,
                event_type=AuditEventType.SESSION_REVOKED,
                result=AuditResult.DENIED,
                reason_code=AuditReasonCode.UNAUTHORIZED,
                actor_user_id=auth_ctx.user.user_id,
                resource_type="role_access",
                resource_id=auth_ctx.user.role.value,
                safe_metadata={
                    "user_role": auth_ctx.user.role.value,
                    "required_roles": [r.value for r in allowed_roles],
                },
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FORBIDDEN",
                    "message": "Insufficient permissions to access this resource.",
                },
            )
        return auth_ctx

    return _role_checker


require_admin = require_role({UserRole.ADMIN})
require_recruiter = require_role({UserRole.RECRUITER})
require_reviewer = require_role({UserRole.REVIEWER})
require_recruiter_or_admin = require_role({UserRole.RECRUITER, UserRole.ADMIN})
require_reviewer_or_admin = require_role({UserRole.REVIEWER, UserRole.ADMIN})


class CandidateAuthenticatedContext:
    def __init__(
        self,
        user: UserRecord,
        session: UserSessionRecord,
        binding: Optional[CandidateAccountBindingRecord] = None,
    ):
        from app.services.candidate_authorization_kernel import CandidatePrincipal
        self.user = user
        self.session = session
        self.binding = binding
        self.principal = CandidatePrincipal.from_context(user, session, binding)


async def get_candidate_authenticated_context(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CandidateAuthenticatedContext:
    # 1. Strictly inspect Candidate session namespace cookie only (no fallback to staff cookie)
    raw_token = request.cookies.get(settings.candidate_session_cookie_name)
    if not raw_token:
        raise UNAUTHENTICATED_ERROR

    token_digest = hash_token(raw_token)
    now = datetime.now(timezone.utc)

    stmt = (
        select(UserSessionRecord)
        .options(selectinload(UserSessionRecord.user))
        .where(UserSessionRecord.token_hash == token_digest)
    )
    user_session = db.execute(stmt).scalar_one_or_none()

    if user_session is None or user_session.revoked_at is not None:
        raise UNAUTHENTICATED_ERROR

    abs_exp = user_session.absolute_expires_at
    if abs_exp.tzinfo is None:
        abs_exp = abs_exp.replace(tzinfo=timezone.utc)

    idle_exp = user_session.idle_expires_at
    if idle_exp.tzinfo is None:
        idle_exp = idle_exp.replace(tzinfo=timezone.utc)

    if now >= abs_exp or now >= idle_exp:
        reason = SessionRevokeReason.EXPIRED_ABSOLUTE if now >= abs_exp else SessionRevokeReason.EXPIRED_IDLE
        user_session.revoked_at = now
        user_session.revoke_reason = reason
        _record_audit_event(
            db=db,
            event_type=AuditEventType.SESSION_REVOKED,
            result=AuditResult.SUCCESS,
            reason_code=AuditReasonCode.SESSION_EXPIRED,
            actor_user_id=user_session.user_id,
            resource_type="user_session",
            resource_id=str(user_session.session_id),
            safe_metadata={"revoke_reason": reason.value, "portal": "CANDIDATE"},
        )
        db.commit()
        raise UNAUTHENTICATED_ERROR

    user = user_session.user
    if user is None or not user.is_active or user.role != UserRole.CANDIDATE:
        raise UNAUTHENTICATED_ERROR

    # Check CandidateAccountBindingRecord if it exists
    binding_stmt = select(CandidateAccountBindingRecord).where(
        CandidateAccountBindingRecord.user_id == user.user_id
    )
    binding = db.execute(binding_stmt).scalar_one_or_none()
    if binding is not None and binding.status != CandidateBindingStatus.ACTIVE.value:
        _record_audit_event(
            db=db,
            event_type=AuditEventType.ACCESS_DENIED,
            result=AuditResult.DENIED,
            reason_code=AuditReasonCode.BINDING_SUSPENDED,
            actor_user_id=user.user_id,
            resource_type="candidate_account_binding",
            resource_id=str(binding.binding_id),
            safe_metadata={"status": binding.status, "portal": "CANDIDATE"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "CANDIDATE_ACCESS_SUSPENDED",
                "message": "Candidate access has been suspended or is not active.",
            },
        )

    last_seen = user_session.last_seen_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)

    if (now - last_seen).total_seconds() > 60:
        user_session.last_seen_at = now
        new_idle = now + timedelta(seconds=settings.session_idle_lifetime_seconds)
        user_session.idle_expires_at = min(new_idle, abs_exp)
        db.commit()

    return CandidateAuthenticatedContext(user=user, session=user_session, binding=binding)


async def require_candidate(
    cand_ctx: CandidateAuthenticatedContext = Depends(get_candidate_authenticated_context),
) -> CandidateAuthenticatedContext:
    """Enforces active Candidate session for an active candidate user."""
    return cand_ctx


def verify_candidate_csrf_and_origin(
    request: Request,
    cand_ctx: CandidateAuthenticatedContext = Depends(get_candidate_authenticated_context),
    x_csrf_token: Optional[str] = Header(default=None, alias="X-CSRF-Token"),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> CandidateAuthenticatedContext:
    origin = request.headers.get("origin")
    if origin:
        norm_origin = origin.strip().rstrip("/")
        allowed_origins = [o.strip().rstrip("/") for o in settings.get_allowed_frontend_origins()]
        if norm_origin not in allowed_origins:
            _record_audit_event(
                db=db,
                event_type=AuditEventType.ACCESS_DENIED,
                result=AuditResult.DENIED,
                reason_code=AuditReasonCode.UNAUTHORIZED,
                actor_user_id=cand_ctx.user.user_id,
                resource_type="candidate_session",
                resource_id=str(cand_ctx.session.session_id),
                safe_metadata={"denial_reason": "ORIGIN_MISMATCH"},
            )
            db.commit()
            raise CSRF_DENIED_ERROR

    if not x_csrf_token or not x_csrf_token.strip():
        _record_audit_event(
            db=db,
            event_type=AuditEventType.ACCESS_DENIED,
            result=AuditResult.DENIED,
            reason_code=AuditReasonCode.UNAUTHORIZED,
            actor_user_id=cand_ctx.user.user_id,
            resource_type="candidate_session",
            resource_id=str(cand_ctx.session.session_id),
            safe_metadata={"denial_reason": "MISSING_CSRF_TOKEN"},
        )
        db.commit()
        raise CSRF_DENIED_ERROR

    token_digest = hash_token(x_csrf_token.strip())
    expected_digest = cand_ctx.session.csrf_token_hash

    if not hmac.compare_digest(token_digest, expected_digest):
        _record_audit_event(
            db=db,
            event_type=AuditEventType.ACCESS_DENIED,
            result=AuditResult.DENIED,
            reason_code=AuditReasonCode.UNAUTHORIZED,
            actor_user_id=cand_ctx.user.user_id,
            resource_type="candidate_session",
            resource_id=str(cand_ctx.session.session_id),
            safe_metadata={"denial_reason": "INVALID_CSRF_TOKEN"},
        )
        db.commit()
        raise CSRF_DENIED_ERROR

    return cand_ctx


def verify_csrf_and_origin(

    request: Request,
    auth_ctx: AuthenticatedContext = Depends(get_authenticated_context),
    x_csrf_token: Optional[str] = Header(default=None, alias="X-CSRF-Token"),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> AuthenticatedContext:
    # 1. Validate Origin header if present
    origin = request.headers.get("origin")
    if origin:
        norm_origin = origin.strip().rstrip("/")
        allowed_origins = [o.strip().rstrip("/") for o in settings.get_allowed_frontend_origins()]
        if norm_origin not in allowed_origins:
            _record_audit_event(
                db=db,
                event_type=AuditEventType.SESSION_REVOKED,
                result=AuditResult.DENIED,
                reason_code=AuditReasonCode.UNAUTHORIZED,
                actor_user_id=auth_ctx.user.user_id,
                resource_type="user_session",
                resource_id=str(auth_ctx.session.session_id),
                safe_metadata={"denial_reason": "ORIGIN_MISMATCH"},
            )
            db.commit()
            raise CSRF_DENIED_ERROR

    # 2. Validate X-CSRF-Token header
    if not x_csrf_token or not x_csrf_token.strip():
        _record_audit_event(
            db=db,
            event_type=AuditEventType.SESSION_REVOKED,
            result=AuditResult.DENIED,
            reason_code=AuditReasonCode.UNAUTHORIZED,
            actor_user_id=auth_ctx.user.user_id,
            resource_type="user_session",
            resource_id=str(auth_ctx.session.session_id),
            safe_metadata={"denial_reason": "MISSING_CSRF_TOKEN"},
        )
        db.commit()
        raise CSRF_DENIED_ERROR

    token_digest = hash_token(x_csrf_token.strip())
    expected_digest = auth_ctx.session.csrf_token_hash

    if not hmac.compare_digest(token_digest, expected_digest):
        _record_audit_event(
            db=db,
            event_type=AuditEventType.SESSION_REVOKED,
            result=AuditResult.DENIED,
            reason_code=AuditReasonCode.UNAUTHORIZED,
            actor_user_id=auth_ctx.user.user_id,
            resource_type="user_session",
            resource_id=str(auth_ctx.session.session_id),
            safe_metadata={"denial_reason": "INVALID_CSRF_TOKEN"},
        )
        db.commit()
        raise CSRF_DENIED_ERROR

    return auth_ctx


def _create_db_session_with_collision_handling(
    db: Session,
    user_id: uuid.UUID,
    settings: Settings,
    now: datetime,
) -> Tuple[UserSessionRecord, str, str]:
    """
    Creates a new user session in the DB, generating 256-bit random session and CSRF tokens.
    Handles potential SHA-256 digest collisions by regenerating token material.
    """
    for _ in range(5):
        raw_session_token = generate_secure_token()
        raw_csrf_token = generate_secure_token()
        session_token_hash = hash_token(raw_session_token)
        csrf_token_hash = hash_token(raw_csrf_token)

        abs_expires = now + timedelta(seconds=settings.session_absolute_lifetime_seconds)
        idle_expires = min(
            now + timedelta(seconds=settings.session_idle_lifetime_seconds),
            abs_expires,
        )

        user_session = UserSessionRecord(
            session_id=uuid.uuid4(),
            user_id=user_id,
            token_hash=session_token_hash,
            csrf_token_hash=csrf_token_hash,
            created_at=now,
            last_seen_at=now,
            idle_expires_at=idle_expires,
            absolute_expires_at=abs_expires,
        )
        db.add(user_session)
        try:
            db.flush()
            return user_session, raw_session_token, raw_csrf_token
        except IntegrityError:
            db.rollback()
            continue

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "SERVICE_UNAVAILABLE", "message": "Unable to initialize session."},
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate User and Create Session",
    description=(
        "Authenticates credentials using constant-time scrypt verification, applies failed-attempt cooldown, "
        "creates a server-backed revocable session, sets the HttpOnly session cookie, and returns safe user data and a CSRF token."
    ),
)
async def login(
    login_req: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LoginResponse:
    now = datetime.now(timezone.utc)
    canonical_email = login_req.email

    stmt = select(UserRecord).where(UserRecord.email == canonical_email)
    user = db.execute(stmt).scalar_one_or_none()

    if user is None:
        dummy_verify_password(login_req.password)
        _record_audit_event(
            db=db,
            event_type=AuditEventType.LOGIN_ATTEMPT,
            result=AuditResult.FAILURE,
            reason_code=AuditReasonCode.CREDENTIALS_INVALID,
            actor_user_id=None,
            safe_metadata={"attempted_email": canonical_email},
        )
        db.commit()
        raise GENERIC_AUTH_ERROR

    # Check cooldown / locked_until
    if user.locked_until is not None:
        locked_until_utc = user.locked_until
        if locked_until_utc.tzinfo is None:
            locked_until_utc = locked_until_utc.replace(tzinfo=timezone.utc)

        if now < locked_until_utc:
            # Account is currently in cooldown
            dummy_verify_password(login_req.password)
            _record_audit_event(
                db=db,
                event_type=AuditEventType.LOGIN_ATTEMPT,
                result=AuditResult.DENIED,
                reason_code=AuditReasonCode.ACCOUNT_LOCKED,
                actor_user_id=user.user_id,
                safe_metadata={"cooldown_active": True},
            )
            db.commit()
            raise GENERIC_AUTH_ERROR
        else:
            # Cooldown expired, clear lock
            user.locked_until = None
            user.failed_login_count = 0

    # Verify password with constant-time scrypt
    is_valid = verify_password(login_req.password, user.password_hash)

    if not is_valid or not user.is_active:
        user.failed_login_count += 1
        cooldown_applied = False
        if user.failed_login_count >= settings.login_failure_threshold:
            user.locked_until = now + timedelta(seconds=settings.login_cooldown_seconds)
            cooldown_applied = True
            _record_audit_event(
                db=db,
                event_type=AuditEventType.ACCOUNT_LOCKED,
                result=AuditResult.SUCCESS,
                reason_code=AuditReasonCode.ACCOUNT_LOCKED,
                actor_user_id=user.user_id,
                safe_metadata={"failure_count": user.failed_login_count},
            )

        _record_audit_event(
            db=db,
            event_type=AuditEventType.LOGIN_ATTEMPT,
            result=AuditResult.FAILURE,
            reason_code=AuditReasonCode.CREDENTIALS_INVALID if user.is_active else AuditReasonCode.UNAUTHORIZED,
            actor_user_id=user.user_id,
            safe_metadata={
                "cooldown_applied": cooldown_applied,
                "is_active": user.is_active,
            },
        )
        db.commit()
        raise GENERIC_AUTH_ERROR

    # Successful authentication: reset failure count, update last_login_at
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now

    # Check if rehash is needed due to updated parameters
    if needs_rehash(
        user.password_hash,
        target_n=settings.scrypt_n,
        target_r=settings.scrypt_r,
        target_p=settings.scrypt_p,
        target_dklen=settings.scrypt_dklen,
    ):
        user.password_hash = hash_password(
            plaintext_password=login_req.password,
            n=settings.scrypt_n,
            r=settings.scrypt_r,
            p=settings.scrypt_p,
            dklen=settings.scrypt_dklen,
        )

    # Create server session
    user_session, raw_session_token, raw_csrf_token = _create_db_session_with_collision_handling(
        db=db,
        user_id=user.user_id,
        settings=settings,
        now=now,
    )

    _record_audit_event(
        db=db,
        event_type=AuditEventType.LOGIN_ATTEMPT,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.SUCCESSFUL_AUTHENTICATION,
        actor_user_id=user.user_id,
        resource_type="user_session",
        resource_id=str(user_session.session_id),
        safe_metadata={"role": user.role.value},
    )

    db.commit()
    _set_session_cookie(response, raw_session_token, settings)

    return LoginResponse(
        user=UserResponse(
            user_id=user.user_id,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            is_active=user.is_active,
            must_change_password=user.must_change_password,
            locked_until=user.locked_until,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
            updated_at=user.updated_at,
        ),
        csrf_token=raw_csrf_token,
    )


@router.get(
    "/me",
    response_model=AuthMeResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Current Authenticated User Session",
    description="Returns safe profile data, role, permissions state, session expiry, and CSRF token for the authenticated caller.",
)
async def get_current_user_me(
    auth_ctx: AuthenticatedContext = Depends(get_authenticated_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthMeResponse:
    user = auth_ctx.user
    session = auth_ctx.session

    # Generate a fresh raw CSRF token and update session hash for frontend usage
    raw_csrf_token = generate_secure_token()
    session.csrf_token_hash = hash_token(raw_csrf_token)
    db.commit()

    return AuthMeResponse(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        idle_expires_at=session.idle_expires_at,
        absolute_expires_at=session.absolute_expires_at,
        csrf_token=raw_csrf_token,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke Current Session and Logout",
    description="Atomically revokes the current database session, clears the session cookie, and records an audit event.",
)
async def logout(
    response: Response,
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    now = datetime.now(timezone.utc)
    session = auth_ctx.session

    session.revoked_at = now
    session.revoke_reason = SessionRevokeReason.LOGOUT

    _record_audit_event(
        db=db,
        event_type=AuditEventType.LOGOUT,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.SUCCESSFUL_AUTHENTICATION,
        actor_user_id=auth_ctx.user.user_id,
        resource_type="user_session",
        resource_id=str(session.session_id),
        safe_metadata={"revoke_reason": SessionRevokeReason.LOGOUT.value},
    )

    db.commit()
    _clear_session_cookie(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/change-password",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Change Password and Rotate Session",
    description=(
        "Verifies current password, checks complexity and mismatch against current password, hashes new password with fresh salt, "
        "revokes all other active user sessions, rotates the current session and CSRF token, and returns updated session state."
    ),
)
async def change_password(
    change_req: ChangePasswordRequest,
    response: Response,
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LoginResponse:
    user = auth_ctx.user
    current_session = auth_ctx.session
    now = datetime.now(timezone.utc)

    # 1. Verify confirmation matches new password
    if change_req.new_password != change_req.new_password_confirmation:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "PASSWORD_CONFIRMATION_MISMATCH",
                "message": "New password and confirmation password do not match.",
            },
        )

    # 2. Verify current password
    if not verify_password(change_req.current_password, user.password_hash):
        _record_audit_event(
            db=db,
            event_type=AuditEventType.PASSWORD_CHANGED,
            result=AuditResult.FAILURE,
            reason_code=AuditReasonCode.CREDENTIALS_INVALID,
            actor_user_id=user.user_id,
            safe_metadata={"error": "INCORRECT_CURRENT_PASSWORD"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_CURRENT_PASSWORD",
                "message": "The current password provided is incorrect.",
            },
        )

    # 3. Reject password reuse
    if verify_password(change_req.new_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "PASSWORD_REUSE_FORBIDDEN",
                "message": "New password cannot be the same as the current password.",
            },
        )

    try:
        # 4. Hash new password with fresh salt
        new_hash = hash_password(
            plaintext_password=change_req.new_password,
            n=settings.scrypt_n,
            r=settings.scrypt_r,
            p=settings.scrypt_p,
            dklen=settings.scrypt_dklen,
        )
        user.password_hash = new_hash
        user.must_change_password = False

        # 5. Revoke all other active sessions for this user
        revoke_other_stmt = (
            update(UserSessionRecord)
            .where(
                UserSessionRecord.user_id == user.user_id,
                UserSessionRecord.session_id != current_session.session_id,
                UserSessionRecord.revoked_at.is_(None),
            )
            .values(
                revoked_at=now,
                revoke_reason=SessionRevokeReason.PASSWORD_CHANGED,
            )
        )
        db.execute(revoke_other_stmt)

        # 6. Rotate current session token & CSRF token
        raw_new_session_token = generate_secure_token()
        raw_new_csrf_token = generate_secure_token()
        current_session.token_hash = hash_token(raw_new_session_token)
        current_session.csrf_token_hash = hash_token(raw_new_csrf_token)
        current_session.last_seen_at = now
        abs_exp = now + timedelta(seconds=settings.session_absolute_lifetime_seconds)
        current_session.absolute_expires_at = abs_exp
        current_session.idle_expires_at = min(
            now + timedelta(seconds=settings.session_idle_lifetime_seconds),
            abs_exp,
        )

        _record_audit_event(
            db=db,
            event_type=AuditEventType.PASSWORD_CHANGED,
            result=AuditResult.SUCCESS,
            reason_code=AuditReasonCode.SUCCESSFUL_AUTHENTICATION,
            actor_user_id=user.user_id,
            resource_type="user",
            resource_id=str(user.user_id),
            safe_metadata={"sessions_rotated": True},
        )

        db.commit()
        _set_session_cookie(response, raw_new_session_token, settings)

        return LoginResponse(
            user=UserResponse(
                user_id=user.user_id,
                email=user.email,
                display_name=user.display_name,
                role=user.role,
                is_active=user.is_active,
                must_change_password=user.must_change_password,
                locked_until=user.locked_until,
                last_login_at=user.last_login_at,
                created_at=user.created_at,
                updated_at=user.updated_at,
            ),
            csrf_token=raw_new_csrf_token,
        )

    except Exception as e:
        db.rollback()
        logger.error("Failed to execute password change", extra={"error_type": type(e).__name__})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "PASSWORD_CHANGE_FAILED", "message": "Failed to update password."},
        )
