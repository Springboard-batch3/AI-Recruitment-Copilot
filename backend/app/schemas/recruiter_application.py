"""Recruiter-Safe Application Response and Request Schemas (Phase 4B.5).

Provides allowlisted DTOs for recruiter applicant listing, application detail,
review state transitions, match/gap summary, and interview release handoff.
Strictly isolates recruiter organization and job context.
Candidate-facing DTOs never inherit or expose these fields.
"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import Field

from app.models.candidate_access import CandidateApplicationStatus, RecruiterReviewState
from app.schemas.candidate import StrictSchema


class RecruiterReviewStateUpdateRequest(StrictSchema):
    """Payload for recruiter review state transition."""
    new_state: RecruiterReviewState = Field(
        description="Target recruiter review state (RECEIVED -> IN_REVIEW -> INTERVIEW_READY)."
    )
    expected_state: Optional[RecruiterReviewState] = Field(
        default=None,
        description="Optional expected state for optimistic concurrency protection.",
    )


class RecruiterApplicationSummaryResponse(StrictSchema):
    """Safe allowlisted summary of a candidate application for the recruiter applicant list."""
    application_id: uuid.UUID
    job_id: uuid.UUID
    job_version_id: uuid.UUID
    job_revision: int
    candidate_id: uuid.UUID
    profile_id: uuid.UUID
    profile_version: int
    candidate_name: str
    candidate_email: Optional[str] = None
    candidate_phone: Optional[str] = None
    current_role: Optional[str] = None
    status: CandidateApplicationStatus
    recruiter_review_state: RecruiterReviewState
    submitted_at: datetime
    withdrawn_at: Optional[datetime] = None
    # Deterministic match status summary
    match_status: str = Field(
        description="Evaluated or Not evaluated"
    )
    match_score: Optional[float] = None
    confidence_tier: Optional[str] = None
    # Interview readiness & release status summary
    interview_status: str = Field(
        description="NOT_RELEASED, RELEASED, or NO_APPROVED_SET"
    )
    release_id: Optional[uuid.UUID] = None
    interview_mode: Optional[str] = Field(
        default="TEXT",
        description="TEXT or AUDIO interview mode for release status.",
    )


class RecruiterApplicationListResponse(StrictSchema):
    """Paginated list of applications for a specific job requisition."""
    items: List[RecruiterApplicationSummaryResponse] = Field(default_factory=list)
    total: int
    page: int
    limit: int
    active_count: int
    withdrawn_count: int


class RecruiterMatchEvidenceDto(StrictSchema):
    """Deterministic match and skill gap evidence if previously computed."""
    run_id: uuid.UUID
    rank: int
    raw_score: float
    display_score: float
    confidence_tier: str
    warning_count: int
    must_have_warning_count: int
    is_tied: bool
    score_decomposition: Dict[str, Any] = Field(default_factory=dict)
    computed_at: datetime


class RecruiterInterviewReleaseEligibilityDto(StrictSchema):
    """Eligibility diagnostic for interview release."""
    is_eligible: bool
    reason_code: Optional[str] = None
    message: str
    interview_mode: Optional[str] = Field(
        default="TEXT",
        description="TEXT or AUDIO mode for this question set / release.",
    )
    question_set_id: Optional[uuid.UUID] = None
    set_version: Optional[int] = None
    question_count: int = 0
    existing_release_id: Optional[uuid.UUID] = None
    existing_release_status: Optional[str] = None


class RecruiterApplicationDetailResponse(StrictSchema):
    """Full detail view for a recruiter reviewing an application."""
    application_id: uuid.UUID
    job_id: uuid.UUID
    job_title: str
    job_version_id: uuid.UUID
    job_revision: int
    candidate_id: uuid.UUID
    profile_id: uuid.UUID
    profile_version: int
    candidate_name: str
    candidate_email: Optional[str] = None
    candidate_phone: Optional[str] = None
    current_role: Optional[str] = None
    status: CandidateApplicationStatus
    recruiter_review_state: RecruiterReviewState
    submitted_at: datetime
    withdrawn_at: Optional[datetime] = None
    # Profile Snapshot Summary
    profile_skills: List[str] = Field(default_factory=list)
    profile_headline: Optional[str] = None
    # Existing Match Evidence (if evaluated)
    match_evidence: Optional[RecruiterMatchEvidenceDto] = None
    # Interview readiness / release diagnostics
    interview_eligibility: RecruiterInterviewReleaseEligibilityDto


class RecruiterReleaseInterviewRequest(StrictSchema):
    """Payload to release interview for an application."""
    ai_set_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Optional explicit approved question set ID matching the submitted revision.",
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Optional idempotency key to protect against duplicate requests.",
    )


class RecruiterReleaseInterviewResponse(StrictSchema):
    """Result of interview release for an application."""
    release_id: uuid.UUID
    application_id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    job_title: str
    job_revision: int
    interview_mode: str = Field(
        default="TEXT",
        description="TEXT or AUDIO mode of the released interview.",
    )
    ai_set_id: uuid.UUID
    set_version: int
    status: str
    released_at: datetime
    message: str
