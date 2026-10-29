import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.api.auth import (
    AuthenticatedContext,
    _record_audit_event,
    get_authenticated_context,
    require_recruiter,
    require_role,
)
from app.database import get_db
from app.models.auth import AuditEventType, AuditReasonCode, AuditResult, UserRole
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.job import (
    JobLifecycleStatus,
    JobPublicationStatus,
    JobRequirement,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
)
from app.models.matching import MatchResultRecord, MatchRunRecord
from app.schemas.provider_admin import (
    InterviewContextResponse,
    RecruiterAIAvailabilityResponse,
)
from app.services.provider_registry import (
    ProviderRegistry,
    get_provider_registry,
)
from app.models.taxonomy import (
    OccupationConcept,
    SkillConcept,
    TaxonomySnapshot,
    TaxonomySnapshotState,
)
from app.schemas.job import (
    JobCreateRequest,
    JobCreateSuccessorRequest,
    JobDetailResponse,
    JobDraftUpdateRequest,
    JobMarkReadyRequest,
    JobReadinessFindingResponse,
    JobReadinessResponse,
    JobRequirementsSaveRequest,
    JobReviewWarningResponse,
    JobSummaryResponse,
    JobVersionDetailResponse,
    JobVersionSummaryResponse,
    RequirementItemResponse,
)
from app.services.job_readiness import evaluate_job_version_readiness
from app.services.requirement_validation import (
    RequirementValidationError,
    validate_and_prepare_requirements,
)

logger = logging.getLogger("ai_hiring.api.jobs")

router = APIRouter(
    prefix="/api/v1/jobs",
    tags=["jobs"],
)

# Role dependency allowing RECRUITER or ADMIN
require_recruiter_or_admin = require_role({UserRole.RECRUITER, UserRole.ADMIN})


def _compute_payload_hash(payload_dict: dict) -> str:
    """Computes deterministic SHA-256 digest of normalized payload."""
    serialized = json.dumps(payload_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().lower()


def _build_job_version_detail_response(version: JobVersion) -> JobVersionDetailResponse:
    requirements_resp: List[RequirementItemResponse] = []
    all_warnings: List[JobReviewWarningResponse] = []

    if version.requirements:
        for r in version.requirements:
            r_resp = RequirementItemResponse(
                requirement_id=r.requirement_id,
                display_order=r.display_order,
                category=r.category,
                label=r.label,
                normalized_label=r.normalized_label,
                taxonomy_skill_concept_id=r.taxonomy_skill_concept_id,
                necessity=r.necessity,
                priority=r.priority,
                custom_weight_override=r.custom_weight_override,
                min_experience_months=r.min_experience_months,
                recency_years=r.recency_years,
                accepted_evidence_types=r.accepted_evidence_types or [],
                accepted_equivalents=r.accepted_equivalents or [],
                rationale=r.rationale,
                origin=r.origin,
                review_required=r.review_required,
                warning_metadata=r.warning_metadata or [],
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            requirements_resp.append(r_resp)

            if r.warning_metadata:
                for w in r.warning_metadata:
                    all_warnings.append(
                        JobReviewWarningResponse(
                            code=w.get("code", "REVIEW_WARNING"),
                            message=w.get("message", ""),
                            severity=w.get("severity", "REVIEW_SUGGESTED"),
                            requirement_id=r.requirement_id,
                            rule=w.get("rule"),
                            matched_term=w.get("matched_term"),
                        )
                    )

    occupation_title = version.occupation.canonical_title if version.occupation else None

    return JobVersionDetailResponse(
        job_version_id=version.job_version_id,
        job_id=version.job_id,
        version_number=version.version_number,
        version_status=version.version_status,
        revision=version.revision,
        title=version.title,
        department=version.department,
        location=version.location,
        employment_type=version.employment_type,
        workplace_type=version.workplace_type,
        openings_count=version.openings_count,
        internal_reference=version.internal_reference,
        summary=version.summary,
        responsibilities=version.responsibilities,
        job_description_text=version.job_description_text,
        occupation_concept_id=version.occupation_concept_id,
        occupation_title=occupation_title,
        taxonomy_version=version.taxonomy_version,
        parser_version=version.parser_version,
        created_by_user_id=version.created_by_user_id,
        approved_by_user_id=version.approved_by_user_id,
        approved_at=version.approved_at,
        ready_by_user_id=version.ready_by_user_id,
        ready_at=version.ready_at,
        requirements=requirements_resp,
        review_warnings=all_warnings,
        created_at=version.created_at,
        updated_at=version.updated_at,
    )


def _build_job_version_summary_response(version: JobVersion) -> JobVersionSummaryResponse:
    return JobVersionSummaryResponse(
        job_version_id=version.job_version_id,
        version_number=version.version_number,
        version_status=version.version_status,
        revision=version.revision,
        title=version.title,
        department=version.department,
        location=version.location,
        employment_type=version.employment_type,
        workplace_type=version.workplace_type,
        openings_count=version.openings_count,
        internal_reference=version.internal_reference,
        requirements_count=len(version.requirements) if version.requirements else 0,
        ready_by_user_id=version.ready_by_user_id,
        ready_at=version.ready_at,
        created_at=version.created_at,
        updated_at=version.updated_at,
    )


def _build_job_detail_response(req: JobRequisition, version: JobVersion) -> JobDetailResponse:
    return JobDetailResponse(
        job_id=req.job_id,
        created_by_user_id=req.created_by_user_id,
        status=req.status,
        publication_status=req.publication_status,
        released_at=req.released_at,
        released_by_user_id=req.released_by_user_id,
        released_job_version_id=req.released_job_version_id,
        created_at=req.created_at,
        updated_at=req.updated_at,
        current_version=_build_job_version_detail_response(version),
    )


def _validate_occupation_concept(occupation_concept_id: Optional[uuid.UUID], db: Session) -> Optional[OccupationConcept]:
    if not occupation_concept_id:
        return None
    stmt = (
        select(OccupationConcept)
        .join(TaxonomySnapshot, OccupationConcept.snapshot_id == TaxonomySnapshot.snapshot_id)
        .where(
            TaxonomySnapshot.state == TaxonomySnapshotState.ACTIVE,
            OccupationConcept.concept_id == occupation_concept_id,
        )
    )
    occ = db.execute(stmt).scalar_one_or_none()
    if occ is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_OCCUPATION_CONCEPT",
                "message": f"Occupation concept '{occupation_concept_id}' not found in active taxonomy snapshot.",
            },
        )
    return occ


@router.get(
    "/ai-capability",
    response_model=RecruiterAIAvailabilityResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Safe AI Capability Status",
    description="Returns minimal, safe provider capability descriptor for authenticated platform users.",
)
async def get_ai_capability(
    auth_ctx: AuthenticatedContext = Depends(get_authenticated_context),
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> RecruiterAIAvailabilityResponse:
    active_profile = registry.get_active_profile()
    return registry.to_recruiter_capability(active_profile)


@router.post(
    "",
    response_model=JobDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Job Requisition and Initial Draft",
    description=(
        "Atomically creates a new JobRequisition and its initial version-1 DRAFT JobVersion. "
        "Supports optional Idempotency-Key header to safely retry requests."
    ),
)
async def create_job(
    create_req: JobCreateRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> JobDetailResponse:
    actor = auth_ctx.user
    now = datetime.now(timezone.utc)

    payload_dict = create_req.model_dump(mode="json")
    payload_hash = _compute_payload_hash(payload_dict)

    # 1. Handle Idempotency-Key
    if idempotency_key:
        clean_key = idempotency_key.strip()
        if len(clean_key) > 255:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_IDEMPOTENCY_KEY", "message": "Idempotency-Key header exceeds maximum length of 255 characters."},
            )

        if clean_key:
            # Query existing requisition with same idempotency key for this creator
            stmt = (
                select(JobRequisition)
                .options(
                    selectinload(JobRequisition.versions).selectinload(JobVersion.requirements),
                    selectinload(JobRequisition.versions).selectinload(JobVersion.occupation),
                )
                .where(
                    JobRequisition.created_by_user_id == actor.user_id,
                    JobRequisition.idempotency_key == clean_key,
                )
            )
            existing = db.execute(stmt).scalar_one_or_none()
            if existing is not None:
                if existing.idempotency_payload_hash == payload_hash:
                    # Idempotent match: return existing job
                    v1 = next((v for v in existing.versions if v.version_number == 1), existing.versions[0] if existing.versions else None)
                    if v1 is not None:
                        return _build_job_detail_response(existing, v1)
                # Differing payload
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "IDEMPOTENCY_CONFLICT",
                        "message": "Idempotency key already used with a different job creation payload.",
                    },
                )

    # Validate optional occupation concept
    occ = _validate_occupation_concept(create_req.occupation_concept_id, db)

    # 2. Atomic creation of JobRequisition and JobVersion v1
    job_id = uuid.uuid4()
    version_id = uuid.uuid4()

    clean_idem_key = idempotency_key.strip() if idempotency_key and idempotency_key.strip() else None

    requisition = JobRequisition(
        job_id=job_id,
        created_by_user_id=actor.user_id,
        status=JobLifecycleStatus.ACTIVE,
        idempotency_key=clean_idem_key,
        idempotency_payload_hash=payload_hash if clean_idem_key else None,
        created_at=now,
        updated_at=now,
    )

    version_1 = JobVersion(
        job_version_id=version_id,
        job_id=job_id,
        version_number=1,
        version_status=JobVersionStatus.DRAFT,
        revision=1,
        title=create_req.title,
        department=create_req.department,
        location=create_req.location,
        employment_type=create_req.employment_type,
        workplace_type=create_req.workplace_type,
        openings_count=create_req.openings_count,
        internal_reference=create_req.internal_reference,
        summary=create_req.summary,
        responsibilities=create_req.responsibilities,
        job_description_text=create_req.job_description_text,
        occupation_concept_id=create_req.occupation_concept_id,
        created_by_user_id=actor.user_id,
        created_at=now,
        updated_at=now,
    )

    try:
        db.add(requisition)
        db.flush()
        db.add(version_1)
        db.commit()
        db.refresh(requisition)
        db.refresh(version_1)
        if occ:
            version_1.occupation = occ
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("Failed to create job requisition and version", extra={"error_type": type(e).__name__})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "JOB_CREATION_FAILED", "message": "Failed to create job."},
        )

    return _build_job_detail_response(requisition, version_1)


@router.get(
    "",
    response_model=List[JobSummaryResponse],
    status_code=status.HTTP_200_OK,
    summary="List Accessible Jobs",
    description=(
        "Returns a list of concise job summaries accessible to the authenticated caller. "
        "Recruiters see only their own jobs; Admins see all jobs. Ordered newest updated first."
    ),
)
async def list_jobs(
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> List[JobSummaryResponse]:
    actor = auth_ctx.user

    stmt = select(JobRequisition).options(
        selectinload(JobRequisition.versions).selectinload(JobVersion.requirements)
    ).where(JobRequisition.deleted_at.is_(None))

    # Recruiter sees only their own jobs; Admin sees all
    if actor.role == UserRole.RECRUITER:
        stmt = stmt.where(JobRequisition.created_by_user_id == actor.user_id)

    # Deterministic ordering: updated_at desc, job_id desc tie breaker
    stmt = stmt.order_by(JobRequisition.updated_at.desc(), JobRequisition.job_id.desc())

    requisitions = db.execute(stmt).scalars().all()

    items = []
    for req in requisitions:
        # Find latest version (highest version_number)
        if not req.versions:
            continue
        latest_version = max(req.versions, key=lambda v: v.version_number)
        items.append(
            JobSummaryResponse(
                job_id=req.job_id,
                created_by_user_id=req.created_by_user_id,
                status=req.status,
                publication_status=req.publication_status,
                released_at=req.released_at,
                released_by_user_id=req.released_by_user_id,
                released_job_version_id=req.released_job_version_id,
                created_at=req.created_at,
                updated_at=req.updated_at,
                current_version=_build_job_version_summary_response(latest_version),
            )
        )

    return items


@router.get(
    "/{job_id}",
    response_model=JobDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Job Detail",
    description="Returns the job requisition and its current version draft/details. Enforces creator ownership.",
)
async def get_job(
    job_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> JobDetailResponse:
    actor = auth_ctx.user

    stmt = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements),
            selectinload(JobRequisition.versions).selectinload(JobVersion.occupation),
        )
        .where(
            JobRequisition.job_id == job_id,
            JobRequisition.deleted_at.is_(None),
        )
    )
    req = db.execute(stmt).scalar_one_or_none()

    # Enforce ownership / conceal existence if recruiter is not owner
    if req is None or (actor.role == UserRole.RECRUITER and req.created_by_user_id != actor.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job not found."},
        )

    if not req.versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job version not found."},
        )

    latest_version = max(req.versions, key=lambda v: v.version_number)
    return _build_job_detail_response(req, latest_version)


@router.patch(
    "/{job_id}/draft",
    response_model=JobDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Current Draft Job Version",
    description=(
        "Applies partial updates to the current DRAFT version only. "
        "Requires expected_revision matching the current draft revision. Increments revision upon success."
    ),
)
async def update_job_draft(
    job_id: uuid.UUID,
    update_req: JobDraftUpdateRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> JobDetailResponse:
    actor = auth_ctx.user
    now = datetime.now(timezone.utc)

    stmt = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements),
            selectinload(JobRequisition.versions).selectinload(JobVersion.occupation),
        )
        .where(
            JobRequisition.job_id == job_id,
            JobRequisition.deleted_at.is_(None),
        )
    )
    req = db.execute(stmt).scalar_one_or_none()

    # Enforce ownership / conceal existence
    if req is None or (actor.role == UserRole.RECRUITER and req.created_by_user_id != actor.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job not found."},
        )

    if not req.versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job version not found."},
        )

    draft_version = max(req.versions, key=lambda v: v.version_number)

    # 1. Reject if not DRAFT
    if draft_version.version_status != JobVersionStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "JOB_NOT_DRAFT",
                "message": "Only job versions in DRAFT status can be updated.",
            },
        )

    # 2. Check expected revision conflict
    if draft_version.revision != update_req.expected_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "REVISION_CONFLICT",
                "message": f"Draft revision conflict. Expected revision {draft_version.revision}, but received {update_req.expected_revision}.",
            },
        )

    # 3. Validate occupation concept if provided in update
    fields_set = update_req.model_fields_set - {"expected_revision"}
    if "occupation_concept_id" in fields_set:
        occ = _validate_occupation_concept(update_req.occupation_concept_id, db)
        draft_version.occupation = occ

    # 4. Apply partial updates
    for field_name in fields_set:
        val = getattr(update_req, field_name)
        setattr(draft_version, field_name, val)

    # Increment revision and update timestamps
    draft_version.revision += 1
    draft_version.updated_at = now
    req.updated_at = now

    try:
        db.commit()
        db.refresh(req)
        db.refresh(draft_version)
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("Failed to update job draft", extra={"job_id": str(job_id), "error_type": type(e).__name__})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "JOB_UPDATE_FAILED", "message": "Failed to update draft."},
        )

    return _build_job_detail_response(req, draft_version)


@router.put(
    "/{job_id}/draft/requirements",
    response_model=JobDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Save Draft Structured Requirements Atomically",
    description=(
        "Atomically replaces the entire requirement list for the current DRAFT job version. "
        "Validates all requirements before persistence. Requires matching expected_revision "
        "and increments revision exactly once upon success."
    ),
)
async def save_draft_requirements(
    job_id: uuid.UUID,
    save_req: JobRequirementsSaveRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> JobDetailResponse:
    actor = auth_ctx.user
    now = datetime.now(timezone.utc)

    stmt = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements),
            selectinload(JobRequisition.versions).selectinload(JobVersion.occupation),
        )
        .where(
            JobRequisition.job_id == job_id,
            JobRequisition.deleted_at.is_(None),
        )
    )
    req = db.execute(stmt).scalar_one_or_none()

    # Enforce ownership / conceal existence
    if req is None or (actor.role == UserRole.RECRUITER and req.created_by_user_id != actor.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job not found."},
        )

    if not req.versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job version not found."},
        )

    draft_version = max(req.versions, key=lambda v: v.version_number)

    # 1. Reject if not DRAFT
    if draft_version.version_status != JobVersionStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "JOB_NOT_DRAFT",
                "message": "Only job versions in DRAFT status can be updated.",
            },
        )

    # 2. Check optimistic revision conflict
    if draft_version.revision != save_req.expected_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "REVISION_CONFLICT",
                "message": f"Draft revision conflict. Expected revision {draft_version.revision}, but received {save_req.expected_revision}.",
            },
        )

    # 3. Validate requirements atomically before making changes
    req_dicts = [r.model_dump(mode="json") for r in save_req.requirements]
    try:
        new_requirement_models = validate_and_prepare_requirements(
            requirements_data=req_dicts,
            job_version_id=draft_version.job_version_id,
            db=db,
        )
    except RequirementValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": err.code,
                "message": err.message,
                "field": err.field,
            },
        )

    # 4. Atomic replacement: delete existing requirements and add new ones
    try:
        draft_version.requirements.clear()
        db.flush()
        for r_model in new_requirement_models:
            draft_version.requirements.append(r_model)

        draft_version.revision += 1
        draft_version.updated_at = now
        req.updated_at = now

        db.commit()
        db.refresh(req)
        db.refresh(draft_version)
    except IntegrityError as ie:
        db.rollback()
        logger.error("Integrity error replacing draft requirements", extra={"job_id": str(job_id)})
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "REQUIREMENT_INTEGRITY_FAILED", "message": "Requirement constraint violated."},
        )
    except SQLAlchemyError as se:
        db.rollback()
        logger.error("SQLAlchemy error replacing draft requirements", extra={"job_id": str(job_id), "error_type": type(se).__name__})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "REQUIREMENTS_SAVE_FAILED", "message": "Failed to save draft requirements."},
        )

    return _build_job_detail_response(req, draft_version)


@router.get(
    "/{job_id}/readiness",
    response_model=JobReadinessResponse,
    status_code=status.HTTP_200_OK,
    summary="Check Job Revision Readiness",
    description="Evaluates server-side readiness validation for the latest job revision without mutating state.",
)
async def check_job_readiness(
    job_id: uuid.UUID,
    expected_revision: Optional[int] = None,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> JobReadinessResponse:
    actor = auth_ctx.user

    stmt = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements),
            selectinload(JobRequisition.versions).selectinload(JobVersion.occupation),
        )
        .where(
            JobRequisition.job_id == job_id,
            JobRequisition.deleted_at.is_(None),
        )
    )
    req = db.execute(stmt).scalar_one_or_none()

    if req is None or (actor.role == UserRole.RECRUITER and req.created_by_user_id != actor.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job not found."},
        )

    if not req.versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job version not found."},
        )

    latest_version = max(req.versions, key=lambda v: v.version_number)

    readiness = evaluate_job_version_readiness(
        version=latest_version,
        db=db,
        expected_revision=expected_revision,
    )

    return JobReadinessResponse(
        job_id=readiness.job_id,
        job_version_id=readiness.job_version_id,
        version_number=readiness.version_number,
        revision=readiness.revision,
        version_status=readiness.version_status,
        is_ready=readiness.is_ready,
        errors=[
            JobReadinessFindingResponse(
                code=e.code,
                severity=e.severity.value,
                message=e.message,
                field=e.field_name,
                requirement_id=e.requirement_id,
                blocking=e.blocking,
                metadata=e.metadata,
            )
            for e in readiness.errors
        ],
        warnings=[
            JobReadinessFindingResponse(
                code=w.code,
                severity=w.severity.value,
                message=w.message,
                field=w.field_name,
                requirement_id=w.requirement_id,
                blocking=w.blocking,
                metadata=w.metadata,
            )
            for w in readiness.warnings
        ],
        info=[
            JobReadinessFindingResponse(
                code=i.code,
                severity=i.severity.value,
                message=i.message,
                field=i.field_name,
                requirement_id=i.requirement_id,
                blocking=i.blocking,
                metadata=i.metadata,
            )
            for i in readiness.info
        ],
    )


@router.post(
    "/{job_id}/ready",
    response_model=JobDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Mark Job Revision Ready for Matching",
    description="Validates and atomically locks the current DRAFT revision as READY_FOR_MATCHING.",
)
async def mark_job_ready(
    job_id: uuid.UUID,
    payload: JobMarkReadyRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> JobDetailResponse:
    actor = auth_ctx.user
    now = datetime.now(timezone.utc)

    stmt = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements),
            selectinload(JobRequisition.versions).selectinload(JobVersion.occupation),
        )
        .where(
            JobRequisition.job_id == job_id,
            JobRequisition.deleted_at.is_(None),
        )
        .with_for_update(of=JobRequisition)
    )
    req = db.execute(stmt).scalar_one_or_none()

    if req is None or (actor.role == UserRole.RECRUITER and req.created_by_user_id != actor.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job not found."},
        )

    if not req.versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job version not found."},
        )

    latest_version = max(req.versions, key=lambda v: v.version_number)

    # Idempotent check: if already READY_FOR_MATCHING and matching expected revision
    if latest_version.version_status == JobVersionStatus.READY_FOR_MATCHING:
        if latest_version.revision == payload.expected_revision:
            return _build_job_detail_response(req, latest_version)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "REVISION_CONFLICT",
                "message": f"Job version is already locked as READY_FOR_MATCHING with revision {latest_version.revision}.",
            },
        )

    if latest_version.version_status != JobVersionStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "JOB_NOT_DRAFT",
                "message": f"Only DRAFT versions can be marked ready. Current status: {latest_version.version_status}.",
            },
        )

    # Concurrency check
    if latest_version.revision != payload.expected_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "REVISION_CONFLICT",
                "message": f"Draft revision conflict. Expected revision {latest_version.revision}, but received {payload.expected_revision}.",
            },
        )

    # Server-side readiness re-validation
    readiness = evaluate_job_version_readiness(
        version=latest_version,
        db=db,
        expected_revision=payload.expected_revision,
    )

    if not readiness.is_ready:
        first_err = readiness.errors[0] if readiness.errors else None
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": first_err.code if first_err else "READINESS_VALIDATION_FAILED",
                "message": first_err.message if first_err else "Requisition does not meet readiness criteria.",
                "errors": [
                    {
                        "code": e.code,
                        "message": e.message,
                        "field": e.field_name,
                        "requirement_id": str(e.requirement_id) if e.requirement_id else None,
                    }
                    for e in readiness.errors
                ],
            },
        )

    # Transition to READY_FOR_MATCHING
    latest_version.version_status = JobVersionStatus.READY_FOR_MATCHING
    latest_version.ready_by_user_id = actor.user_id
    latest_version.ready_at = now
    latest_version.approved_by_user_id = actor.user_id
    latest_version.approved_at = now
    latest_version.updated_at = now
    req.updated_at = now

    try:
        db.commit()
        db.refresh(req)
        db.refresh(latest_version)
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("Failed to mark job ready", extra={"job_id": str(job_id), "error_type": type(e).__name__})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "JOB_READY_FAILED", "message": "Failed to mark job ready for matching."},
        )

    return _build_job_detail_response(req, latest_version)


@router.post(
    "/{job_id}/release",
    response_model=JobDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Release Job Revision to Candidates",
    description="Releases an approved, locked job revision so candidates can discover and apply to the job.",
)
async def release_job(
    job_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> JobDetailResponse:
    actor = auth_ctx.user
    now = datetime.now(timezone.utc)

    stmt = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements),
            selectinload(JobRequisition.versions).selectinload(JobVersion.occupation),
        )
        .where(
            JobRequisition.job_id == job_id,
            JobRequisition.deleted_at.is_(None),
        )
        .with_for_update(of=JobRequisition)
    )
    req = db.execute(stmt).scalar_one_or_none()

    if req is None or (actor.role == UserRole.RECRUITER and req.created_by_user_id != actor.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job not found."},
        )

    if req.status != JobLifecycleStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "JOB_NOT_ACTIVE", "message": "Archived or inactive jobs cannot be released."},
        )

    if not req.versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job version not found."},
        )

    # Find the latest version to release
    latest_version = max(req.versions, key=lambda v: v.version_number)

    # Idempotent check: if already released pointing to this exact version
    if (
        req.publication_status == JobPublicationStatus.RELEASED
        and req.released_job_version_id == latest_version.job_version_id
    ):
        return _build_job_detail_response(req, latest_version)

    # Current revision must be READY_FOR_MATCHING or APPROVED
    if latest_version.version_status not in (JobVersionStatus.READY_FOR_MATCHING, JobVersionStatus.APPROVED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "JOB_NOT_READY_FOR_MATCHING",
                "message": f"Job revision must be approved and locked before releasing. Current version status: {latest_version.version_status}.",
            },
        )

    # Job must contain at least one active opening
    if latest_version.openings_count is None or latest_version.openings_count < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "NO_ACTIVE_OPENINGS",
                "message": "Job must contain at least one active opening to be released.",
            },
        )

    # Update publication status on job requisition
    req.publication_status = JobPublicationStatus.RELEASED
    req.released_at = now
    req.released_by_user_id = actor.user_id
    req.released_job_version_id = latest_version.job_version_id
    req.updated_at = now

    logger.info(
        "Job revision released to candidates",
        extra={
            "job_id": str(job_id),
            "released_by": str(actor.user_id),
            "released_job_version_id": str(latest_version.job_version_id),
            "version_number": latest_version.version_number,
            "revision": latest_version.revision,
        },
    )

    try:
        db.commit()
        db.refresh(req)
        db.refresh(latest_version)
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("Failed to release job", extra={"job_id": str(job_id), "error_type": type(e).__name__})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "JOB_RELEASE_FAILED", "message": "Failed to release job to candidates."},
        )

    return _build_job_detail_response(req, latest_version)


@router.post(
    "/{job_id}/versions",
    response_model=JobDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Successor Job Version Draft",
    description="Creates a new DRAFT successor version from a locked READY_FOR_MATCHING version without modifying historical versions.",
)
async def create_successor_version(
    job_id: uuid.UUID,
    payload: JobCreateSuccessorRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> JobDetailResponse:
    actor = auth_ctx.user
    now = datetime.now(timezone.utc)

    stmt = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements),
            selectinload(JobRequisition.versions).selectinload(JobVersion.occupation),
        )
        .where(
            JobRequisition.job_id == job_id,
            JobRequisition.deleted_at.is_(None),
        )
        .with_for_update(of=JobRequisition)
    )
    req = db.execute(stmt).scalar_one_or_none()

    if req is None or (actor.role == UserRole.RECRUITER and req.created_by_user_id != actor.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job not found."},
        )

    if not req.versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job version not found."},
        )

    latest_version = max(req.versions, key=lambda v: v.version_number)

    # Check if latest version is already a DRAFT
    if latest_version.version_status == JobVersionStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "DRAFT_ALREADY_EXISTS",
                "message": f"Version {latest_version.version_number} is already an active DRAFT. Edit the existing draft or mark it ready before creating a new version.",
            },
        )

    # Create successor version (version_number = latest + 1)
    new_version_number = latest_version.version_number + 1
    new_version_id = uuid.uuid4()

    successor_version = JobVersion(
        job_version_id=new_version_id,
        job_id=req.job_id,
        version_number=new_version_number,
        version_status=JobVersionStatus.DRAFT,
        revision=1,
        title=latest_version.title,
        department=latest_version.department,
        location=latest_version.location,
        employment_type=latest_version.employment_type,
        workplace_type=latest_version.workplace_type,
        openings_count=latest_version.openings_count,
        internal_reference=latest_version.internal_reference,
        summary=latest_version.summary,
        responsibilities=latest_version.responsibilities,
        job_description_text=latest_version.job_description_text,
        occupation_concept_id=latest_version.occupation_concept_id,
        taxonomy_version=latest_version.taxonomy_version,
        parser_version=latest_version.parser_version,
        created_by_user_id=actor.user_id,
        created_at=now,
        updated_at=now,
    )

    # Clone requirements with fresh identities and preserved order/data
    if latest_version.requirements:
        for r in latest_version.requirements:
            cloned_req = JobRequirement(
                requirement_id=uuid.uuid4(),
                job_version_id=new_version_id,
                display_order=r.display_order,
                category=r.category,
                label=r.label,
                normalized_label=r.normalized_label,
                taxonomy_skill_concept_id=r.taxonomy_skill_concept_id,
                necessity=r.necessity,
                priority=r.priority,
                custom_weight_override=r.custom_weight_override,
                min_experience_months=r.min_experience_months,
                recency_years=r.recency_years,
                accepted_evidence_types=list(r.accepted_evidence_types or []),
                accepted_equivalents=list(r.accepted_equivalents or []),
                rationale=r.rationale,
                origin=r.origin,
                review_required=False,
                warning_metadata=[],
                created_at=now,
                updated_at=now,
            )
            successor_version.requirements.append(cloned_req)

    req.updated_at = now

    try:
        db.add(successor_version)
        db.commit()
        db.refresh(req)
        db.refresh(successor_version)
        if latest_version.occupation:
            successor_version.occupation = latest_version.occupation
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("Failed to create successor job version", extra={"job_id": str(job_id), "error_type": type(e).__name__})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "SUCCESSOR_CREATION_FAILED", "message": "Failed to create successor version."},
        )

    return _build_job_detail_response(req, successor_version)


@router.get(
    "/{job_id}/versions",
    response_model=List[JobVersionSummaryResponse],
    status_code=status.HTTP_200_OK,
    summary="Get Job Version History",
    description="Returns chronological version history for a job requisition accessible to the recruiter.",
)
async def list_job_versions(
    job_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> List[JobVersionSummaryResponse]:
    actor = auth_ctx.user

    stmt = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements)
        )
        .where(
            JobRequisition.job_id == job_id,
            JobRequisition.deleted_at.is_(None),
        )
    )
    req = db.execute(stmt).scalar_one_or_none()

    if req is None or (actor.role == UserRole.RECRUITER and req.created_by_user_id != actor.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job not found."},
        )

    sorted_versions = sorted(req.versions, key=lambda v: v.version_number, reverse=True)
    return [_build_job_version_summary_response(v) for v in sorted_versions]


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_200_OK,
    summary="Soft Delete Job Requisition",
    description="Soft deletes a job requisition by setting deleted_at and deleted_by_user_id. Preserves historical records.",
)
async def delete_job(
    job_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> dict:
    actor = auth_ctx.user
    now = datetime.now(timezone.utc)

    stmt = select(JobRequisition).where(
        JobRequisition.job_id == job_id,
        JobRequisition.deleted_at.is_(None),
    )
    req = db.execute(stmt).scalar_one_or_none()

    if req is None or (actor.role == UserRole.RECRUITER and req.created_by_user_id != actor.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job not found."},
        )

    req.deleted_at = now
    req.deleted_by_user_id = actor.user_id
    req.updated_at = now

    try:
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("Failed to delete job requisition", extra={"job_id": str(job_id), "error_type": type(e).__name__})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "JOB_DELETION_FAILED", "message": "Failed to delete job."},
        )

    return {"message": "Job deleted."}


# ---------------------------------------------------------------------------
# Phase 3A.4 Recruiter Interview Workspace Shell Endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/{job_id}/interview-context",
    response_model=InterviewContextResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Recruiter Job Interview Context",
    description=(
        "Returns authorized job and AI capability metadata for the Recruiter interview workspace shell "
        "at the job requisition level without candidate binding."
    ),
)
async def get_job_interview_workspace_context(
    job_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> InterviewContextResponse:
    actor = auth_ctx.user

    # Verify job existence and recruiter ownership
    stmt_job = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements)
        )
        .where(
            JobRequisition.job_id == job_id,
            JobRequisition.deleted_at.is_(None),
        )
    )
    job = db.execute(stmt_job).scalar_one_or_none()

    if job is None or (actor.role == UserRole.RECRUITER and job.created_by_user_id != actor.user_id):
        _record_audit_event(
            db=db,
            event_type=AuditEventType.ACCESS_DENIED,
            result=AuditResult.DENIED,
            reason_code=AuditReasonCode.ACCESS_DENIED,
            actor_user_id=actor.user_id,
            resource_type="interview_context",
            resource_id=str(job_id),
            safe_metadata={"reason": "UNAUTHORIZED_JOB_ACCESS"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job requisition not found or unauthorized."},
        )

    latest_version = sorted(job.versions, key=lambda v: (v.version_number, v.revision), reverse=True)[0]
    active_profile = registry.get_active_profile()
    ai_capability = registry.to_recruiter_capability(active_profile)

    return InterviewContextResponse(
        job_id=job.job_id,
        job_title=latest_version.title,
        job_version_id=latest_version.job_version_id,
        job_revision=latest_version.revision,
        candidate_id=None,
        candidate_name=None,
        candidate_email=None,
        resume_artifact_id=None,
        match_run_id=None,
        match_score=None,
        skill_gap_summary=None,
        ai_capability=ai_capability,
        interview_plan_state="EMPTY_PLAN",
        message="Interview template workspace ready for job requisition.",
    )


@router.get(
    "/{job_id}/candidates/{resume_id}/interview-context",
    response_model=InterviewContextResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Recruiter Interview Workspace Context",
    description=(
        "Returns authorized job, candidate identity, and match metadata for the Recruiter interview workspace shell. "
        "Strictly requires RECRUITER role and ownership of the job requisition."
    ),
)
async def get_interview_workspace_context(
    job_id: uuid.UUID,
    resume_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> InterviewContextResponse:
    actor = auth_ctx.user

    # Verify job existence and recruiter ownership
    stmt_job = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements)
        )
        .where(
            JobRequisition.job_id == job_id,
            JobRequisition.deleted_at.is_(None),
        )
    )
    job = db.execute(stmt_job).scalar_one_or_none()

    if job is None or job.created_by_user_id != actor.user_id:
        _record_audit_event(
            db=db,
            event_type=AuditEventType.ACCESS_DENIED,
            result=AuditResult.DENIED,
            reason_code=AuditReasonCode.ACCESS_DENIED,
            actor_user_id=actor.user_id,
            resource_type="interview_context",
            resource_id=f"{job_id}:{resume_id}",
            safe_metadata={"reason": "UNAUTHORIZED_JOB_ACCESS"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job requisition not found or unauthorized."},
        )

    # Resolve latest or active job version
    latest_version = sorted(job.versions, key=lambda v: (v.version_number, v.revision), reverse=True)[0]

    # Verify candidate / resume profile
    stmt_prof = (
        select(CandidateProfileRecord)
        .options(selectinload(CandidateProfileRecord.identity))
        .where(CandidateProfileRecord.artifact_id == resume_id)
        .order_by(CandidateProfileRecord.profile_version.desc())
    )
    candidate_profile = db.execute(stmt_prof).scalars().first()

    if candidate_profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CANDIDATE_NOT_FOUND", "message": "Candidate profile not found."},
        )

    candidate_name = "Candidate"
    candidate_email = None
    if candidate_profile.identity:
        candidate_name = candidate_profile.identity.full_name or "Candidate"
        candidate_email = candidate_profile.identity.primary_email

    # Query match score and gap report summary if available
    stmt_match = (
        select(MatchResultRecord)
        .join(MatchRunRecord, MatchResultRecord.run_id == MatchRunRecord.run_id)
        .where(
            MatchRunRecord.job_id == job_id,
            MatchResultRecord.profile_id == candidate_profile.profile_id,
        )
    )
    match_rec = db.execute(stmt_match).scalars().first()

    match_run_id = match_rec.run_id if match_rec else None
    match_score = float(match_rec.display_compatibility_score) if match_rec and match_rec.display_compatibility_score is not None else None
    skill_gap_summary = None
    if match_rec:
        tier_str = match_rec.confidence_tier or "PENDING"
        skill_gap_summary = f"Confidence Tier: {tier_str} (Score: {match_score:.1f}%)"

    active_profile = registry.get_active_profile()
    ai_capability = registry.to_recruiter_capability(active_profile)

    return InterviewContextResponse(
        job_id=job.job_id,
        job_title=latest_version.title,
        job_version_id=latest_version.job_version_id,
        job_revision=latest_version.revision,
        candidate_id=candidate_profile.profile_id,
        candidate_name=candidate_name,
        candidate_email=candidate_email,
        resume_artifact_id=resume_id,
        match_run_id=match_run_id,
        match_score=match_score,
        skill_gap_summary=skill_gap_summary,
        ai_capability=ai_capability,
        interview_plan_state="EMPTY_PLAN",
        message="Interview workspace ready. Standardized core competencies and supplemental probes will be generated in Phase 3B.",
    )

