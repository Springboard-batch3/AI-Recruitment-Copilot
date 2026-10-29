import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedContext, require_recruiter
from app.config import Settings, get_settings
from app.database import get_db
from app.schemas.candidate_access import (
    CandidateAccessStatusResponse,
    CandidateInvitationSummary,
    CreateCandidateInvitationRequest,
    CreateCandidateInvitationResponse,
)
from app.services.candidate_access_service import (
    create_candidate_invitation,
    get_candidate_access_status,
    list_candidate_invitations_summary,
    revoke_candidate_access,
    revoke_candidate_invitation,
)

logger = logging.getLogger("ai_hiring.api.candidate_invitations")

router = APIRouter(
    prefix="/api/v1/recruiter",
    tags=["candidate_access", "candidate_invitations"],
    dependencies=[Depends(require_recruiter)],
)


# --- Canonical Candidate Access Endpoints ---
@router.get(
    "/candidate-access/candidates/{candidate_id}",
    response_model=CandidateAccessStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Candidate Access and Account Binding Status",
)
async def get_candidate_access_status_endpoint(
    candidate_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> CandidateAccessStatusResponse:
    return get_candidate_access_status(
        db=db,
        candidate_id=candidate_id,
        recruiter_user=auth_ctx.user,
    )


@router.post(
    "/candidate-access/candidates/{candidate_id}/revoke",
    response_model=CandidateAccessStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke Candidate Access and Suspend Account",
)
async def revoke_candidate_access_endpoint(
    candidate_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> CandidateAccessStatusResponse:
    return revoke_candidate_access(
        db=db,
        candidate_id=candidate_id,
        recruiter_user=auth_ctx.user,
    )


@router.post(
    "/candidate-access/candidates/{candidate_id}/invitations",
    status_code=status.HTTP_410_GONE,
    summary="Create Single-Use Candidate Access Invitation (Obsolete)",
)
async def create_candidate_access_invitation_endpoint(
    candidate_id: uuid.UUID,
) -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "INVITATIONS_DEPRECATED",
            "message": "Recruiter-provisioned candidate invitations have been deprecated. Candidates can register directly via the portal.",
        },
    )


@router.post(
    "/candidate-access/invitations/{invitation_id}/revoke",
    response_model=CandidateInvitationSummary,
    status_code=status.HTTP_200_OK,
    summary="Revoke Unused Candidate Invitation",
)
async def revoke_candidate_access_invitation_endpoint(
    invitation_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> CandidateInvitationSummary:
    inv = revoke_candidate_invitation(
        db=db,
        recruiter_user=auth_ctx.user,
        invitation_id=invitation_id,
    )
    return CandidateInvitationSummary(
        invitation_id=inv.invitation_id,
        candidate_id=inv.candidate_id,
        organization_id=inv.organization_id,
        purpose=inv.purpose,
        status="REVOKED",
        expires_at=inv.expires_at,
        consumed_at=inv.consumed_at,
        revoked_at=inv.revoked_at,
        created_by_user_id=inv.created_by_user_id,
        created_at=inv.created_at,
    )


# --- Legacy / Aliased Candidate Invitations Endpoints ---
@router.post(
    "/candidate-invitations",
    status_code=status.HTTP_410_GONE,
    summary="Create Single-Use Candidate Invitation (Obsolete)",
)
async def create_invitation() -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "INVITATIONS_DEPRECATED",
            "message": "Recruiter-provisioned candidate invitations have been deprecated. Candidates can register directly via the portal.",
        },
    )


@router.get(
    "/candidate-invitations/{candidate_id}",
    response_model=List[CandidateInvitationSummary],
    status_code=status.HTTP_200_OK,
    summary="List Safe Invitation History for Candidate (Legacy)",
)
async def get_candidate_invitations(
    candidate_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> List[CandidateInvitationSummary]:
    return list_candidate_invitations_summary(db=db, candidate_id=candidate_id)


@router.post(
    "/candidate-invitations/{invitation_id}/revoke",
    response_model=CandidateInvitationSummary,
    status_code=status.HTTP_200_OK,
    summary="Revoke Unused Candidate Invitation (Legacy)",
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> CandidateInvitationSummary:
    inv = revoke_candidate_invitation(
        db=db,
        recruiter_user=auth_ctx.user,
        invitation_id=invitation_id,
    )
    return CandidateInvitationSummary(
        invitation_id=inv.invitation_id,
        candidate_id=inv.candidate_id,
        organization_id=inv.organization_id,
        purpose=inv.purpose,
        status="REVOKED",
        expires_at=inv.expires_at,
        consumed_at=inv.consumed_at,
        revoked_at=inv.revoked_at,
        created_by_user_id=inv.created_by_user_id,
        created_at=inv.created_at,
    )
