import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import (
    AuthenticatedContext,
    _record_audit_event,
    require_admin,
    verify_csrf_and_origin,
)
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
from app.schemas.auth import (
    AdminCandidateResponse,
    AdminCandidateUpdateRequest,
    AdminPasswordResetRequest,
    AdminUserCreateRequest,
    AdminUserUpdateRequest,
    PaginatedSecurityAuditEventsResponse,
    SecurityAuditEventResponse,
    UserResponse,
)
from app.schemas.provider_admin import (
    ActivateProviderProfileRequest,
    AdminProviderProfileResponse,
    ArchiveProviderProfileRequest,
    CreateProviderProfileRequest,
    DisableProviderProfileRequest,
    ProviderCompatibilityReportResponse,
    SelectProviderModelRequest,
    TestProviderCompatibilityRequest,
    UpdateProviderProfileRequest,
)
from app.services.provider_registry import (
    InactiveProfileUpdateError,
    IneligibleProviderError,
    ProviderRegistry,
    RemoteEgressConfirmationRequiredError,
    StaleVersionError,
    get_provider_registry,
)
from app.services.security import hash_password

logger = logging.getLogger("ai_hiring.api.admin")

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
)


@router.get(
    "/users",
    response_model=list[UserResponse],
    status_code=status.HTTP_200_OK,
    summary="List Internal Users",
    description="Returns internal accounts (Admin, Recruiter, Reviewer) for Administrator management, ordered by created_at descending.",
)
async def list_users(
    include_candidates: bool = Query(False, description="Whether to include CANDIDATE accounts"),
    auth_ctx: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[UserResponse]:
    stmt = select(UserRecord)
    if not include_candidates:
        stmt = stmt.where(UserRecord.role != UserRole.CANDIDATE)
    stmt = stmt.order_by(UserRecord.created_at.desc())
    users = db.execute(stmt).scalars().all()
    return [
        UserResponse(
            user_id=u.user_id,
            email=u.email,
            display_name=u.display_name,
            role=u.role,
            is_active=u.is_active,
            must_change_password=u.must_change_password,
            locked_until=u.locked_until,
            last_login_at=u.last_login_at,
            created_at=u.created_at,
            updated_at=u.updated_at,
        )
        for u in users
    ]



@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Internal User",
    description=(
        "Creates a new internal ADMIN, RECRUITER, or REVIEWER account with a temporary password and must_change_password=true. "
        "Requires Admin role, CSRF header, and valid Origin."
    ),
)
async def create_user(
    create_req: AdminUserCreateRequest,
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    admin_auth: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserResponse:
    now = datetime.now(timezone.utc)

    # Check for existing email
    stmt = select(UserRecord).where(UserRecord.email == create_req.email)
    if db.execute(stmt).scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "USER_ALREADY_EXISTS",
                "message": f"A user with email '{create_req.email}' already exists.",
            },
        )

    # Hash temporary password with versioned scrypt
    encoded_hash = hash_password(
        plaintext_password=create_req.temporary_password,
        n=settings.scrypt_n,
        r=settings.scrypt_r,
        p=settings.scrypt_p,
        dklen=settings.scrypt_dklen,
    )

    new_user = UserRecord(
        user_id=uuid.uuid4(),
        email=create_req.email,
        display_name=create_req.display_name,
        password_hash=encoded_hash,
        role=create_req.role,
        is_active=True,
        must_change_password=True,
        failed_login_count=0,
    )
    db.add(new_user)

    _record_audit_event(
        db=db,
        event_type=AuditEventType.USER_BOOTSTRAPPED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.SYSTEM_BOOTSTRAP,
        actor_user_id=admin_auth.user.user_id,
        resource_type="user",
        resource_id=str(new_user.user_id),
        safe_metadata={
            "created_email": new_user.email,
            "created_role": new_user.role.value,
        },
    )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "USER_ALREADY_EXISTS",
                "message": f"A user with email '{create_req.email}' already exists.",
            },
        )

    return UserResponse(
        user_id=new_user.user_id,
        email=new_user.email,
        display_name=new_user.display_name,
        role=new_user.role,
        is_active=new_user.is_active,
        must_change_password=new_user.must_change_password,
        locked_until=new_user.locked_until,
        last_login_at=new_user.last_login_at,
        created_at=new_user.created_at,
        updated_at=new_user.updated_at,
    )


@router.patch(
    "/users/{user_id}",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Update User Account",
    description="Updates display name, role, or active status. If role or active state changes, all active sessions for the target user are revoked immediately.",
)
async def update_user(
    user_id: uuid.UUID,
    update_req: AdminUserUpdateRequest,
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    admin_auth: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserResponse:
    now = datetime.now(timezone.utc)

    stmt = select(UserRecord).where(UserRecord.user_id == user_id)
    target_user = db.execute(stmt).scalar_one_or_none()

    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "User not found."},
        )

    # Prevent demoting or deactivating the last active Admin
    is_demoting_or_deactivating_admin = (
        target_user.role == UserRole.ADMIN
        and target_user.is_active
        and (
            (update_req.role is not None and update_req.role != UserRole.ADMIN)
            or (update_req.is_active is False)
        )
    )

    if is_demoting_or_deactivating_admin:
        # Check active admin count
        count_stmt = select(func.count()).select_from(UserRecord).where(
            UserRecord.role == UserRole.ADMIN,
            UserRecord.is_active.is_(True),
        )
        active_admin_count = db.execute(count_stmt).scalar_one()
        if active_admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "CANNOT_DEMOTE_LAST_ADMIN",
                    "message": "Cannot deactivate or demote the final active Administrator.",
                },
            )

    role_changed = update_req.role is not None and update_req.role != target_user.role
    active_changed = update_req.is_active is not None and update_req.is_active != target_user.is_active

    if update_req.display_name is not None:
        target_user.display_name = update_req.display_name

    if role_changed:
        old_role = target_user.role
        target_user.role = update_req.role
        _record_audit_event(
            db=db,
            event_type=AuditEventType.SESSION_REVOKED,
            result=AuditResult.SUCCESS,
            reason_code=AuditReasonCode.SYSTEM_BOOTSTRAP,
            actor_user_id=admin_auth.user.user_id,
            resource_type="user",
            resource_id=str(target_user.user_id),
            safe_metadata={
                "action": "ROLE_CHANGED",
                "old_role": old_role.value,
                "new_role": update_req.role.value,
            },
        )

    if active_changed:
        target_user.is_active = update_req.is_active
        _record_audit_event(
            db=db,
            event_type=AuditEventType.ACCOUNT_LOCKED if not update_req.is_active else AuditEventType.ACCOUNT_UNLOCKED,
            result=AuditResult.SUCCESS,
            reason_code=AuditReasonCode.ACCOUNT_LOCKED if not update_req.is_active else AuditReasonCode.SUCCESSFUL_AUTHENTICATION,
            actor_user_id=admin_auth.user.user_id,
            resource_type="user",
            resource_id=str(target_user.user_id),
            safe_metadata={"is_active": update_req.is_active},
        )

    # If role or active status changed, revoke all active sessions for this target user
    if role_changed or active_changed:
        revoke_reason = SessionRevokeReason.ADMIN_REVOKED
        revoke_stmt = (
            update(UserSessionRecord)
            .where(
                UserSessionRecord.user_id == target_user.user_id,
                UserSessionRecord.revoked_at.is_(None),
            )
            .values(
                revoked_at=now,
                revoke_reason=revoke_reason,
            )
        )
        db.execute(revoke_stmt)

    db.commit()
    db.refresh(target_user)

    return UserResponse(
        user_id=target_user.user_id,
        email=target_user.email,
        display_name=target_user.display_name,
        role=target_user.role,
        is_active=target_user.is_active,
        must_change_password=target_user.must_change_password,
        locked_until=target_user.locked_until,
        last_login_at=target_user.last_login_at,
        created_at=target_user.created_at,
        updated_at=target_user.updated_at,
    )


@router.post(
    "/users/{user_id}/reset-password",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Admin Reset Temporary Password",
    description="Resets a target user's password to a temporary password, enforces must_change_password=true, clears cooldowns, and revokes all active sessions.",
)
async def reset_password(
    user_id: uuid.UUID,
    reset_req: AdminPasswordResetRequest,
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    admin_auth: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserResponse:
    now = datetime.now(timezone.utc)

    stmt = select(UserRecord).where(UserRecord.user_id == user_id)
    target_user = db.execute(stmt).scalar_one_or_none()

    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "User not found."},
        )

    # Hash new temporary password
    new_hash = hash_password(
        plaintext_password=reset_req.temporary_password,
        n=settings.scrypt_n,
        r=settings.scrypt_r,
        p=settings.scrypt_p,
        dklen=settings.scrypt_dklen,
    )

    target_user.password_hash = new_hash
    target_user.must_change_password = True
    target_user.failed_login_count = 0
    target_user.locked_until = None

    # Revoke all active sessions for the target user
    revoke_stmt = (
        update(UserSessionRecord)
        .where(
            UserSessionRecord.user_id == target_user.user_id,
            UserSessionRecord.revoked_at.is_(None),
        )
        .values(
            revoked_at=now,
            revoke_reason=SessionRevokeReason.PASSWORD_CHANGED,
        )
    )
    db.execute(revoke_stmt)

    _record_audit_event(
        db=db,
        event_type=AuditEventType.PASSWORD_CHANGED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.SYSTEM_BOOTSTRAP,
        actor_user_id=admin_auth.user.user_id,
        resource_type="user",
        resource_id=str(target_user.user_id),
        safe_metadata={"action": "ADMIN_PASSWORD_RESET"},
    )

    db.commit()
    db.refresh(target_user)

    return UserResponse(
        user_id=target_user.user_id,
        email=target_user.email,
        display_name=target_user.display_name,
        role=target_user.role,
        is_active=target_user.is_active,
        must_change_password=target_user.must_change_password,
        locked_until=target_user.locked_until,
        last_login_at=target_user.last_login_at,
        created_at=target_user.created_at,
        updated_at=target_user.updated_at,
    )


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete User Account",
    description="Permanently deletes an internal staff or candidate user account and all associated data.",
)
async def delete_user(
    user_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    admin_auth: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    from sqlalchemy import delete

    if admin_auth.user.user_id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CANNOT_DELETE_SELF", "message": "Cannot delete your own active administrator account."},
        )

    stmt = select(UserRecord).where(UserRecord.user_id == user_id)
    target_user = db.execute(stmt).scalar_one_or_none()

    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "User not found."},
        )

    # Prevent deleting the last active Administrator
    if target_user.role == UserRole.ADMIN and target_user.is_active:
        count_stmt = select(func.count()).select_from(UserRecord).where(
            UserRecord.role == UserRole.ADMIN,
            UserRecord.is_active.is_(True),
        )
        active_admin_count = db.execute(count_stmt).scalar_one()
        if active_admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "CANNOT_DELETE_LAST_ADMIN",
                    "message": "Cannot delete the final active Administrator.",
                },
            )

    # Drop candidate records if candidate or linked to candidate identities
    summary = _drop_candidate_records(
        db=db,
        target_user_id=user_id,
        target_email=target_user.email,
        settings=settings,
    )

    # Delete sessions and user record
    db.execute(delete(UserSessionRecord).where(UserSessionRecord.user_id == user_id))
    db.execute(delete(UserRecord).where(UserRecord.user_id == user_id))

    _record_audit_event(
        db=db,
        event_type=AuditEventType.SESSION_REVOKED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.SYSTEM_BOOTSTRAP,
        actor_user_id=admin_auth.user.user_id,
        resource_type="user",
        resource_id=str(user_id),
        safe_metadata={
            "action": "USER_DELETED",
            "deleted_email": target_user.email,
            "deleted_role": target_user.role.value,
        },
    )

    db.commit()

    return {
        "message": f"User '{target_user.display_name}' deleted successfully.",
        "user_id": str(user_id),
    }



def _parse_optional_uuid(val: Any) -> Optional[uuid.UUID]:
    if not val:
        return None
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError, AttributeError):
        return None


def _drop_candidate_records(
    db: Session,
    target_identity_id: Optional[uuid.UUID] = None,
    target_user_id: Optional[uuid.UUID] = None,
    target_artifact_id: Optional[uuid.UUID] = None,
    target_email: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> dict:
    from sqlalchemy import delete, or_
    from pathlib import Path
    from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
    from app.models.candidate_access import (
        CandidateAccountBindingRecord,
        CandidateApplicationRecord,
        CandidateInterviewAnswerRecord,
        CandidateInterviewEvaluationRecord,
        CandidateInterviewSessionRecord,
        CandidateInvitationRecord,
        CandidatePracticeAnswerReviewRecord,
        CandidatePracticeAnswerSegmentRecord,
        CandidatePracticeMcqAnswerRecord,
        CandidatePracticeMessageRecord,
        CandidatePracticeReleaseRecord,
        CandidatePracticeReportRecord,
        CandidatePracticeRequestRecord,
        CandidatePracticeSessionRecord,
        CandidateQuestionEvaluationRecord,
    )
    from app.models.interview import CandidateInterviewPlanRecord, SimulationAnswerSegmentRecord
    from app.models.matching import MatchResultRecord
    from app.models.gap_report import GapReportRecord
    from app.models.ats import ATSObjectMappingRecord, ATSExportReceiptRecord
    from app.models.resume import ResumeArtifact, ProcessingJob
    from app.models.extraction import ResumeExtraction, ExtractionBlock

    identity_ids = set()
    if target_identity_id:
        identity_ids.add(target_identity_id)
    artifact_ids = set()
    if target_artifact_id:
        artifact_ids.add(target_artifact_id)
    user_ids = set()
    if target_user_id:
        user_ids.add(target_user_id)

    # 1. If target_email is provided, search identities and candidate users
    if target_email:
        ident_by_email = db.execute(
            select(CandidateIdentityRecord).where(func.lower(CandidateIdentityRecord.primary_email) == target_email.lower().strip())
        ).scalars().all()
        for i in ident_by_email:
            identity_ids.add(i.identity_id)
            if i.artifact_id:
                artifact_ids.add(i.artifact_id)

        user_by_email = db.execute(
            select(UserRecord).where(
                func.lower(UserRecord.email) == target_email.lower().strip(),
                UserRecord.role == UserRole.CANDIDATE,
            )
        ).scalars().all()
        for u in user_by_email:
            user_ids.add(u.user_id)

    # 2. Expand from target_user_id
    if user_ids:
        bindings = db.execute(
            select(CandidateAccountBindingRecord).where(CandidateAccountBindingRecord.user_id.in_(list(user_ids)))
        ).scalars().all()
        for b in bindings:
            if b.candidate_id:
                identity_ids.add(b.candidate_id)

        invites = db.execute(
            select(CandidateInvitationRecord).where(CandidateInvitationRecord.resulting_user_id.in_(list(user_ids)))
        ).scalars().all()
        for inv in invites:
            if inv.candidate_id:
                identity_ids.add(inv.candidate_id)

    # 3. Expand from identity_ids
    if identity_ids:
        identities = db.execute(
            select(CandidateIdentityRecord).where(CandidateIdentityRecord.identity_id.in_(list(identity_ids)))
        ).scalars().all()
        for ident in identities:
            if ident.artifact_id:
                artifact_ids.add(ident.artifact_id)
            if ident.primary_email:
                u = db.execute(
                    select(UserRecord).where(
                        func.lower(UserRecord.email) == ident.primary_email.lower().strip(),
                        UserRecord.role == UserRole.CANDIDATE,
                    )
                ).scalar_one_or_none()
                if u:
                    user_ids.add(u.user_id)

        bindings = db.execute(
            select(CandidateAccountBindingRecord).where(CandidateAccountBindingRecord.candidate_id.in_(list(identity_ids)))
        ).scalars().all()
        for b in bindings:
            if b.user_id:
                user_ids.add(b.user_id)

    # 4. Expand from artifact_ids
    if artifact_ids:
        idents = db.execute(
            select(CandidateIdentityRecord).where(CandidateIdentityRecord.artifact_id.in_(list(artifact_ids)))
        ).scalars().all()
        for i in idents:
            identity_ids.add(i.identity_id)

    # Find all profile_ids
    profile_ids = set()
    if artifact_ids or identity_ids:
        conds = []
        if artifact_ids:
            conds.append(CandidateProfileRecord.artifact_id.in_(list(artifact_ids)))
        if identity_ids:
            conds.append(CandidateProfileRecord.identity_id.in_(list(identity_ids)))
        if conds:
            profs = db.execute(select(CandidateProfileRecord).where(or_(*conds))).scalars().all()
            for p in profs:
                profile_ids.add(p.profile_id)

    # Find all practice session IDs and interview session IDs
    practice_session_ids = set()
    if identity_ids:
        p_sess = db.execute(
            select(CandidatePracticeSessionRecord).where(CandidatePracticeSessionRecord.candidate_id.in_(list(identity_ids)))
        ).scalars().all()
        for s in p_sess:
            practice_session_ids.add(s.session_id)

    interview_session_ids = set()
    if identity_ids:
        i_sess = db.execute(
            select(CandidateInterviewSessionRecord).where(CandidateInterviewSessionRecord.candidate_id.in_(list(identity_ids)))
        ).scalars().all()
        for s in i_sess:
            interview_session_ids.add(s.session_id)

    # Deletions in safe dependency order:
    # 1. Practice records
    if practice_session_ids:
        db.execute(delete(CandidatePracticeAnswerReviewRecord).where(CandidatePracticeAnswerReviewRecord.session_id.in_(list(practice_session_ids))))
        db.execute(delete(CandidatePracticeAnswerSegmentRecord).where(CandidatePracticeAnswerSegmentRecord.session_id.in_(list(practice_session_ids))))
        db.execute(delete(CandidatePracticeMcqAnswerRecord).where(CandidatePracticeMcqAnswerRecord.session_id.in_(list(practice_session_ids))))
        db.execute(delete(CandidatePracticeMessageRecord).where(CandidatePracticeMessageRecord.session_id.in_(list(practice_session_ids))))
        db.execute(delete(CandidatePracticeReportRecord).where(CandidatePracticeReportRecord.session_id.in_(list(practice_session_ids))))
        db.execute(delete(CandidatePracticeSessionRecord).where(CandidatePracticeSessionRecord.session_id.in_(list(practice_session_ids))))
    if identity_ids:
        db.execute(delete(CandidatePracticeReportRecord).where(CandidatePracticeReportRecord.candidate_id.in_(list(identity_ids))))
        db.execute(delete(CandidatePracticeReleaseRecord).where(CandidatePracticeReleaseRecord.candidate_id.in_(list(identity_ids))))
        db.execute(delete(CandidatePracticeRequestRecord).where(CandidatePracticeRequestRecord.candidate_id.in_(list(identity_ids))))

    # 2. Interview records
    if interview_session_ids:
        db.execute(delete(CandidateQuestionEvaluationRecord).where(CandidateQuestionEvaluationRecord.session_id.in_(list(interview_session_ids))))
        db.execute(delete(CandidateInterviewAnswerRecord).where(CandidateInterviewAnswerRecord.session_id.in_(list(interview_session_ids))))
        db.execute(delete(SimulationAnswerSegmentRecord).where(SimulationAnswerSegmentRecord.session_id.in_(list(interview_session_ids))))
        db.execute(delete(CandidateInterviewEvaluationRecord).where(CandidateInterviewEvaluationRecord.session_id.in_(list(interview_session_ids))))
        db.execute(delete(CandidateInterviewSessionRecord).where(CandidateInterviewSessionRecord.session_id.in_(list(interview_session_ids))))
    if identity_ids:
        db.execute(delete(CandidateInterviewEvaluationRecord).where(CandidateInterviewEvaluationRecord.candidate_id.in_(list(identity_ids))))
        db.execute(delete(CandidateInterviewPlanRecord).where(CandidateInterviewPlanRecord.candidate_id.in_(list(identity_ids))))
        db.execute(delete(CandidateInterviewSessionRecord).where(CandidateInterviewSessionRecord.candidate_id.in_(list(identity_ids))))

    # 3. Match & Gap results
    if profile_ids:
        db.execute(delete(MatchResultRecord).where(MatchResultRecord.profile_id.in_(list(profile_ids))))
        db.execute(delete(GapReportRecord).where(GapReportRecord.profile_id.in_(list(profile_ids))))

    # 4. Applications
    app_conds = []
    if identity_ids:
        app_conds.append(CandidateApplicationRecord.candidate_id.in_(list(identity_ids)))
    if artifact_ids:
        app_conds.append(CandidateApplicationRecord.artifact_id.in_(list(artifact_ids)))
    if user_ids:
        app_conds.append(CandidateApplicationRecord.user_id.in_(list(user_ids)))
    if app_conds:
        db.execute(delete(CandidateApplicationRecord).where(or_(*app_conds)))

    # 5. ATS receipts & mappings
    if identity_ids:
        db.execute(delete(ATSExportReceiptRecord).where(ATSExportReceiptRecord.internal_candidate_id.in_(list(identity_ids))))
        db.execute(delete(ATSObjectMappingRecord).where(ATSObjectMappingRecord.internal_id.in_(list(identity_ids))))


    # 6. Invitations & Account bindings
    if identity_ids:
        db.execute(delete(CandidateInvitationRecord).where(CandidateInvitationRecord.candidate_id.in_(list(identity_ids))))
        db.execute(delete(CandidateAccountBindingRecord).where(CandidateAccountBindingRecord.candidate_id.in_(list(identity_ids))))
    if user_ids:
        db.execute(delete(CandidateInvitationRecord).where(CandidateInvitationRecord.resulting_user_id.in_(list(user_ids))))
        db.execute(delete(CandidateAccountBindingRecord).where(CandidateAccountBindingRecord.user_id.in_(list(user_ids))))

    # 7. Candidate Profiles & Identities
    if profile_ids:
        db.execute(delete(CandidateProfileRecord).where(CandidateProfileRecord.profile_id.in_(list(profile_ids))))
    if identity_ids:
        db.execute(delete(CandidateIdentityRecord).where(CandidateIdentityRecord.identity_id.in_(list(identity_ids))))

    # 8. Resume Artifacts, Extractions, Processing Jobs
    if artifact_ids:
        extractions = db.execute(
            select(ResumeExtraction).where(ResumeExtraction.artifact_id.in_(list(artifact_ids)))
        ).scalars().all()
        ext_ids = [e.extraction_id for e in extractions]
        if ext_ids:
            db.execute(delete(ExtractionBlock).where(ExtractionBlock.extraction_id.in_(ext_ids)))
            db.execute(delete(ResumeExtraction).where(ResumeExtraction.extraction_id.in_(ext_ids)))
        db.execute(delete(ProcessingJob).where(ProcessingJob.artifact_id.in_(list(artifact_ids))))

        # Storage cleanup on disk
        artifacts = db.execute(
            select(ResumeArtifact).where(ResumeArtifact.artifact_id.in_(list(artifact_ids)))
        ).scalars().all()
        if settings:
            storage_dir = Path(settings.storage_dir) if hasattr(settings, "storage_dir") else Path("data/storage")
            quarantine_dir = Path(settings.quarantine_dir) if hasattr(settings, "quarantine_dir") else Path("data/quarantine")
            for art in artifacts:
                if art.quarantine_key:
                    for base in [quarantine_dir, storage_dir]:
                        fpath = base / art.quarantine_key
                        if fpath.exists() and fpath.is_file():
                            try:
                                fpath.unlink()
                            except Exception:
                                pass
        db.execute(delete(ResumeArtifact).where(ResumeArtifact.artifact_id.in_(list(artifact_ids))))

    # 9. User Sessions & User Records (Only for Candidate Users)
    if user_ids:
        db.execute(delete(UserSessionRecord).where(UserSessionRecord.user_id.in_(list(user_ids))))
        db.execute(delete(UserRecord).where(UserRecord.user_id.in_(list(user_ids)), UserRecord.role == UserRole.CANDIDATE))

    return {
        "identities_dropped": len(identity_ids),
        "artifacts_dropped": len(artifact_ids),
        "users_dropped": len(user_ids),
    }


@router.get(
    "/candidates",
    response_model=list[AdminCandidateResponse],
    status_code=status.HTTP_200_OK,
    summary="List Candidates for Administration",
    description="Returns all candidates across identity records, applications, and accounts for Admin management.",
)
async def list_candidates(
    auth_ctx: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AdminCandidateResponse]:
    from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
    from app.models.candidate_access import (
        CandidateAccountBindingRecord,
        CandidateApplicationRecord,
        CandidateInterviewSessionRecord,
        CandidateBindingStatus,
    )

    identities = db.execute(
        select(CandidateIdentityRecord).order_by(CandidateIdentityRecord.created_at.desc())
    ).scalars().all()

    seen_emails = set()
    seen_user_ids = set()
    results: list[AdminCandidateResponse] = []

    for ident in identities:
        email = (ident.primary_email or "").strip().lower()

        # Fetch latest profile
        prof = db.execute(
            select(CandidateProfileRecord)
            .where(CandidateProfileRecord.identity_id == ident.identity_id)
            .order_by(CandidateProfileRecord.profile_version.desc())
        ).scalars().first()

        headline = None
        if prof and prof.professional_profile:
            hl_obj = prof.professional_profile.get("headline")
            if isinstance(hl_obj, dict):
                headline = hl_obj.get("value")
            elif isinstance(hl_obj, str):
                headline = hl_obj

        # Check binding
        binding = db.execute(
            select(CandidateAccountBindingRecord).where(CandidateAccountBindingRecord.candidate_id == ident.identity_id)
        ).scalar_one_or_none()

        user = None
        if binding:
            user = db.execute(
                select(UserRecord).where(UserRecord.user_id == binding.user_id)
            ).scalar_one_or_none()
        elif email:
            user = db.execute(
                select(UserRecord).where(
                    func.lower(UserRecord.email) == email,
                    UserRecord.role == UserRole.CANDIDATE,
                )
            ).scalar_one_or_none()

        if not user and ident.full_name:
            user = db.execute(
                select(UserRecord).where(
                    func.lower(UserRecord.display_name) == ident.full_name.strip().lower(),
                    UserRecord.role == UserRole.CANDIDATE,
                )
            ).scalars().first()

        is_active = True
        has_account = False
        user_id = None

        if user:
            user_id = user.user_id
            seen_user_ids.add(user.user_id)
            if user.email:
                seen_emails.add(user.email.strip().lower())
            is_active = user.is_active
            has_account = True
        elif binding:
            is_active = binding.status == CandidateBindingStatus.ACTIVE.value
            has_account = True
            user_id = binding.user_id
            if binding.user_id:
                seen_user_ids.add(binding.user_id)

        if email:
            seen_emails.add(email)

        # Application & interview counts
        app_count = db.execute(
            select(func.count()).select_from(CandidateApplicationRecord).where(
                CandidateApplicationRecord.candidate_id == ident.identity_id
            )
        ).scalar_one()

        interview_count = db.execute(
            select(func.count()).select_from(CandidateInterviewSessionRecord).where(
                CandidateInterviewSessionRecord.candidate_id == ident.identity_id
            )
        ).scalar_one()

        account_email = user.email.strip() if (user and user.email) else None
        resume_email = (ident.primary_email or "").strip() or None
        exact_email = account_email or resume_email or ""

        results.append(
            AdminCandidateResponse(
                id=str(ident.identity_id),
                identity_id=ident.identity_id,
                artifact_id=ident.artifact_id,
                user_id=user_id,
                full_name=ident.full_name or (user.display_name if user else "Candidate"),
                email=exact_email,
                account_email=account_email,
                resume_email=resume_email,
                headline=headline,
                is_active=is_active,
                has_account=has_account,
                created_at=ident.created_at,
                applications_count=app_count,
                interviews_count=interview_count,
            )
        )

    # Check for orphan candidate users
    orphan_users = db.execute(
        select(UserRecord).where(
            UserRecord.role == UserRole.CANDIDATE,
        )
    ).scalars().all()

    for u in orphan_users:
        if u.user_id in seen_user_ids or (u.email and u.email.strip().lower() in seen_emails):
            continue

        results.append(
            AdminCandidateResponse(
                id=str(u.user_id),
                identity_id=None,
                artifact_id=None,
                user_id=u.user_id,
                full_name=u.display_name or "Candidate",
                email=u.email,
                account_email=u.email,
                resume_email=None,
                headline="Candidate Account",
                is_active=u.is_active,
                has_account=True,
                created_at=u.created_at,
                applications_count=0,
                interviews_count=0,
            )
        )

    return results


@router.patch(
    "/candidates/{candidate_id}",
    response_model=AdminCandidateResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Candidate Status",
    description="Updates candidate active status. Deactivating revokes all active sessions for the candidate.",
)
async def update_candidate_status(
    candidate_id: str,
    update_req: AdminCandidateUpdateRequest,
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    admin_auth: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminCandidateResponse:
    from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
    from app.models.candidate_access import (
        CandidateAccountBindingRecord,
        CandidateBindingStatus,
        CandidateApplicationRecord,
        CandidateInterviewSessionRecord,
    )

    parsed_uuid = _parse_optional_uuid(candidate_id)
    ident = None
    user = None

    if parsed_uuid:
        ident = db.execute(
            select(CandidateIdentityRecord).where(CandidateIdentityRecord.identity_id == parsed_uuid)
        ).scalar_one_or_none()

        if not ident:
            user = db.execute(
                select(UserRecord).where(UserRecord.user_id == parsed_uuid)
            ).scalar_one_or_none()

    if not ident and not user:
        # Check by email
        ident = db.execute(
            select(CandidateIdentityRecord).where(func.lower(CandidateIdentityRecord.primary_email) == candidate_id.lower().strip())
        ).scalar_one_or_none()
        if not ident:
            user = db.execute(
                select(UserRecord).where(
                    func.lower(UserRecord.email) == candidate_id.lower().strip(),
                    UserRecord.role == UserRole.CANDIDATE,
                )
            ).scalar_one_or_none()

    if not ident and not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CANDIDATE_NOT_FOUND", "message": "Candidate not found."},
        )

    now = datetime.now(timezone.utc)
    target_user_id = user.user_id if user else None

    if ident:
        binding = db.execute(
            select(CandidateAccountBindingRecord).where(CandidateAccountBindingRecord.candidate_id == ident.identity_id)
        ).scalar_one_or_none()
        if binding:
            binding.status = (
                CandidateBindingStatus.ACTIVE.value if update_req.is_active else CandidateBindingStatus.SUSPENDED.value
            )
            target_user_id = binding.user_id
            if not user and binding.user_id:
                user = db.execute(
                    select(UserRecord).where(UserRecord.user_id == binding.user_id)
                ).scalar_one_or_none()
        elif not user and ident.primary_email:
            user = db.execute(
                select(UserRecord).where(
                    func.lower(UserRecord.email) == ident.primary_email.lower().strip(),
                    UserRecord.role == UserRole.CANDIDATE,
                )
            ).scalar_one_or_none()
            if user:
                target_user_id = user.user_id

    if user:
        user.is_active = update_req.is_active
        if not update_req.is_active:
            db.execute(
                update(UserSessionRecord)
                .where(
                    UserSessionRecord.user_id == user.user_id,
                    UserSessionRecord.revoked_at.is_(None),
                )
                .values(
                    revoked_at=now,
                    revoke_reason=SessionRevokeReason.ADMIN_REVOKED,
                )
            )

    _record_audit_event(
        db=db,
        event_type=AuditEventType.ACCOUNT_UNLOCKED if update_req.is_active else AuditEventType.ACCOUNT_LOCKED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.SUCCESSFUL_AUTHENTICATION if update_req.is_active else AuditReasonCode.ACCOUNT_LOCKED,
        actor_user_id=admin_auth.user.user_id,
        resource_type="candidate",
        resource_id=candidate_id,
        safe_metadata={"is_active": update_req.is_active},
    )

    db.commit()

    full_name = ident.full_name if ident else (user.display_name if user else "Candidate")
    headline = None
    if ident:
        prof = db.execute(
            select(CandidateProfileRecord)
            .where(CandidateProfileRecord.identity_id == ident.identity_id)
            .order_by(CandidateProfileRecord.profile_version.desc())
        ).scalars().first()
        if prof and prof.professional_profile:
            hl = prof.professional_profile.get("headline")
            headline = hl.get("value") if isinstance(hl, dict) else (hl if isinstance(hl, str) else None)

    account_email = user.email.strip() if (user and user.email) else None
    resume_email = (ident.primary_email or "").strip() if ident else None
    exact_email = account_email or resume_email or ""

    return AdminCandidateResponse(
        id=str(ident.identity_id) if ident else str(user.user_id),
        identity_id=ident.identity_id if ident else None,
        artifact_id=ident.artifact_id if ident else None,
        user_id=target_user_id,
        full_name=full_name or "Candidate",
        email=exact_email,
        account_email=account_email,
        resume_email=resume_email,
        headline=headline or ("Candidate Account" if user else None),
        is_active=update_req.is_active,
        has_account=bool(user or (ident and binding)),
        created_at=ident.created_at if ident else user.created_at,
        applications_count=0,
        interviews_count=0,
    )


@router.delete(
    "/candidates/{candidate_id}",
    status_code=status.HTTP_200_OK,
    summary="Drop Candidate and All Associated Data",
    description="Permanently drops and purges all candidate data, resumes, applications, interviews, bindings, and sessions.",
)
async def delete_candidate(
    candidate_id: str,
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    admin_auth: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    from app.models.candidate import CandidateIdentityRecord

    parsed_uuid = _parse_optional_uuid(candidate_id)
    ident = None
    user = None

    if parsed_uuid:
        ident = db.execute(
            select(CandidateIdentityRecord).where(CandidateIdentityRecord.identity_id == parsed_uuid)
        ).scalar_one_or_none()
        if not ident:
            user = db.execute(
                select(UserRecord).where(UserRecord.user_id == parsed_uuid)
            ).scalar_one_or_none()

    if not ident and not user:
        ident = db.execute(
            select(CandidateIdentityRecord).where(func.lower(CandidateIdentityRecord.primary_email) == candidate_id.lower().strip())
        ).scalar_one_or_none()
        if not ident:
            user = db.execute(
                select(UserRecord).where(
                    func.lower(UserRecord.email) == candidate_id.lower().strip(),
                    UserRecord.role == UserRole.CANDIDATE,
                )
            ).scalar_one_or_none()

    if not ident and not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CANDIDATE_NOT_FOUND", "message": "Candidate not found."},
        )

    cand_ident_id = ident.identity_id if ident else None
    cand_user_id = user.user_id if user else None
    cand_art_id = ident.artifact_id if ident else None
    cand_email = ident.primary_email if ident else (user.email if user else None)

    summary = _drop_candidate_records(
        db=db,
        target_identity_id=cand_ident_id,
        target_user_id=cand_user_id,
        target_artifact_id=cand_art_id,
        target_email=cand_email,
        settings=settings,
    )

    _record_audit_event(
        db=db,
        event_type=AuditEventType.CANDIDATE_DELETED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.CANDIDATE_PURGED,
        actor_user_id=admin_auth.user.user_id,
        resource_type="candidate",
        resource_id=candidate_id,
        safe_metadata=summary,
    )

    db.commit()

    return {
        "message": "Candidate and all associated data dropped successfully.",
        "candidate_id": candidate_id,
        "summary": summary,
    }



@router.get(
    "/security-events",
    response_model=PaginatedSecurityAuditEventsResponse,
    status_code=status.HTTP_200_OK,
    summary="List Security Audit Events",
    description="Returns safely bounded, paginated security audit events for Administrator inspection, ordered by occurred_at descending.",
)
async def list_security_events(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth_ctx: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> PaginatedSecurityAuditEventsResponse:
    count_stmt = select(func.count()).select_from(SecurityAuditEventRecord)
    total = db.execute(count_stmt).scalar_one()

    stmt = (
        select(SecurityAuditEventRecord)
        .order_by(SecurityAuditEventRecord.occurred_at.desc())
        .limit(limit)
        .offset(offset)
    )
    records = db.execute(stmt).scalars().all()

    items = [
        SecurityAuditEventResponse(
            event_id=r.event_id,
            actor_user_id=r.actor_user_id,
            event_type=r.event_type,
            resource_type=r.resource_type,
            resource_id=r.resource_id,
            result=r.result,
            reason_code=r.reason_code,
            safe_metadata=r.safe_metadata,
            occurred_at=r.occurred_at,
        )
        for r in records
    ]

    return PaginatedSecurityAuditEventsResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Phase 3A.4 AI Provider Administration Endpoints (Admin Only)
# ---------------------------------------------------------------------------


@router.get(
    "/provider-profiles",
    response_model=list[AdminProviderProfileResponse],
    status_code=status.HTTP_200_OK,
    summary="List Safe AI Provider Profiles",
    description="Lists configured provider profiles for Administrator inspection with masked secrets and sanitized URLs.",
)
async def list_provider_profiles(
    include_archived: bool = Query(default=True, description="Include archived profiles in response"),
    auth_ctx: AuthenticatedContext = Depends(require_admin),
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> list[AdminProviderProfileResponse]:
    profiles = registry.list_profiles(include_archived=include_archived)
    return [registry.to_safe_admin_response(p) for p in profiles]


@router.post(
    "/provider-profiles/{profile_id}/archive",
    response_model=AdminProviderProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Archive or Restore Provider Profile",
    description="Archives or unarchives a provider profile. Active profiles cannot be archived. Requires Admin role and CSRF.",
)
async def archive_provider_profile(
    profile_id: uuid.UUID,
    archive_req: ArchiveProviderProfileRequest = Body(...),
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    admin_auth: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> AdminProviderProfileResponse:
    try:
        updated = registry.archive_profile(
            profile_id=profile_id,
            version=archive_req.version,
            archive=archive_req.archive,
            actor_id=admin_auth.user.user_id,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider profile '{profile_id}' not found.",
        )
    except StaleVersionError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except InactiveProfileUpdateError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    _record_audit_event(
        db=db,
        event_type=AuditEventType.PROVIDER_PROFILE_UPDATED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.PROVIDER_UPDATED,
        actor_user_id=admin_auth.user.user_id,
        resource_type="provider_profile",
        resource_id=str(updated.profile_id),
        safe_metadata={
            "action": "ARCHIVE" if archive_req.archive else "UNARCHIVE",
            "is_archived": updated.is_archived,
            "version": updated.version,
        },
    )
    db.commit()

    return registry.to_safe_admin_response(updated)


@router.get(
    "/provider-profiles/discover-models",
    response_model=list[str],
    status_code=status.HTTP_200_OK,
    summary="Discover Local LM Studio Models",
    description="Queries local LM Studio /v1/models endpoint safely and returns discovered model IDs.",
)
async def discover_local_models(
    base_url: Optional[str] = Query(default="http://127.0.0.1:1234/v1"),
    auth_ctx: AuthenticatedContext = Depends(require_admin),
) -> list[str]:
    import httpx
    target_url = (base_url or "http://127.0.0.1:1234/v1").rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
            resp = await client.get(target_url)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                    models = [str(m["id"]) for m in data["data"] if isinstance(m, dict) and "id" in m]
                    return models
    except Exception as exc:
        logger.info("Model discovery query failed: %s", exc)
    return []


@router.post(
    "/provider-profiles",
    response_model=AdminProviderProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create AI Provider Profile",
    description="Creates a new inactive AI provider profile with validated destinations. Requires Admin role and CSRF.",
)
async def create_provider_profile(
    create_req: CreateProviderProfileRequest = Body(...),
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    admin_auth: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> AdminProviderProfileResponse:
    try:
        profile = registry.create_profile(create_req, actor_id=admin_auth.user.user_id)
    except ValueError as e:
        _record_audit_event(
            db=db,
            event_type=AuditEventType.PROVIDER_CONFIG_REJECTED,
            result=AuditResult.FAILURE,
            reason_code=AuditReasonCode.PROVIDER_INVALID_CONFIG,
            actor_user_id=admin_auth.user.user_id,
            resource_type="provider_profile",
            safe_metadata={
                "provider_type": create_req.provider_type.value,
                "display_name": create_req.display_name,
                "egress_class": create_req.egress_class.value,
                "error": str(e),
            },
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    _record_audit_event(
        db=db,
        event_type=AuditEventType.PROVIDER_PROFILE_CREATED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.PROVIDER_CONFIGURED,
        actor_user_id=admin_auth.user.user_id,
        resource_type="provider_profile",
        resource_id=str(profile.profile_id),
        safe_metadata={
            "provider_type": profile.provider_type.value,
            "display_name": profile.display_name,
            "egress_class": profile.egress_class.value,
            "version": profile.version,
        },
    )
    db.commit()

    return registry.to_safe_admin_response(profile)


@router.put(
    "/provider-profiles/{profile_id}",
    response_model=AdminProviderProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Inactive AI Provider Profile",
    description="Updates settings on an inactive provider profile with optimistic version check. Requires Admin role and CSRF.",
)
async def update_provider_profile(
    profile_id: uuid.UUID,
    update_req: UpdateProviderProfileRequest = Body(...),
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    admin_auth: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> AdminProviderProfileResponse:
    try:
        updated = registry.update_profile(profile_id, update_req, actor_id=admin_auth.user.user_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider profile '{profile_id}' not found.",
        )
    except StaleVersionError as e:
        _record_audit_event(
            db=db,
            event_type=AuditEventType.PROVIDER_CONFIG_REJECTED,
            result=AuditResult.FAILURE,
            reason_code=AuditReasonCode.STALE_VERSION,
            actor_user_id=admin_auth.user.user_id,
            resource_type="provider_profile",
            resource_id=str(profile_id),
            safe_metadata={"error": str(e), "requested_version": update_req.version},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except InactiveProfileUpdateError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except ValueError as e:
        _record_audit_event(
            db=db,
            event_type=AuditEventType.PROVIDER_CONFIG_REJECTED,
            result=AuditResult.FAILURE,
            reason_code=AuditReasonCode.PROVIDER_INVALID_CONFIG,
            actor_user_id=admin_auth.user.user_id,
            resource_type="provider_profile",
            resource_id=str(profile_id),
            safe_metadata={"error": str(e)},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    _record_audit_event(
        db=db,
        event_type=AuditEventType.PROVIDER_PROFILE_UPDATED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.PROVIDER_UPDATED,
        actor_user_id=admin_auth.user.user_id,
        resource_type="provider_profile",
        resource_id=str(updated.profile_id),
        safe_metadata={
            "provider_type": updated.provider_type.value,
            "display_name": updated.display_name,
            "egress_class": updated.egress_class.value,
            "version": updated.version,
        },
    )
    db.commit()

    return registry.to_safe_admin_response(updated)


@router.post(
    "/provider-profiles/{profile_id}/activate",
    response_model=AdminProviderProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Activate Eligible AI Provider Profile",
    description="Activates an eligible provider profile, transactionally disabling previous profiles. Requires Admin role and CSRF.",
)
async def activate_provider_profile(
    profile_id: uuid.UUID,
    activate_req: ActivateProviderProfileRequest = Body(...),
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    admin_auth: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> AdminProviderProfileResponse:
    try:
        activated = registry.activate_profile(profile_id, activate_req, actor_id=admin_auth.user.user_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider profile '{profile_id}' not found.",
        )
    except StaleVersionError as e:
        _record_audit_event(
            db=db,
            event_type=AuditEventType.PROVIDER_CONFIG_REJECTED,
            result=AuditResult.FAILURE,
            reason_code=AuditReasonCode.STALE_VERSION,
            actor_user_id=admin_auth.user.user_id,
            resource_type="provider_profile",
            resource_id=str(profile_id),
            safe_metadata={"error": str(e), "requested_version": activate_req.version},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except (IneligibleProviderError, RemoteEgressConfirmationRequiredError) as e:
        _record_audit_event(
            db=db,
            event_type=AuditEventType.PROVIDER_CONFIG_REJECTED,
            result=AuditResult.FAILURE,
            reason_code=AuditReasonCode.PROVIDER_INCOMPATIBLE,
            actor_user_id=admin_auth.user.user_id,
            resource_type="provider_profile",
            resource_id=str(profile_id),
            safe_metadata={"error": str(e)},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    _record_audit_event(
        db=db,
        event_type=AuditEventType.PROVIDER_PROFILE_ACTIVATED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.PROVIDER_ACTIVATED,
        actor_user_id=admin_auth.user.user_id,
        resource_type="provider_profile",
        resource_id=str(activated.profile_id),
        safe_metadata={
            "provider_type": activated.provider_type.value,
            "display_name": activated.display_name,
            "egress_class": activated.egress_class.value,
            "version": activated.version,
            "reason": activate_req.reason,
        },
    )
    db.commit()

    return registry.to_safe_admin_response(activated)


@router.post(
    "/provider-profiles/{profile_id}/disable",
    response_model=AdminProviderProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Disable AI Provider Profile",
    description="Disables an active or inactive provider profile. Requires Admin role and CSRF.",
)
async def disable_provider_profile(
    profile_id: uuid.UUID,
    disable_req: DisableProviderProfileRequest = Body(...),
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    admin_auth: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> AdminProviderProfileResponse:
    try:
        disabled = registry.disable_profile(profile_id, disable_req, actor_id=admin_auth.user.user_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider profile '{profile_id}' not found.",
        )
    except StaleVersionError as e:
        _record_audit_event(
            db=db,
            event_type=AuditEventType.PROVIDER_CONFIG_REJECTED,
            result=AuditResult.FAILURE,
            reason_code=AuditReasonCode.STALE_VERSION,
            actor_user_id=admin_auth.user.user_id,
            resource_type="provider_profile",
            resource_id=str(profile_id),
            safe_metadata={"error": str(e), "requested_version": disable_req.version},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    _record_audit_event(
        db=db,
        event_type=AuditEventType.PROVIDER_PROFILE_DISABLED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.PROVIDER_DISABLED,
        actor_user_id=admin_auth.user.user_id,
        resource_type="provider_profile",
        resource_id=str(disabled.profile_id),
        safe_metadata={
            "provider_type": disabled.provider_type.value,
            "display_name": disabled.display_name,
            "egress_class": disabled.egress_class.value,
            "version": disabled.version,
            "reason": disable_req.reason,
        },
    )
    db.commit()

    return registry.to_safe_admin_response(disabled)


@router.post(
    "/provider-profiles/{profile_id}/test-compatibility",
    response_model=ProviderCompatibilityReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Test AI Provider Endpoint Compatibility",
    description="Executes synthetic compatibility verification against a provider endpoint (Admin only). Returns safe diagnostics without secrets.",
)
async def test_provider_compatibility(
    profile_id: uuid.UUID,
    test_req: TestProviderCompatibilityRequest = Body(...),
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    admin_auth: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> ProviderCompatibilityReportResponse:
    try:
        report = await registry.test_compatibility(
            profile_id=profile_id,
            request=test_req,
            actor_id=admin_auth.user.user_id,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider profile '{profile_id}' not found.",
        )
    except StaleVersionError as e:
        _record_audit_event(
            db=db,
            event_type=AuditEventType.PROVIDER_CONFIG_REJECTED,
            result=AuditResult.FAILURE,
            reason_code=AuditReasonCode.STALE_VERSION,
            actor_user_id=admin_auth.user.user_id,
            resource_type="provider_profile",
            resource_id=str(profile_id),
            safe_metadata={"error": str(e), "requested_version": test_req.version},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    _record_audit_event(
        db=db,
        event_type=AuditEventType.PROVIDER_COMPATIBILITY_TESTED,
        result=AuditResult.SUCCESS if report.is_compatible else AuditResult.FAILURE,
        reason_code=AuditReasonCode.PROVIDER_COMPATIBILITY_SUCCESS if report.is_compatible else AuditReasonCode.PROVIDER_COMPATIBILITY_FAILED,
        actor_user_id=admin_auth.user.user_id,
        resource_type="provider_profile",
        resource_id=str(profile_id),
        safe_metadata={
            "is_compatible": report.is_compatible,
            "compatibility_status": report.compatibility_status.value,
            "provider_type": report.provider_type.value,
            "model_identifier": report.model_identifier,
            "latency_ms": report.latency_ms,
            "safe_failure_code": report.safe_failure_code,
            "suite_version": report.suite_version,
            "discovered_model_count": len(report.discovered_model_ids),
        },
    )
    db.commit()

    return report


@router.post(
    "/provider-profiles/{profile_id}/select-model",
    response_model=AdminProviderProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Select Model for Provider Profile",
    description="Updates the model identifier directly on a provider profile, resetting compatibility status and persisting to PostgreSQL.",
)
async def select_provider_model(
    profile_id: uuid.UUID,
    select_req: SelectProviderModelRequest = Body(...),
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    admin_auth: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> AdminProviderProfileResponse:
    try:
        updated = registry.set_profile_model(
            profile_id=profile_id,
            model_identifier=select_req.model_identifier,
            actor_id=admin_auth.user.user_id,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider profile '{profile_id}' not found.",
        )

    _record_audit_event(
        db=db,
        event_type=AuditEventType.PROVIDER_PROFILE_UPDATED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.PROVIDER_UPDATED,
        actor_user_id=admin_auth.user.user_id,
        resource_type="provider_profile",
        resource_id=str(updated.profile_id),
        safe_metadata={
            "action": "SELECT_MODEL",
            "model_identifier": updated.model_identifier,
            "version": updated.version,
        },
    )
    db.commit()

    return registry.to_safe_admin_response(updated)


