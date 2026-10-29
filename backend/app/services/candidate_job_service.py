"""Candidate Job Discovery Service (Phase 4B.3).

Implements candidate-safe, deny-by-default job discovery operations.
Excludes drafts, archived, and deleted jobs. Returns only the latest approved version.
"""
import logging
import uuid
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import AuditEventType, AuditReasonCode, AuditResult
from app.models.candidate_access import CandidateApplicationRecord, CandidateApplicationStatus
from app.models.job import (
    JobLifecycleStatus,
    JobPublicationStatus,
    JobRequirement,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
)
from app.schemas.candidate_job import (
    CandidateJobDetailResponse,
    CandidateJobListResponse,
    CandidateJobSummaryResponse,
)
from app.services.candidate_authorization_kernel import (
    CandidatePrincipal,
    _record_authz_audit,
)

logger = logging.getLogger("ai_hiring.services.candidate_job")


def list_candidate_open_jobs(
    principal: CandidatePrincipal,
    db: Session,
    query: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
) -> CandidateJobListResponse:
    """Lists candidate-safe eligible jobs.
    
    A job is eligible if:
    - JobRequisition.status == ACTIVE
    - JobRequisition.publication_status == RELEASED
    - JobRequisition.deleted_at is NULL
    - Has an associated released JobVersion (or fallback latest READY_FOR_MATCHING / APPROVED version)
    
    Returns the released version per active released requisition with bounded pagination and deterministic ordering.
    """
    page = max(1, page)
    limit = max(1, min(limit, 50))
    offset = (page - 1) * limit

    # Query active job requisitions that are RELEASED
    stmt = (
        select(JobRequisition)
        .where(
            JobRequisition.status == JobLifecycleStatus.ACTIVE,
            JobRequisition.publication_status == JobPublicationStatus.RELEASED,
            JobRequisition.deleted_at.is_(None),
        )
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements)
        )
        .distinct()
    )

    all_active_jobs = db.execute(stmt).scalars().all()

    # Collect released version for each job
    eligible_items: List[CandidateJobSummaryResponse] = []

    for job in all_active_jobs:
        # 1. Prefer explicit released_job_version_id
        released_ver: Optional[JobVersion] = None
        if job.released_job_version_id:
            released_ver = next(
                (v for v in job.versions if v.job_version_id == job.released_job_version_id),
                None,
            )
        
        # 2. Fallback to latest locked/approved version
        if not released_ver:
            candidate_versions = [
                v for v in job.versions if v.version_status in (JobVersionStatus.APPROVED, JobVersionStatus.READY_FOR_MATCHING)
            ]
            if not candidate_versions:
                continue
            released_ver = sorted(
                candidate_versions,
                key=lambda v: (v.version_number, v.revision),
                reverse=True,
            )[0]

        # Optional search filter across title, department, location, summary
        if query:
            q_clean = query.strip().lower()
            t_match = released_ver.title and q_clean in released_ver.title.lower()
            d_match = released_ver.department and q_clean in released_ver.department.lower()
            l_match = released_ver.location and q_clean in released_ver.location.lower()
            s_match = released_ver.summary and q_clean in released_ver.summary.lower()
            if not (t_match or d_match or l_match or s_match):
                continue

        published_time = job.released_at or released_ver.approved_at or released_ver.ready_at or released_ver.updated_at or released_ver.created_at

        eligible_items.append(
            CandidateJobSummaryResponse(
                job_id=job.job_id,
                job_version_id=released_ver.job_version_id,
                version_number=released_ver.version_number,
                revision=released_ver.revision,
                title=released_ver.title,
                department=released_ver.department,
                location=released_ver.location,
                employment_type=released_ver.employment_type,
                workplace_type=released_ver.workplace_type,
                openings_count=released_ver.openings_count,
                summary=released_ver.summary,
                published_at=published_time,
                updated_at=released_ver.updated_at or released_ver.created_at,
            )
        )

    # Deterministic sorting: published_at desc, job_id asc
    eligible_items.sort(key=lambda item: (item.published_at, str(item.job_id)), reverse=True)

    total_count = len(eligible_items)
    paginated_items = eligible_items[offset : offset + limit]

    return CandidateJobListResponse(
        items=paginated_items,
        total=total_count,
        page=page,
        limit=limit,
    )


def get_candidate_open_job_detail(
    principal: CandidatePrincipal,
    job_id: uuid.UUID,
    db: Session,
    requested_version_id: Optional[uuid.UUID] = None,
) -> CandidateJobDetailResponse:
    """Retrieves candidate-safe details for a released job requisition.
    
    Ensures that only the released version is returned. If a specific version_id
    is requested in the URL, it must match the released version, otherwise safely returns 404.
    """
    stmt = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements)
        )
        .where(
            JobRequisition.job_id == job_id,
            JobRequisition.status == JobLifecycleStatus.ACTIVE,
            JobRequisition.publication_status == JobPublicationStatus.RELEASED,
            JobRequisition.deleted_at.is_(None),
        )
    )
    job = db.execute(stmt).scalar_one_or_none()

    if not job:
        _record_authz_audit(
            db=db,
            principal=principal,
            event_type=AuditEventType.ACCESS_DENIED,
            result=AuditResult.DENIED,
            reason_code=AuditReasonCode.RESOURCE_NOT_FOUND,
            resource_type="candidate_job",
            resource_id=str(job_id),
            safe_metadata={"action": "GET_JOB_DETAIL", "denial": "JOB_NOT_FOUND_UNRELEASED_OR_INACTIVE"},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job posting not found or unavailable."},
        )

    released_ver: Optional[JobVersion] = None
    if job.released_job_version_id:
        released_ver = next(
            (v for v in job.versions if v.job_version_id == job.released_job_version_id),
            None,
        )

    if not released_ver:
        candidate_versions = [
            v for v in job.versions if v.version_status in (JobVersionStatus.APPROVED, JobVersionStatus.READY_FOR_MATCHING)
        ]
        if not candidate_versions:
            _record_authz_audit(
                db=db,
                principal=principal,
                event_type=AuditEventType.ACCESS_DENIED,
                result=AuditResult.DENIED,
                reason_code=AuditReasonCode.RESOURCE_NOT_FOUND,
                resource_type="candidate_job",
                resource_id=str(job_id),
                safe_metadata={"action": "GET_JOB_DETAIL", "denial": "NO_RELEASED_VERSION"},
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "JOB_NOT_FOUND", "message": "Job posting not found or unavailable."},
            )
        released_ver = sorted(
            candidate_versions,
            key=lambda v: (v.version_number, v.revision),
            reverse=True,
        )[0]

    # If caller specifically requested a version, verify it is the released one
    if requested_version_id and requested_version_id != released_ver.job_version_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Requested job version is not available."},
        )

    # Extract public qualifications (sorted by display_order)
    raw_reqs = released_ver.requirements or []
    sorted_reqs = sorted(raw_reqs, key=lambda r: r.display_order)
    public_qualifications = [
        r.label for r in sorted_reqs if r.label and r.label.strip()
    ]

    published_time = job.released_at or released_ver.approved_at or released_ver.ready_at or released_ver.updated_at or released_ver.created_at

    # Check if candidate has active SUBMITTED application for this job
    active_app = None
    if principal.binding_id:
        stmt_app = select(CandidateApplicationRecord).where(
            CandidateApplicationRecord.binding_id == principal.binding_id,
            CandidateApplicationRecord.job_id == job.job_id,
            CandidateApplicationRecord.status == CandidateApplicationStatus.SUBMITTED.value,
        )
        active_app = db.execute(stmt_app).scalar_one_or_none()

    return CandidateJobDetailResponse(
        job_id=job.job_id,
        job_version_id=released_ver.job_version_id,
        version_number=released_ver.version_number,
        revision=released_ver.revision,
        title=released_ver.title,
        department=released_ver.department,
        location=released_ver.location,
        employment_type=released_ver.employment_type,
        workplace_type=released_ver.workplace_type,
        openings_count=released_ver.openings_count,
        summary=released_ver.summary,
        responsibilities=released_ver.responsibilities,
        qualifications=public_qualifications,
        published_at=published_time,
        updated_at=released_ver.updated_at or released_ver.created_at,
        has_active_application=active_app is not None,
        active_application_id=active_app.application_id if active_app else None,
    )
