import logging
import uuid
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path as PathParam,
    UploadFile,
    status,
)

from app.api.auth import AuthenticatedContext, require_role
from app.models.auth import UserRole

logger = logging.getLogger("ai_hiring.api.job_imports")

router = APIRouter(
    prefix="/api/v1/jobs/imports",
    tags=["job-imports"],
)

require_recruiter_or_admin = require_role({UserRole.RECRUITER, UserRole.ADMIN})

_GONE_DETAIL = {
    "code": "FEATURE_DEPRECATED",
    "message": (
        "Job description document import and automated suggestion extraction has been deprecated "
        "and removed. Manual structured job creation is the supported path."
    ),
}


@router.post(
    "/analyze-text",
    status_code=status.HTTP_410_GONE,
    summary="[Deprecated] Analyze Pasted Job Description Text",
)
async def analyze_job_description_text(
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
):
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE_DETAIL)


@router.post(
    "/analyze-file",
    status_code=status.HTTP_410_GONE,
    summary="[Deprecated] Analyze Uploaded Job Description File (.pdf, .docx)",
)
async def analyze_job_description_file(
    file: UploadFile = File(...),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
):
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE_DETAIL)


@router.get(
    "/{session_id}",
    status_code=status.HTTP_410_GONE,
    summary="[Deprecated] Get Job Description Import Session",
)
async def get_import_session(
    session_id: uuid.UUID = PathParam(...),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
):
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE_DETAIL)


@router.patch(
    "/{session_id}/suggestions/{suggestion_id}",
    status_code=status.HTTP_410_GONE,
    summary="[Deprecated] Update Import Suggestion Status",
)
async def update_suggestion_status(
    session_id: uuid.UUID = PathParam(...),
    suggestion_id: str = PathParam(...),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
):
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE_DETAIL)


@router.post(
    "/{session_id}/apply",
    status_code=status.HTTP_410_GONE,
    summary="[Deprecated] Apply Import Suggestions",
)
async def apply_import_suggestions(
    session_id: uuid.UUID = PathParam(...),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
):
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE_DETAIL)


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_410_GONE,
    summary="[Deprecated] Discard Import Session",
)
async def discard_import_session(
    session_id: uuid.UUID = PathParam(...),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
):
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE_DETAIL)
