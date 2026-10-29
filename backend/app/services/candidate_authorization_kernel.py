"""Centralized Candidate Authorization Kernel and Policy Service (Phase 4B.2).

Provides typed Candidate principals, deny-by-default authorization policies,
and ownership-scoped database resource loaders for Candidate operations.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import (
    AuditEventType,
    AuditReasonCode,
    AuditResult,
    UserRecord,
    UserRole,
    UserSessionRecord,
)
from app.models.candidate import (
    CandidateIdentityRecord,
    CandidateProfileRecord,
)
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateApplicationRecord,
    CandidateApplicationStatus,
    CandidateBindingStatus,
    CandidatePracticeReleaseRecord,
    CandidatePracticeRequestRecord,
    CandidatePracticeRequestStatus,
    CandidatePracticeSessionRecord,
    JobPracticeCatalogRecord,
)
from app.models.extraction import ResumeExtraction
from app.models.interview import CandidateInterviewPlanRecord, SimulationAttemptRecord
from app.models.job import JobRequisition, JobVersion
from app.schemas.phase3_enums import PracticeReleaseStatus, PracticeSessionLifecycle

logger = logging.getLogger("ai_hiring.services.candidate_authz")


@dataclass(frozen=True)
class CandidatePrincipal:
    """Server-derived immutable principal representing an authenticated candidate actor."""
    user_id: UUID
    role: str
    session_id: UUID
    candidate_id: Optional[UUID] = None
    binding_id: Optional[UUID] = None
    organization_id: Optional[UUID] = None
    binding_status: Optional[str] = None
    must_change_password: bool = False

    @classmethod
    def from_context(
        cls,
        user: UserRecord,
        session: UserSessionRecord,
        binding: Optional[CandidateAccountBindingRecord] = None,
    ) -> "CandidatePrincipal":
        return cls(
            user_id=user.user_id,
            role=user.role.value if hasattr(user.role, "value") else str(user.role),
            session_id=session.session_id,
            candidate_id=binding.candidate_id if binding else None,
            binding_id=binding.binding_id if binding else None,
            organization_id=binding.organization_id if binding else None,
            binding_status=binding.status if binding else None,
            must_change_password=bool(user.must_change_password),
        )


def _record_authz_audit(
    db: Session,
    principal: Optional[CandidatePrincipal],
    event_type: AuditEventType,
    result: AuditResult,
    reason_code: AuditReasonCode,
    resource_type: str,
    resource_id: Optional[str] = None,
    safe_metadata: Optional[Dict[str, Any]] = None,
) -> None:
    from app.models.auth import SecurityAuditEventRecord
    audit_rec = SecurityAuditEventRecord(
        event_type=event_type,
        result=result,
        reason_code=reason_code,
        actor_user_id=principal.user_id if principal else None,
        resource_type=resource_type,
        resource_id=resource_id,
        safe_metadata=safe_metadata or {},
        occurred_at=datetime.now(timezone.utc),
    )
    db.add(audit_rec)
    db.commit()


# =============================================================================
# Deny-By-Default Centralized Authorization Policies
# =============================================================================

def authorize_view_own_profile(
    principal: CandidatePrincipal,
    target_candidate_id: UUID,
    db: Session,
) -> None:
    """Enforces that candidate may only access their own profile."""
    if principal.role != UserRole.CANDIDATE.value or (principal.binding_status and principal.binding_status != CandidateBindingStatus.ACTIVE.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "CANDIDATE_ACCESS_DENIED", "message": "Candidate account or binding is not active."},
        )

    if not principal.candidate_id or principal.candidate_id != target_candidate_id:
        _record_authz_audit(
            db=db,
            principal=principal,
            event_type=AuditEventType.ACCESS_DENIED,
            result=AuditResult.DENIED,
            reason_code=AuditReasonCode.UNAUTHORIZED,
            resource_type="candidate_profile",
            resource_id=str(target_candidate_id),
            safe_metadata={"action": "VIEW_OWN_PROFILE", "denial": "CROSS_CANDIDATE_ACCESS"},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROFILE_NOT_FOUND", "message": "Candidate profile not found."},
        )


def authorize_access_practice_release(
    principal: CandidatePrincipal,
    release: CandidatePracticeReleaseRecord,
    db: Session,
) -> None:
    """Enforces ownership and lifecycle checks for a candidate practice release."""
    if principal.role != UserRole.CANDIDATE.value or principal.binding_status != CandidateBindingStatus.ACTIVE.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "CANDIDATE_ACCESS_DENIED", "message": "Candidate account or binding is not active."},
        )

    # Ownership & Org check
    if release.candidate_id != principal.candidate_id or release.organization_id != principal.organization_id:
        _record_authz_audit(
            db=db,
            principal=principal,
            event_type=AuditEventType.ACCESS_DENIED,
            result=AuditResult.DENIED,
            reason_code=AuditReasonCode.UNAUTHORIZED,
            resource_type="candidate_practice_release",
            resource_id=str(release.release_id),
            safe_metadata={"action": "ACCESS_RELEASE", "denial": "OWNERSHIP_OR_ORG_MISMATCH"},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RELEASE_NOT_FOUND", "message": "Practice assignment not found for candidate."},
        )

    # Lifecycle state check
    if release.status != PracticeReleaseStatus.RELEASED.value:
        _record_authz_audit(
            db=db,
            principal=principal,
            event_type=AuditEventType.ACCESS_DENIED,
            result=AuditResult.DENIED,
            reason_code=AuditReasonCode.UNAUTHORIZED,
            resource_type="candidate_practice_release",
            resource_id=str(release.release_id),
            safe_metadata={"action": "ACCESS_RELEASE", "denial": "RELEASE_REVOKED", "status": release.status},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "RELEASE_REVOKED", "message": "This practice assignment has been revoked."},
        )


def authorize_access_practice_session(
    principal: CandidatePrincipal,
    session: CandidatePracticeSessionRecord,
    db: Session,
    action: str = "ACCESS_SESSION",
) -> None:
    """Enforces ownership and active lifecycle for candidate practice session operations."""
    if principal.role != UserRole.CANDIDATE.value or principal.binding_status != CandidateBindingStatus.ACTIVE.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "CANDIDATE_ACCESS_DENIED", "message": "Candidate account or binding is not active."},
        )

    # Ownership & Org check
    if session.candidate_id != principal.candidate_id or session.organization_id != principal.organization_id:
        _record_authz_audit(
            db=db,
            principal=principal,
            event_type=AuditEventType.ACCESS_DENIED,
            result=AuditResult.DENIED,
            reason_code=AuditReasonCode.UNAUTHORIZED,
            resource_type="candidate_practice_session",
            resource_id=str(session.session_id),
            safe_metadata={"action": action, "denial": "OWNERSHIP_OR_ORG_MISMATCH"},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Practice session not found."},
        )


# =============================================================================
# Ownership-Scoped Resource Loaders
# =============================================================================

def load_scoped_candidate_release(
    principal: CandidatePrincipal,
    release_id: UUID,
    db: Session,
) -> CandidatePracticeReleaseRecord:
    """Loads a CandidatePracticeReleaseRecord strictly scoped to the authenticated candidate principal."""
    stmt = (
        select(CandidatePracticeReleaseRecord)
        .options(
            selectinload(CandidatePracticeReleaseRecord.job),
            selectinload(CandidatePracticeReleaseRecord.job_version),
            selectinload(CandidatePracticeReleaseRecord.ai_set),
        )
        .where(
            CandidatePracticeReleaseRecord.release_id == release_id,
            CandidatePracticeReleaseRecord.candidate_id == principal.candidate_id,
            CandidatePracticeReleaseRecord.organization_id == principal.organization_id,
        )
    )
    release = db.execute(stmt).scalar_one_or_none()
    if not release:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RELEASE_NOT_FOUND", "message": "Practice assignment not found for candidate."},
        )

    authorize_access_practice_release(principal=principal, release=release, db=db)
    return release


def load_scoped_candidate_session(
    principal: CandidatePrincipal,
    session_id: UUID,
    db: Session,
    for_update: bool = False,
    action: str = "LOAD_SESSION",
) -> CandidatePracticeSessionRecord:
    """Loads a CandidatePracticeSessionRecord strictly scoped to the authenticated candidate principal."""
    stmt = (
        select(CandidatePracticeSessionRecord)
        .options(
            selectinload(CandidatePracticeSessionRecord.messages),
            selectinload(CandidatePracticeSessionRecord.job),
            selectinload(CandidatePracticeSessionRecord.job_version),
            selectinload(CandidatePracticeSessionRecord.reports),
        )
        .where(
            CandidatePracticeSessionRecord.session_id == session_id,
            CandidatePracticeSessionRecord.candidate_id == principal.candidate_id,
            CandidatePracticeSessionRecord.organization_id == principal.organization_id,
        )
    )
    if for_update:
        stmt = stmt.with_for_update()

    session = db.execute(stmt).scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Practice session not found."},
        )

    authorize_access_practice_session(principal=principal, session=session, db=db, action=action)
    return session


def load_scoped_candidate_practice_request(
    principal: CandidatePrincipal,
    request_id: UUID,
    db: Session,
) -> CandidatePracticeRequestRecord:
    """Loads a CandidatePracticeRequestRecord strictly scoped to the authenticated candidate principal."""
    stmt = select(CandidatePracticeRequestRecord).where(
        CandidatePracticeRequestRecord.request_id == request_id,
        CandidatePracticeRequestRecord.candidate_id == principal.candidate_id,
        CandidatePracticeRequestRecord.organization_id == principal.organization_id,
    )
    req = db.execute(stmt).scalar_one_or_none()
    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "REQUEST_NOT_FOUND", "message": "Practice request not found."},
        )
    return req


def load_scoped_candidate_profile(
    principal: CandidatePrincipal,
    db: Session,
    version: Optional[int] = None,
) -> CandidateProfileRecord:
    """Loads CandidateProfileRecord for the authenticated candidate principal."""
    if not principal.candidate_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROFILE_NOT_FOUND", "message": "Candidate profile not found."},
        )

    stmt = (
        select(CandidateProfileRecord)
        .options(
            selectinload(CandidateProfileRecord.identity),
            selectinload(CandidateProfileRecord.extraction).selectinload(ResumeExtraction.blocks),
        )
        .where(CandidateProfileRecord.identity_id == principal.candidate_id)
    )
    if version is not None:
        stmt = stmt.where(CandidateProfileRecord.profile_version == version)
    else:
        stmt = stmt.order_by(desc(CandidateProfileRecord.profile_version))

    profile = db.execute(stmt).scalars().first()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROFILE_NOT_FOUND", "message": "Candidate profile not found."},
        )
    return profile


def authorize_access_application(
    principal: CandidatePrincipal,
    application: CandidateApplicationRecord,
    db: Session,
    action: str = "ACCESS_APPLICATION",
) -> None:
    """Enforces ownership and binding status checks for candidate applications."""
    if principal.role != UserRole.CANDIDATE.value or (principal.binding_status and principal.binding_status != CandidateBindingStatus.ACTIVE.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "CANDIDATE_ACCESS_DENIED", "message": "Candidate account or binding is not active."},
        )

    # Ownership check: either matching user_id or matching binding_id
    is_owner = False
    if application.user_id and application.user_id == principal.user_id:
        is_owner = True
    elif principal.binding_id and application.binding_id == principal.binding_id:
        is_owner = True

    if not is_owner:
        _record_authz_audit(
            db=db,
            principal=principal,
            event_type=AuditEventType.ACCESS_DENIED,
            result=AuditResult.DENIED,
            reason_code=AuditReasonCode.UNAUTHORIZED,
            resource_type="candidate_application",
            resource_id=str(application.application_id),
            safe_metadata={"action": action, "denial": "OWNERSHIP_MISMATCH"},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "APPLICATION_NOT_FOUND", "message": "Application not found."},
        )


def load_scoped_candidate_application(
    principal: CandidatePrincipal,
    application_id: UUID,
    db: Session,
    for_update: bool = False,
    action: str = "LOAD_APPLICATION",
) -> CandidateApplicationRecord:
    """Loads a CandidateApplicationRecord strictly scoped to the authenticated candidate principal."""
    conditions = [CandidateApplicationRecord.user_id == principal.user_id]
    if principal.binding_id:
        conditions.append(CandidateApplicationRecord.binding_id == principal.binding_id)

    stmt = (
        select(CandidateApplicationRecord)
        .options(
            selectinload(CandidateApplicationRecord.job),
            selectinload(CandidateApplicationRecord.job_version),
            selectinload(CandidateApplicationRecord.profile),
            selectinload(CandidateApplicationRecord.artifact),
        )
        .where(
            CandidateApplicationRecord.application_id == application_id,
            or_(*conditions),
        )
    )
    if for_update:
        stmt = stmt.with_for_update()

    app_rec = db.execute(stmt).scalar_one_or_none()
    if not app_rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "APPLICATION_NOT_FOUND", "message": "Application not found."},
        )

    authorize_access_application(principal=principal, application=app_rec, db=db, action=action)
    return app_rec


