"""API Router for Candidate Job Discovery (Phase 4B.3).

Provides endpoints for authenticated candidate actors to browse and view eligible jobs.
Enforces explicit candidate session resolution and allowlisted safe responses.
"""
import logging
import uuid
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.auth import (
    CandidateAuthenticatedContext,
    require_candidate,
)
from app.database import get_db
from app.schemas.candidate_job import (
    CandidateJobDetailResponse,
    CandidateJobListResponse,
)
from app.services.candidate_authorization_kernel import CandidatePrincipal
from app.services.candidate_job_service import (
    get_candidate_open_job_detail,
    list_candidate_open_jobs,
)

logger = logging.getLogger("ai_hiring.api.candidate_jobs")

router = APIRouter(
    prefix="/api/v1/candidate/jobs",
    tags=["candidate_jobs"],
)


@router.get(
    "",
    response_model=CandidateJobListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Candidate Open Jobs",
    description="Returns a paginated list of candidate-safe eligible jobs for the authenticated candidate.",
)
async def list_open_jobs(
    q: Optional[str] = Query(None, description="Optional search term for title, department, location, summary"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=50, description="Items per page"),
    auth_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> CandidateJobListResponse:
    principal = CandidatePrincipal.from_context(
        user=auth_ctx.user,
        session=auth_ctx.session,
        binding=auth_ctx.binding,
    )
    return list_candidate_open_jobs(
        principal=principal,
        db=db,
        query=q,
        page=page,
        limit=limit,
    )


@router.get(
    "/{job_id}",
    response_model=CandidateJobDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Candidate Open Job Detail",
    description="Returns full candidate-safe details for an approved job requisition.",
)
async def get_open_job_detail(
    job_id: UUID,
    version_id: Optional[UUID] = Query(None, description="Optional job version UUID for consistency verification"),
    auth_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> CandidateJobDetailResponse:
    principal = CandidatePrincipal.from_context(
        user=auth_ctx.user,
        session=auth_ctx.session,
        binding=auth_ctx.binding,
    )
    return get_candidate_open_job_detail(
        principal=principal,
        job_id=job_id,
        db=db,
        requested_version_id=version_id,
    )
