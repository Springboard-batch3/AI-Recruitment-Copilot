import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# --- Session and Ticket Schemas ---

class DashboardSessionCreateRequest(BaseModel):
    pass


class DashboardSessionCreateResponse(BaseModel):
    ticket: str = Field(..., description="Short-lived one-time dashboard launch ticket")
    launch_url: str = Field(..., description="Streamlit URL with the ticket query param")
    expires_in_seconds: int = Field(..., description="Ticket expiration TTL in seconds")


class DashboardSessionExchangeRequest(BaseModel):
    ticket: str = Field(..., description="The one-time launch ticket")


class DashboardSessionExchangeResponse(BaseModel):
    access_token: str = Field(..., description="Short-lived read-only dashboard bearer token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in_seconds: int = Field(..., description="Token expiration TTL in seconds")
    user_id: uuid.UUID = Field(..., description="Authenticated user ID")
    role: str = Field(..., description="User role")
    organization_id: uuid.UUID = Field(..., description="Organization ID")


# --- Analytics Response Schemas ---

class DashboardMetadata(BaseModel):
    generated_at: datetime = Field(..., description="Timestamp of data generation")
    organization_name: str = Field(..., description="Organization display name")
    data_status: str = Field(default="authoritative", description="Status of persisted records")
    active_filters: Dict[str, Any] = Field(default_factory=dict, description="Active filters applied")


class DashboardOverviewResponse(BaseModel):
    metadata: DashboardMetadata
    published_jobs: int = Field(..., description="Total published/released jobs")
    draft_ready_jobs: int = Field(..., description="Total draft or ready jobs")
    submitted_applications: int = Field(..., description="Total submitted candidate applications")
    awaiting_review: int = Field(..., description="Applications in RECEIVED status awaiting recruiter review")
    interview_ready: int = Field(..., description="Applications in INTERVIEW_READY status")
    active_interview_releases: int = Field(..., description="Active interview question releases")
    completed_official_interviews: int = Field(..., description="Submitted official interview sessions")
    evaluations_available: int = Field(..., description="Finalized or completed interview evaluations")


class PipelineStageCount(BaseModel):
    stage: str = Field(..., description="Stage name (e.g., Submitted, In review, Ready for interview, Interview released, Interview completed, Evaluation available)")
    count: int = Field(..., description="Authoritative count of candidates in this stage")
    description: str = Field(..., description="Human-readable definition of the stage")


class JobPipelineSummary(BaseModel):
    job_id: uuid.UUID
    title: str
    department: Optional[str] = None
    location: Optional[str] = None
    publication_status: str
    current_revision: int
    submitted_applications: int
    awaiting_review: int
    interview_ready: int
    interview_released: int
    completed_interviews: int
    evaluations_available: int


class DashboardPipelineResponse(BaseModel):
    metadata: DashboardMetadata
    stages: List[PipelineStageCount]
    jobs: List[JobPipelineSummary]


class DashboardJobItem(BaseModel):
    job_id: uuid.UUID
    title: str
    department: Optional[str] = None
    location: Optional[str] = None
    publication_status: str
    current_revision: int
    submitted_applications: int
    awaiting_review: int
    interview_ready: int
    completed_interviews: int
    evaluations_available: int


class DashboardJobsResponse(BaseModel):
    metadata: DashboardMetadata
    jobs: List[DashboardJobItem]


class DashboardMatchItem(BaseModel):
    job_id: uuid.UUID
    job_title: str
    candidate_id: uuid.UUID
    candidate_display_name: str
    match_score: float = Field(..., description="Deterministic compatibility score (0-100)")
    confidence_tier: str = Field(..., description="HIGH, MEDIUM, or LOW confidence")
    must_have_coverage: float = Field(..., description="Percentage coverage of must-have requirements")
    preferred_coverage: float = Field(..., description="Percentage coverage of preferred requirements")
    missing_must_have_count: int = Field(..., description="Count of missing must-have requirements")
    missing_preferred_count: int = Field(..., description="Count of missing preferred requirements")
    match_created_at: datetime


class DashboardMatchesResponse(BaseModel):
    metadata: DashboardMetadata
    matches: List[DashboardMatchItem]
    total_count: int


class DashboardInterviewItem(BaseModel):
    job_id: uuid.UUID
    job_title: str
    candidate_id: uuid.UUID
    candidate_display_name: str
    interview_mode: str = Field(..., description="TEXT or AUDIO")
    session_status: str = Field(..., description="IN_PROGRESS or SUBMITTED")
    evaluation_status: str = Field(..., description="NOT_STARTED, IN_REVIEW, FINALIZED, or UNAVAILABLE")
    submitted_at: Optional[datetime] = None
    evaluated_at: Optional[datetime] = None
    persisted_overall_score: Optional[float] = Field(None, description="Persisted aggregate score if evaluated; null if unavailable")
    dimension_scores: Dict[str, float] = Field(default_factory=dict, description="Persisted competency/dimension breakdown scores")
    scoring_notes: Optional[str] = Field(None, description="Sanitized evaluation context (e.g. transcript-only note for Audio)")


class DashboardInterviewsResponse(BaseModel):
    metadata: DashboardMetadata
    interviews: List[DashboardInterviewItem]
    total_count: int


class DashboardReadinessResponse(BaseModel):
    metadata: DashboardMetadata
    api_status: str = Field(..., description="AVAILABLE or UNAVAILABLE")
    database_status: str = Field(..., description="AVAILABLE or UNAVAILABLE")
    local_ai_status: str = Field(..., description="AVAILABLE, DISABLED, or ATTENTION_NEEDED")
    text_interview_status: str = Field(..., description="AVAILABLE or UNAVAILABLE")
    audio_playback_status: str = Field(..., description="AVAILABLE or UNAVAILABLE")
    audio_transcription_status: str = Field(..., description="AVAILABLE or UNAVAILABLE")
    last_checked_at: datetime
    user_safe_message: str = Field(..., description="Sanitized informational summary")
