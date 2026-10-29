import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DashboardMetadata(BaseModel):
    generated_at: datetime
    organization_name: str
    data_status: str = "authoritative"
    active_filters: Dict[str, Any] = Field(default_factory=dict)


class DashboardOverviewData(BaseModel):
    metadata: DashboardMetadata
    published_jobs: int
    draft_ready_jobs: int
    submitted_applications: int
    awaiting_review: int
    interview_ready: int
    active_interview_releases: int
    completed_official_interviews: int
    evaluations_available: int


class PipelineStageCount(BaseModel):
    stage: str
    count: int
    description: str


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


class DashboardPipelineData(BaseModel):
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


class DashboardJobsData(BaseModel):
    metadata: DashboardMetadata
    jobs: List[DashboardJobItem]


class DashboardMatchItem(BaseModel):
    job_id: uuid.UUID
    job_title: str
    candidate_id: uuid.UUID
    candidate_display_name: str
    match_score: float
    confidence_tier: str
    must_have_coverage: float
    preferred_coverage: float
    missing_must_have_count: int
    missing_preferred_count: int
    match_created_at: datetime


class DashboardMatchesData(BaseModel):
    metadata: DashboardMetadata
    matches: List[DashboardMatchItem]
    total_count: int


class DashboardInterviewItem(BaseModel):
    job_id: uuid.UUID
    job_title: str
    candidate_id: uuid.UUID
    candidate_display_name: str
    interview_mode: str
    session_status: str
    evaluation_status: str
    submitted_at: Optional[datetime] = None
    evaluated_at: Optional[datetime] = None
    persisted_overall_score: Optional[float] = None
    dimension_scores: Dict[str, float] = Field(default_factory=dict)
    scoring_notes: Optional[str] = None


class DashboardInterviewsData(BaseModel):
    metadata: DashboardMetadata
    interviews: List[DashboardInterviewItem]
    total_count: int


class DashboardReadinessData(BaseModel):
    metadata: DashboardMetadata
    api_status: str
    database_status: str
    local_ai_status: str
    text_interview_status: str
    audio_playback_status: str
    audio_transcription_status: str
    last_checked_at: datetime
    user_safe_message: str
