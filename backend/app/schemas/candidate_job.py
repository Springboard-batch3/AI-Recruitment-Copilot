"""Candidate-Safe Job Response Schemas (Phase 4B.3).

Provides allowlisted DTOs for candidate job discovery.
Strictly excludes recruiter notes, scoring parameters, AI prompts, candidate rankings, and draft revisions.
"""
import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import Field

from app.models.job import (
    EmploymentType,
    JobVersionStatus,
    WorkplaceType,
)
from app.schemas.candidate import StrictSchema


class CandidateJobSummaryResponse(StrictSchema):
    """Safe, allowlisted summary of an approved job posting for candidate discovery."""
    job_id: uuid.UUID
    job_version_id: uuid.UUID
    version_number: int
    revision: int
    title: str
    department: Optional[str] = None
    location: Optional[str] = None
    employment_type: Optional[EmploymentType] = None
    workplace_type: Optional[WorkplaceType] = None
    openings_count: Optional[int] = None
    summary: Optional[str] = None
    published_at: datetime
    updated_at: datetime


class CandidateJobDetailResponse(StrictSchema):
    """Safe, allowlisted full details of an approved job posting for candidate discovery."""
    job_id: uuid.UUID
    job_version_id: uuid.UUID
    version_number: int
    revision: int
    title: str
    department: Optional[str] = None
    location: Optional[str] = None
    employment_type: Optional[EmploymentType] = None
    workplace_type: Optional[WorkplaceType] = None
    openings_count: Optional[int] = None
    summary: Optional[str] = None
    responsibilities: Optional[str] = None
    qualifications: List[str] = Field(
        default_factory=list,
        description="Public qualification labels extracted from approved job requirements.",
    )
    published_at: datetime
    updated_at: datetime
    has_active_application: bool = Field(
        default=False,
        description="Whether the authenticated candidate currently has an active SUBMITTED application for this job.",
    )
    active_application_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Application ID of active application if one exists for the candidate.",
    )


class CandidateJobListResponse(StrictSchema):
    """Paginated list of candidate-safe eligible jobs."""
    items: List[CandidateJobSummaryResponse] = Field(default_factory=list)
    total: int
    page: int
    limit: int
