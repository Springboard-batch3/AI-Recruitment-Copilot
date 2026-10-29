"""API Router for Recruiter Job-Contextual Applicants Workflow (Phase 4B.5).

Provides endpoints for authorized recruiters and admins to:
1. List job-specific applications (active and withdrawn).
2. View application detail with deterministic match evidence and interview eligibility.
3. Explicitly update internal recruiter review state (RECEIVED -> IN_REVIEW -> INTERVIEW_READY).
4. Explicitly release an approved, revision-matched interview to an eligible candidate.
"""
import logging
import uuid
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.auth import (
    AuthenticatedContext,
    require_recruiter,
    require_role,
)
from app.database import get_db
from app.models.auth import UserRole
from app.schemas.recruiter_application import (
    RecruiterApplicationDetailResponse,
    RecruiterApplicationListResponse,
    RecruiterReleaseInterviewRequest,
    RecruiterReleaseInterviewResponse,
    RecruiterReviewStateUpdateRequest,
)
from app.services.recruiter_applicant_service import (
    get_job_application_detail_for_recruiter,
    list_job_applications_for_recruiter,
    release_interview_for_application,
    update_application_review_state,
)

logger = logging.getLogger("ai_hiring.api.recruiter_applications")

router = APIRouter(
    prefix="/api/v1/jobs/{job_id}/applications",
    tags=["recruiter_applications"],
)

require_recruiter_or_admin = require_role({UserRole.RECRUITER, UserRole.ADMIN})


@router.get(
    "",
    response_model=RecruiterApplicationListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Job Applicants",
    description="Returns a paginated list of candidate applications for an authorized job requisition.",
)
async def list_job_applicants(
    job_id: UUID,
    status: Optional[str] = Query(None, description="Optional application status filter (SUBMITTED, WITHDRAWN)"),
    review_state: Optional[str] = Query(None, description="Optional recruiter review state filter (RECEIVED, IN_REVIEW, INTERVIEW_READY)"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    mode: Optional[str] = Query(None, description="Optional interview mode filter (TEXT, AUDIO)"),
    revision: Optional[int] = Query(None, description="Optional job revision filter"),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> RecruiterApplicationListResponse:
    return list_job_applications_for_recruiter(
        job_id=job_id,
        actor=auth_ctx.user,
        db=db,
        status_filter=status,
        review_state_filter=review_state,
        mode_filter=mode,
        revision=revision,
        page=page,
        limit=limit,
    )


@router.get(
    "/{application_id}",
    response_model=RecruiterApplicationDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Job Application Detail",
    description="Returns full application detail, candidate profile summary, existing deterministic match evidence, and interview release readiness.",
)
async def get_job_applicant_detail(
    job_id: UUID,
    application_id: UUID,
    mode: Optional[str] = Query(None, description="Optional interview mode context (TEXT, AUDIO)"),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> RecruiterApplicationDetailResponse:
    return get_job_application_detail_for_recruiter(
        job_id=job_id,
        application_id=application_id,
        actor=auth_ctx.user,
        db=db,
        mode=mode,
    )


@router.patch(
    "/{application_id}/review-state",
    response_model=RecruiterApplicationDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Recruiter Review State",
    description="Transitions internal recruiter review state (RECEIVED -> IN_REVIEW -> INTERVIEW_READY).",
)
async def update_applicant_review_state(
    job_id: UUID,
    application_id: UUID,
    payload: RecruiterReviewStateUpdateRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> RecruiterApplicationDetailResponse:
    return update_application_review_state(
        job_id=job_id,
        application_id=application_id,
        payload=payload,
        actor=auth_ctx.user,
        db=db,
    )


@router.post(
    "/{application_id}/release-interview",
    response_model=RecruiterReleaseInterviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Release Approved Interview to Candidate",
    description="Explicitly releases an approved question set matching the application's submitted job revision to the candidate.",
)
async def release_applicant_interview(
    job_id: UUID,
    application_id: UUID,
    payload: RecruiterReleaseInterviewRequest = RecruiterReleaseInterviewRequest(),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> RecruiterReleaseInterviewResponse:
    return release_interview_for_application(
        job_id=job_id,
        application_id=application_id,
        payload=payload,
        actor=auth_ctx.user,
        db=db,
    )
