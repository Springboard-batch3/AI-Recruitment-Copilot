"""API Router for Candidate Applications Lifecycle (Phase 4B.4).

Provides endpoints for authenticated candidate actors to submit job applications,
view their submitted applications (My Applications), view application detail,
and safely withdraw applications.
"""
import logging
import uuid
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.auth import (
    CandidateAuthenticatedContext,
    require_candidate,
)
from app.config import Settings, get_settings
from app.database import get_db
from app.schemas.candidate_application import (
    CandidateApplicationDraftRequest,
    CandidateApplicationListResponse,
    CandidateApplicationResponse,
    CandidateApplicationSubmitRequest,
    CandidateResumeStatusResponse,
)
from app.services.candidate_application_service import (
    create_or_get_candidate_application_draft,
    get_candidate_application_detail,
    get_candidate_application_resume_status,
    list_candidate_applications,
    submit_candidate_application,
    submit_candidate_application_draft,
    upload_candidate_application_resume,
    withdraw_candidate_application,
)
from app.services.candidate_authorization_kernel import CandidatePrincipal

logger = logging.getLogger("ai_hiring.api.candidate_applications")

router = APIRouter(
    prefix="/api/v1/candidate/applications",
    tags=["candidate_applications"],
)


@router.post(
    "/drafts",
    response_model=CandidateApplicationResponse,
    status_code=status.HTTP_200_OK,
    summary="Create or Get Application Draft",
    description="Initializes or retrieves an existing draft application for a candidate-visible released job.",
)
async def create_or_get_draft(
    payload: CandidateApplicationDraftRequest,
    auth_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> CandidateApplicationResponse:
    principal = CandidatePrincipal.from_context(
        user=auth_ctx.user,
        session=auth_ctx.session,
        binding=auth_ctx.binding,
    )
    return create_or_get_candidate_application_draft(
        principal=principal,
        payload=payload,
        db=db,
    )


@router.post(
    "/{application_id}/resume",
    response_model=CandidateResumeStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload Resume for Application",
    description="Uploads and processes a candidate's resume (PDF/DOCX, max 10MB) for their draft application.",
)
async def upload_application_resume(
    application_id: UUID,
    file: UploadFile = File(...),
    auth_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CandidateResumeStatusResponse:
    principal = CandidatePrincipal.from_context(
        user=auth_ctx.user,
        session=auth_ctx.session,
        binding=auth_ctx.binding,
    )
    return await upload_candidate_application_resume(
        principal=principal,
        application_id=application_id,
        file=file,
        settings=settings,
        db=db,
    )


@router.get(
    "/{application_id}/resume/status",
    response_model=CandidateResumeStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Resume Processing Status",
    description="Retrieves the current extraction and profile status of the uploaded resume for an application draft.",
)
async def get_application_resume_status(
    application_id: UUID,
    auth_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> CandidateResumeStatusResponse:
    principal = CandidatePrincipal.from_context(
        user=auth_ctx.user,
        session=auth_ctx.session,
        binding=auth_ctx.binding,
    )
    return get_candidate_application_resume_status(
        principal=principal,
        application_id=application_id,
        db=db,
    )


@router.post(
    "/{application_id}/submit",
    response_model=CandidateApplicationResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit Application Draft",
    description="Submits a prepared candidate application draft after resume processing completes successfully.",
)
async def submit_draft(
    application_id: UUID,
    payload: Optional[CandidateApplicationSubmitRequest] = None,
    auth_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> CandidateApplicationResponse:
    principal = CandidatePrincipal.from_context(
        user=auth_ctx.user,
        session=auth_ctx.session,
        binding=auth_ctx.binding,
    )
    return submit_candidate_application_draft(
        principal=principal,
        application_id=application_id,
        db=db,
        payload=payload,
    )


@router.post(
    "/jobs/{job_id}",
    response_model=CandidateApplicationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit Candidate Application",
    description="Submits an application to an open, candidate-visible job using the candidate's bound profile.",
)
async def submit_application(
    job_id: UUID,
    payload: CandidateApplicationSubmitRequest,
    auth_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> CandidateApplicationResponse:
    principal = CandidatePrincipal.from_context(
        user=auth_ctx.user,
        session=auth_ctx.session,
        binding=auth_ctx.binding,
    )
    return submit_candidate_application(
        principal=principal,
        job_id=job_id,
        payload=payload,
        db=db,
    )


@router.get(
    "",
    response_model=CandidateApplicationListResponse,
    status_code=status.HTTP_200_OK,
    summary="List My Applications",
    description="Returns a paginated list of all applications submitted or withdrawn by the authenticated candidate.",
)
async def list_my_applications(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=50, description="Items per page"),
    auth_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> CandidateApplicationListResponse:
    principal = CandidatePrincipal.from_context(
        user=auth_ctx.user,
        session=auth_ctx.session,
        binding=auth_ctx.binding,
    )
    return list_candidate_applications(
        principal=principal,
        db=db,
        page=page,
        limit=limit,
    )


@router.get(
    "/{application_id}",
    response_model=CandidateApplicationResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Candidate Application Detail",
    description="Returns full safe details for a single application owned by the authenticated candidate.",
)
async def get_application_detail(
    application_id: UUID,
    auth_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> CandidateApplicationResponse:
    principal = CandidatePrincipal.from_context(
        user=auth_ctx.user,
        session=auth_ctx.session,
        binding=auth_ctx.binding,
    )
    return get_candidate_application_detail(
        principal=principal,
        application_id=application_id,
        db=db,
    )


@router.post(
    "/{application_id}/withdraw",
    response_model=CandidateApplicationResponse,
    status_code=status.HTTP_200_OK,
    summary="Withdraw Candidate Application",
    description="Withdraws an active SUBMITTED application owned by the authenticated candidate.",
)
async def withdraw_application(
    application_id: UUID,
    auth_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> CandidateApplicationResponse:
    principal = CandidatePrincipal.from_context(
        user=auth_ctx.user,
        session=auth_ctx.session,
        binding=auth_ctx.binding,
    )
    return withdraw_candidate_application(
        principal=principal,
        application_id=application_id,
        db=db,
    )
