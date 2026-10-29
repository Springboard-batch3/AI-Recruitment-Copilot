import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedContext, require_recruiter_or_admin, verify_csrf_and_origin
from app.config import Settings, get_settings
from app.database import get_db
from app.schemas.dashboard import (
    DashboardInterviewsResponse,
    DashboardJobsResponse,
    DashboardMatchesResponse,
    DashboardOverviewResponse,
    DashboardPipelineResponse,
    DashboardReadinessResponse,
    DashboardSessionCreateRequest,
    DashboardSessionCreateResponse,
    DashboardSessionExchangeRequest,
    DashboardSessionExchangeResponse,
)
from app.services.dashboard_analytics_service import DashboardAnalyticsService
from app.services.dashboard_session_service import (
    DashboardAuthenticatedContext,
    create_dashboard_ticket,
    exchange_dashboard_ticket,
    verify_dashboard_token,
)

logger = logging.getLogger("ai_hiring.api.dashboard")

router = APIRouter(
    prefix="/api/v1/dashboard",
    tags=["dashboard"],
)


# --- Session Management Endpoints ---

@router.post(
    "/session",
    response_model=DashboardSessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create one-time dashboard launch ticket",
)
def create_session_ticket(
    auth_ctx: AuthenticatedContext = Depends(verify_csrf_and_origin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    Requires an authenticated Recruiter or Admin browser session and CSRF token.
    Rejects Candidate accounts.
    Issues a short-lived (60-120s), single-use hashed ticket for Streamlit launch.
    """
    if auth_ctx.user.role.value not in {"ADMIN", "RECRUITER"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Candidate accounts cannot launch the analytics dashboard."},
        )

    raw_ticket, launch_url, ttl = create_dashboard_ticket(
        db=db,
        user=auth_ctx.user,
        settings=settings,
    )

    return DashboardSessionCreateResponse(
        ticket=raw_ticket,
        launch_url=launch_url,
        expires_in_seconds=ttl,
    )


@router.post(
    "/session/exchange",
    response_model=DashboardSessionExchangeResponse,
    status_code=status.HTTP_200_OK,
    summary="Exchange one-time launch ticket for dashboard bearer token",
)
def exchange_ticket(
    payload: DashboardSessionExchangeRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    Called by Streamlit upon receiving the launch ticket in the URL query string.
    Atomically consumes the ticket and returns a short-lived (15-30m) read-only bearer token.
    Second exchange attempts are strictly rejected with 401.
    """
    token, expires_in, user_id, role, org_id = exchange_dashboard_ticket(
        db=db,
        raw_ticket=payload.ticket,
        settings=settings,
    )

    return DashboardSessionExchangeResponse(
        access_token=token,
        token_type="bearer",
        expires_in_seconds=expires_in,
        user_id=user_id,
        role=role,
        organization_id=org_id,
    )


# --- Analytics Endpoints (Protected by Dashboard Bearer Token) ---

@router.get(
    "/overview",
    response_model=DashboardOverviewResponse,
    summary="Authoritative recruitment pipeline overview",
)
def get_dashboard_overview(
    dash_ctx: DashboardAuthenticatedContext = Depends(verify_dashboard_token),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    service = DashboardAnalyticsService(db=db, organization_id=dash_ctx.organization_id, settings=settings)
    return service.get_overview()


@router.get(
    "/pipeline",
    response_model=DashboardPipelineResponse,
    summary="Pipeline funnel and per-job lifecycle distribution",
)
def get_dashboard_pipeline(
    job_id: Optional[uuid.UUID] = Query(default=None, description="Optional job requisition ID filter"),
    department: Optional[str] = Query(default=None, description="Optional department filter"),
    dash_ctx: DashboardAuthenticatedContext = Depends(verify_dashboard_token),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    service = DashboardAnalyticsService(db=db, organization_id=dash_ctx.organization_id, settings=settings)
    return service.get_pipeline(job_id=job_id, department=department)


@router.get(
    "/jobs",
    response_model=DashboardJobsResponse,
    summary="Job requisition analysis and application breakdown",
)
def get_dashboard_jobs(
    department: Optional[str] = Query(default=None, description="Optional department filter"),
    dash_ctx: DashboardAuthenticatedContext = Depends(verify_dashboard_token),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    service = DashboardAnalyticsService(db=db, organization_id=dash_ctx.organization_id, settings=settings)
    return service.get_jobs(department=department)


@router.get(
    "/matches",
    response_model=DashboardMatchesResponse,
    summary="Deterministic resume-requirement matching insights",
)
def get_dashboard_matches(
    job_id: Optional[uuid.UUID] = Query(default=None, description="Job requisition ID filter"),
    confidence_tier: Optional[str] = Query(default=None, description="HIGH, MEDIUM, or LOW"),
    has_must_have_gaps: Optional[bool] = Query(default=None, description="Filter for must-have gaps"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    dash_ctx: DashboardAuthenticatedContext = Depends(verify_dashboard_token),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    service = DashboardAnalyticsService(db=db, organization_id=dash_ctx.organization_id, settings=settings)
    return service.get_matches(
        job_id=job_id,
        confidence_tier=confidence_tier,
        has_must_have_gaps=has_must_have_gaps,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/interviews",
    response_model=DashboardInterviewsResponse,
    summary="Official interview outcomes with strict Text/Audio separation",
)
def get_dashboard_interviews(
    job_id: Optional[uuid.UUID] = Query(default=None, description="Job requisition ID filter"),
    interview_mode: Optional[str] = Query(default=None, description="TEXT or AUDIO"),
    session_status: Optional[str] = Query(default=None, description="IN_PROGRESS or SUBMITTED"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    dash_ctx: DashboardAuthenticatedContext = Depends(verify_dashboard_token),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    service = DashboardAnalyticsService(db=db, organization_id=dash_ctx.organization_id, settings=settings)
    return service.get_interviews(
        job_id=job_id,
        interview_mode=interview_mode,
        session_status=session_status,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/readiness",
    response_model=DashboardReadinessResponse,
    summary="Local subsystem operational readiness and speech capability",
)
def get_dashboard_readiness(
    dash_ctx: DashboardAuthenticatedContext = Depends(verify_dashboard_token),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    service = DashboardAnalyticsService(db=db, organization_id=dash_ctx.organization_id, settings=settings)
    return service.get_readiness()
