import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
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
from app.models.candidate import CandidateIdentityRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateBindingStatus,
    CandidateInvitationRecord,
)
from app.schemas.candidate_access import (
    CandidateAccessInvitationInfo,
    CandidateAccessStatusResponse,
    CandidateActivationRequest,
    CandidateInvitationSummary,
    CandidateRegisterRequest,
    CreateCandidateInvitationRequest,
)
from app.services.security import (
    canonicalize_email,
    generate_secure_token,
    hash_password,
    hash_token,
)

logger = logging.getLogger("ai_hiring.services.candidate_access")

DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _record_audit_event(
    db: Session,
    event_type: AuditEventType,
    result: AuditResult,
    reason_code: AuditReasonCode,
    actor_user_id: Optional[uuid.UUID] = None,
    resource_type: Optional[str] = "candidate_access",
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


def create_candidate_invitation(
    db: Session,
    recruiter_user: UserRecord,
    req: CreateCandidateInvitationRequest,
    settings: Settings,
) -> Tuple[CandidateInvitationRecord, str, str]:
    """
    Creates a single-use candidate invitation link.
    Enforces recruiter authorization, candidate existence, bounds expiry,
    hashes the token with SHA-256, and returns the raw token and activation URL.
    """
    now = datetime.now(timezone.utc)
    org_id = req.organization_id or DEFAULT_ORG_ID

    # 1. Verify candidate identity exists
    cand_stmt = select(CandidateIdentityRecord).where(
        CandidateIdentityRecord.identity_id == req.candidate_id
    )
    candidate = db.execute(cand_stmt).scalar_one_or_none()
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "CANDIDATE_NOT_FOUND",
                "message": f"Candidate with ID '{req.candidate_id}' does not exist.",
            },
        )

    # 2. Check for duplicate active invitations within cooldown window (5 minutes)
    recent_active_stmt = (
        select(CandidateInvitationRecord)
        .where(
            CandidateInvitationRecord.candidate_id == req.candidate_id,
            CandidateInvitationRecord.organization_id == org_id,
            CandidateInvitationRecord.consumed_at.is_(None),
            CandidateInvitationRecord.revoked_at.is_(None),
            CandidateInvitationRecord.expires_at > now,
        )
        .order_by(CandidateInvitationRecord.created_at.desc())
    )
    existing_active = db.execute(recent_active_stmt).scalars().all()

    for inv in existing_active:
        created_at_utc = inv.created_at
        if created_at_utc.tzinfo is None:
            created_at_utc = created_at_utc.replace(tzinfo=timezone.utc)
        if (now - created_at_utc).total_seconds() < 300:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "INVITATION_COOLDOWN_ACTIVE",
                    "message": "An active invitation was recently generated for this candidate. Please wait 5 minutes or revoke the existing invitation.",
                },
            )
        else:
            # Revoke older unconsumed active invitation to ensure single active token
            inv.revoked_at = now
            _record_audit_event(
                db=db,
                event_type=AuditEventType.CANDIDATE_INVITATION_REVOKED,
                result=AuditResult.SUCCESS,
                reason_code=AuditReasonCode.INVITATION_REVOKED,
                actor_user_id=recruiter_user.user_id,
                resource_type="candidate_invitation",
                resource_id=str(inv.invitation_id),
                safe_metadata={"auto_revoked_for_new_invitation": True},
            )

    # 3. Generate secure random token
    raw_token = generate_secure_token()
    token_digest = hash_token(raw_token)
    expires_at = now + timedelta(hours=req.valid_hours)

    invitation = CandidateInvitationRecord(
        invitation_id=uuid.uuid4(),
        organization_id=org_id,
        candidate_id=req.candidate_id,
        application_id=req.application_id,
        token_hash=token_digest,
        purpose=req.purpose,
        expires_at=expires_at,
        created_by_user_id=recruiter_user.user_id,
        created_at=now,
    )
    db.add(invitation)

    _record_audit_event(
        db=db,
        event_type=AuditEventType.CANDIDATE_INVITATION_CREATED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.INVITATION_CREATED,
        actor_user_id=recruiter_user.user_id,
        resource_type="candidate_invitation",
        resource_id=str(invitation.invitation_id),
        safe_metadata={
            "candidate_id": str(req.candidate_id),
            "organization_id": str(org_id),
            "valid_hours": req.valid_hours,
        },
    )

    db.commit()
    db.refresh(invitation)

    frontend_base = settings.frontend_origin.rstrip("/")
    activation_url = f"{frontend_base}/candidate/activate#token={raw_token}"

    return invitation, raw_token, activation_url


def revoke_candidate_invitation(
    db: Session,
    recruiter_user: UserRecord,
    invitation_id: uuid.UUID,
) -> CandidateInvitationRecord:
    """
    Revokes an unconsumed candidate invitation.
    """
    now = datetime.now(timezone.utc)
    stmt = select(CandidateInvitationRecord).where(
        CandidateInvitationRecord.invitation_id == invitation_id
    )
    invitation = db.execute(stmt).scalar_one_or_none()
    if invitation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "INVITATION_NOT_FOUND",
                "message": f"Invitation with ID '{invitation_id}' does not exist.",
            },
        )

    if invitation.consumed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVITATION_ALREADY_CONSUMED",
                "message": "Cannot revoke an invitation that has already been consumed.",
            },
        )

    if invitation.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVITATION_ALREADY_REVOKED",
                "message": "Invitation is already revoked.",
            },
        )

    invitation.revoked_at = now
    _record_audit_event(
        db=db,
        event_type=AuditEventType.CANDIDATE_INVITATION_REVOKED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.INVITATION_REVOKED_BY_RECRUITER,
        actor_user_id=recruiter_user.user_id,
        resource_type="candidate_invitation",
        resource_id=str(invitation.invitation_id),
        safe_metadata={
            "candidate_id": str(invitation.candidate_id),
        },
    )

    db.commit()
    db.refresh(invitation)
    return invitation


def revoke_candidate_access(
    db: Session,
    recruiter_user: UserRecord,
    candidate_id: uuid.UUID,
) -> CandidateAccessStatusResponse:
    """
    Revokes and suspends all portal access for a candidate:
    - Suspends active candidate account bindings
    - Deactivates candidate UserRecord and revokes all active sessions
    - Revokes pending unconsumed invitations
    - Emits audit events and returns updated CandidateAccessStatusResponse
    """
    now = datetime.now(timezone.utc)

    # 1. Verify candidate identity exists
    cand_stmt = select(CandidateIdentityRecord).where(
        CandidateIdentityRecord.identity_id == candidate_id
    )
    candidate = db.execute(cand_stmt).scalar_one_or_none()
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "CANDIDATE_NOT_FOUND",
                "message": f"Candidate with ID '{candidate_id}' does not exist.",
            },
        )

    # 2. Find and suspend active bindings and deactivate candidate user
    binding_stmt = (
        select(CandidateAccountBindingRecord)
        .options(selectinload(CandidateAccountBindingRecord.user))
        .where(
            CandidateAccountBindingRecord.candidate_id == candidate_id,
            CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
        )
    )
    bindings = db.execute(binding_stmt).scalars().all()
    for b in bindings:
        b.status = CandidateBindingStatus.SUSPENDED.value
        b.updated_at = now
        if b.user:
            b.user.is_active = False
            b.user.updated_at = now
            # Revoke all active sessions
            db.execute(
                update(UserSessionRecord)
                .where(
                    UserSessionRecord.user_id == b.user_id,
                    UserSessionRecord.revoked_at.is_(None),
                )
                .values(
                    revoked_at=now,
                    revoke_reason=SessionRevokeReason.ADMIN_REVOKED.value,
                )
            )

    # 3. Revoke any pending invitations
    inv_stmt = (
        select(CandidateInvitationRecord)
        .where(
            CandidateInvitationRecord.candidate_id == candidate_id,
            CandidateInvitationRecord.consumed_at.is_(None),
            CandidateInvitationRecord.revoked_at.is_(None),
        )
    )
    invitations = db.execute(inv_stmt).scalars().all()
    for inv in invitations:
        inv.revoked_at = now

    _record_audit_event(
        db=db,
        event_type=AuditEventType.CANDIDATE_BINDING_UPDATED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.BINDING_SUSPENDED,
        actor_user_id=recruiter_user.user_id,
        resource_type="candidate_access",
        resource_id=str(candidate_id),
        safe_metadata={
            "candidate_id": str(candidate_id),
            "revoked_by_user_id": str(recruiter_user.user_id),
        },
    )

    db.commit()
    return get_candidate_access_status(
        db=db,
        candidate_id=candidate_id,
        recruiter_user=recruiter_user,
    )


def list_candidate_invitations_summary(
    db: Session,
    candidate_id: uuid.UUID,
) -> List[CandidateInvitationSummary]:
    """
    Lists invitation records for a candidate with safe metadata and status.
    Never exposes raw tokens or token hashes.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(CandidateInvitationRecord)
        .where(CandidateInvitationRecord.candidate_id == candidate_id)
        .order_by(CandidateInvitationRecord.created_at.desc())
    )
    invitations = db.execute(stmt).scalars().all()

    results = []
    for inv in invitations:
        exp_utc = inv.expires_at
        if exp_utc.tzinfo is None:
            exp_utc = exp_utc.replace(tzinfo=timezone.utc)

        if inv.revoked_at is not None:
            status_val = "REVOKED"
        elif inv.consumed_at is not None:
            status_val = "CONSUMED"
        elif now >= exp_utc:
            status_val = "EXPIRED"
        else:
            status_val = "ACTIVE"

        results.append(
            CandidateInvitationSummary(
                invitation_id=inv.invitation_id,
                candidate_id=inv.candidate_id,
                organization_id=inv.organization_id,
                purpose=inv.purpose,
                status=status_val,
                expires_at=inv.expires_at,
                consumed_at=inv.consumed_at,
                revoked_at=inv.revoked_at,
                created_by_user_id=inv.created_by_user_id,
                created_at=inv.created_at,
            )
        )
    return results


def get_candidate_access_status(
    db: Session,
    candidate_id: uuid.UUID,
    recruiter_user: Optional[UserRecord] = None,
) -> CandidateAccessStatusResponse:
    """
    Returns strict recruiter-safe account-binding and invitation status for a candidate.
    An accessible candidate without an account or invitation returns 200 with NOT_INVITED.
    """
    now = datetime.now(timezone.utc)

    # 1. Verify candidate identity exists
    cand_stmt = select(CandidateIdentityRecord).where(
        CandidateIdentityRecord.identity_id == candidate_id
    )
    candidate = db.execute(cand_stmt).scalar_one_or_none()
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "CANDIDATE_NOT_FOUND",
                "message": f"Candidate with ID '{candidate_id}' does not exist.",
            },
        )

    # 2. Check for account binding (ACTIVE or SUSPENDED)
    binding_stmt = (
        select(CandidateAccountBindingRecord)
        .options(selectinload(CandidateAccountBindingRecord.user))
        .where(
            CandidateAccountBindingRecord.candidate_id == candidate_id,
        )
        .order_by(CandidateAccountBindingRecord.updated_at.desc())
    )
    binding = db.execute(binding_stmt).scalars().first()

    has_active_account = False
    account_status = "NOT_INVITED"
    email = None
    display_name = candidate.full_name or "Candidate"

    if binding and binding.user:
        user = binding.user
        email = user.email
        display_name = user.display_name or candidate.full_name or "Candidate"
        if binding.status == CandidateBindingStatus.ACTIVE.value and user.is_active:
            has_active_account = True
            account_status = "ACTIVE"
        else:
            has_active_account = False
            account_status = "SUSPENDED"

    # 3. Check for latest invitation
    inv_stmt = (
        select(CandidateInvitationRecord)
        .where(CandidateInvitationRecord.candidate_id == candidate_id)
        .order_by(CandidateInvitationRecord.created_at.desc())
    )
    latest_inv = db.execute(inv_stmt).scalars().first()

    inv_info: Optional[CandidateAccessInvitationInfo] = None
    if latest_inv:
        exp_utc = latest_inv.expires_at
        if exp_utc.tzinfo is None:
            exp_utc = exp_utc.replace(tzinfo=timezone.utc)

        if latest_inv.revoked_at is not None:
            inv_status = "REVOKED"
        elif latest_inv.consumed_at is not None:
            inv_status = "CONSUMED"
        elif now >= exp_utc:
            inv_status = "EXPIRED"
        else:
            inv_status = "ACTIVE"

        inv_info = CandidateAccessInvitationInfo(
            invitation_id=latest_inv.invitation_id,
            status=inv_status,
            expires_at=latest_inv.expires_at,
            created_at=latest_inv.created_at,
        )

        if not has_active_account and account_status != "SUSPENDED":
            if inv_status == "ACTIVE":
                account_status = "INVITED"
            else:
                account_status = "NOT_INVITED"

    return CandidateAccessStatusResponse(
        candidate_id=candidate_id,
        account_status=account_status,
        has_active_account=has_active_account,
        email=email,
        display_name=display_name,
        invitation=inv_info,
    )


def activate_candidate_account(
    db: Session,
    req: CandidateActivationRequest,
    settings: Settings,
) -> Tuple[UserRecord, CandidateAccountBindingRecord, UserSessionRecord, str, str]:
    """
    Atomically consumes a valid invitation, provisions/updates Candidate account,
    creates active Candidate binding, rotates/establishes Candidate session,
    and returns user, binding, session, raw_session_token, and raw_csrf_token.
    """
    now = datetime.now(timezone.utc)
    token_digest = hash_token(req.token)

    # 1. Lookup invitation with row lock if possible
    # We use select for update on Postgres to prevent race conditions on double activation
    bind = db.get_bind()
    stmt = select(CandidateInvitationRecord).where(
        CandidateInvitationRecord.token_hash == token_digest
    )
    if bind.engine.name == "postgresql":
        stmt = stmt.with_for_update()

    invitation = db.execute(stmt).scalar_one_or_none()

    generic_inv_error = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "INVALID_OR_EXPIRED_INVITATION",
            "message": "The invitation link is invalid, expired, or has already been used.",
        },
    )

    if invitation is None:
        _record_audit_event(
            db=db,
            event_type=AuditEventType.CANDIDATE_ACTIVATED,
            result=AuditResult.FAILURE,
            reason_code=AuditReasonCode.INVITATION_INVALID,
            safe_metadata={"error": "TOKEN_NOT_FOUND"},
        )
        db.commit()
        raise generic_inv_error

    if invitation.revoked_at is not None:
        _record_audit_event(
            db=db,
            event_type=AuditEventType.CANDIDATE_ACTIVATED,
            result=AuditResult.FAILURE,
            reason_code=AuditReasonCode.INVITATION_REVOKED,
            resource_type="candidate_invitation",
            resource_id=str(invitation.invitation_id),
            safe_metadata={"error": "INVITATION_REVOKED"},
        )
        db.commit()
        raise generic_inv_error

    if invitation.consumed_at is not None:
        _record_audit_event(
            db=db,
            event_type=AuditEventType.CANDIDATE_ACTIVATED,
            result=AuditResult.FAILURE,
            reason_code=AuditReasonCode.INVITATION_CONSUMED,
            resource_type="candidate_invitation",
            resource_id=str(invitation.invitation_id),
            safe_metadata={"error": "INVITATION_ALREADY_CONSUMED"},
        )
        db.commit()
        raise generic_inv_error

    exp_utc = invitation.expires_at
    if exp_utc.tzinfo is None:
        exp_utc = exp_utc.replace(tzinfo=timezone.utc)

    if now >= exp_utc:
        _record_audit_event(
            db=db,
            event_type=AuditEventType.CANDIDATE_ACTIVATED,
            result=AuditResult.FAILURE,
            reason_code=AuditReasonCode.INVITATION_EXPIRED,
            resource_type="candidate_invitation",
            resource_id=str(invitation.invitation_id),
            safe_metadata={"error": "INVITATION_EXPIRED"},
        )
        db.commit()
        raise generic_inv_error

    # 2. Lookup candidate identity
    cand_stmt = select(CandidateIdentityRecord).where(
        CandidateIdentityRecord.identity_id == invitation.candidate_id
    )
    candidate = db.execute(cand_stmt).scalar_one_or_none()
    if candidate is None:
        raise generic_inv_error

    # Derive candidate email and display name
    raw_email = candidate.primary_email
    if raw_email:
        try:
            cand_email = canonicalize_email(raw_email)
        except Exception:
            cand_email = f"candidate-{invitation.candidate_id}@candidate.portal"
    else:
        cand_email = f"candidate-{invitation.candidate_id}@candidate.portal"

    display_name = candidate.full_name or "Candidate"

    # 3. Hash password using existing project scrypt implementation
    pw_hash = hash_password(
        plaintext_password=req.password,
        n=settings.scrypt_n,
        r=settings.scrypt_r,
        p=settings.scrypt_p,
        dklen=settings.scrypt_dklen,
    )

    # 4. Check if a user already exists for this email
    user_stmt = select(UserRecord).where(UserRecord.email == cand_email)
    user = db.execute(user_stmt).scalar_one_or_none()

    if user is None:
        user = UserRecord(
            user_id=uuid.uuid4(),
            email=cand_email,
            display_name=display_name,
            password_hash=pw_hash,
            role=UserRole.CANDIDATE,
            is_active=True,
            must_change_password=False,
            failed_login_count=0,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        db.flush()
    else:
        # Existing user account update
        user.password_hash = pw_hash
        user.role = UserRole.CANDIDATE
        user.is_active = True
        user.must_change_password = False
        user.failed_login_count = 0
        user.locked_until = None
        user.updated_at = now
        db.flush()

    # 5. Mark invitation as consumed
    invitation.consumed_at = now
    invitation.resulting_user_id = user.user_id

    # 6. Create or update CandidateAccountBindingRecord
    binding_stmt = select(CandidateAccountBindingRecord).where(
        CandidateAccountBindingRecord.user_id == user.user_id
    )
    binding = db.execute(binding_stmt).scalar_one_or_none()

    if binding is None:
        binding = CandidateAccountBindingRecord(
            binding_id=uuid.uuid4(),
            user_id=user.user_id,
            candidate_id=invitation.candidate_id,
            organization_id=invitation.organization_id,
            invitation_id=invitation.invitation_id,
            status=CandidateBindingStatus.ACTIVE.value,
            created_at=now,
            updated_at=now,
        )
        db.add(binding)
    else:
        binding.candidate_id = invitation.candidate_id
        binding.organization_id = invitation.organization_id
        binding.invitation_id = invitation.invitation_id
        binding.status = CandidateBindingStatus.ACTIVE.value
        binding.updated_at = now

    # 7. Create fresh server session
    from app.api.auth import _create_db_session_with_collision_handling
    user_session, raw_session_token, raw_csrf_token = _create_db_session_with_collision_handling(
        db=db,
        user_id=user.user_id,
        settings=settings,
        now=now,
    )

    _record_audit_event(
        db=db,
        event_type=AuditEventType.CANDIDATE_ACTIVATED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.CANDIDATE_ACCOUNT_ACTIVATED,
        actor_user_id=user.user_id,
        resource_type="candidate_account",
        resource_id=str(binding.binding_id),
        safe_metadata={
            "candidate_id": str(invitation.candidate_id),
            "organization_id": str(invitation.organization_id),
            "user_id": str(user.user_id),
        },
    )

    db.commit()
    db.refresh(user)
    db.refresh(binding)
    db.refresh(user_session)

    return user, binding, user_session, raw_session_token, raw_csrf_token


def register_candidate_account(
    db: Session,
    req: CandidateRegisterRequest,
    settings: Settings,
) -> Tuple[UserRecord, UserSessionRecord, str, str]:
    """
    Public Candidate Self-Registration.
    - Strictly assigns Candidate role server-side.
    - Normalizes and checks for email collisions.
    - Hashes password using existing scrypt implementation.
    - Creates UserRecord without requiring candidate identity/profile/binding.
    - Establishes fresh candidate session and returns (user, session, raw_session_token, raw_csrf_token).
    """
    now = datetime.now(timezone.utc)
    canonical_email = canonicalize_email(req.email)

    # Check if user already exists with this email
    stmt = select(UserRecord).where(UserRecord.email == canonical_email)
    existing_user = db.execute(stmt).scalar_one_or_none()

    if existing_user is not None:
        _record_audit_event(
            db=db,
            event_type=AuditEventType.CANDIDATE_REGISTERED,
            result=AuditResult.FAILURE,
            reason_code=AuditReasonCode.DUPLICATE_APPLICATION,
            actor_user_id=None,
            resource_type="user",
            safe_metadata={"attempted_email": canonical_email, "conflict": "EMAIL_EXISTS"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "EMAIL_ALREADY_REGISTERED",
                "message": "An account with this email address already exists. Please sign in instead.",
            },
        )

    # Hash password using scrypt
    pw_hash = hash_password(
        plaintext_password=req.password,
        n=settings.scrypt_n,
        r=settings.scrypt_r,
        p=settings.scrypt_p,
        dklen=settings.scrypt_dklen,
    )

    # Create candidate user record
    new_user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonical_email,
        display_name=req.full_name.strip(),
        password_hash=pw_hash,
        role=UserRole.CANDIDATE,
        is_active=True,
        must_change_password=False,
        failed_login_count=0,
        locked_until=None,
        last_login_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(new_user)
    db.flush()

    # Create fresh server session
    from app.api.auth import _create_db_session_with_collision_handling
    user_session, raw_session_token, raw_csrf_token = _create_db_session_with_collision_handling(
        db=db,
        user_id=new_user.user_id,
        settings=settings,
        now=now,
    )

    _record_audit_event(
        db=db,
        event_type=AuditEventType.CANDIDATE_REGISTERED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.CANDIDATE_ACCOUNT_REGISTERED,
        actor_user_id=new_user.user_id,
        resource_type="user",
        resource_id=str(new_user.user_id),
        safe_metadata={
            "email": canonical_email,
            "role": UserRole.CANDIDATE.value,
            "portal": "CANDIDATE",
        },
    )

    db.commit()
    db.refresh(new_user)
    db.refresh(user_session)

    return new_user, user_session, raw_session_token, raw_csrf_token

