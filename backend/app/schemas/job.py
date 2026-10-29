import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import Field, field_validator

from app.models.job import (
    EmploymentType,
    JobImportSourceType,
    JobImportStatus,
    JobLifecycleStatus,
    JobPublicationStatus,
    JobVersionStatus,
    RequirementCategory,
    RequirementEvidenceType,
    RequirementNecessity,
    RequirementOrigin,
    RequirementPriority,
    WorkplaceType,
)
from app.schemas.candidate import StrictSchema


class RequirementItemRequest(StrictSchema):
    display_order: Optional[int] = Field(default=0, ge=0)
    category: RequirementCategory = RequirementCategory.SKILL
    label: str = Field(min_length=1, max_length=255)
    taxonomy_skill_concept_id: Optional[uuid.UUID] = None
    necessity: RequirementNecessity = RequirementNecessity.MUST_HAVE
    priority: RequirementPriority = RequirementPriority.MEDIUM
    custom_weight_override: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    min_experience_months: Optional[int] = Field(default=None, ge=1)
    recency_years: Optional[int] = Field(default=None, ge=1, le=50)
    accepted_evidence_types: List[RequirementEvidenceType] = Field(default_factory=list)
    accepted_equivalents: List[str] = Field(default_factory=list)
    rationale: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("label")
    @classmethod
    def validate_label(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Requirement label cannot be empty or whitespace.")
        return cleaned

    @field_validator("rationale")
    @classmethod
    def trim_rationale(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            cleaned = v.strip()
            return cleaned if cleaned else None
        return None


class JobRequirementsSaveRequest(StrictSchema):
    expected_revision: int = Field(ge=1)
    requirements: List[RequirementItemRequest] = Field(default_factory=list)


class RequirementItemResponse(StrictSchema):
    requirement_id: uuid.UUID
    display_order: int
    category: RequirementCategory
    label: str
    normalized_label: str
    taxonomy_skill_concept_id: Optional[uuid.UUID] = None
    necessity: RequirementNecessity
    priority: RequirementPriority
    custom_weight_override: Optional[float] = None
    min_experience_months: Optional[int] = None
    recency_years: Optional[int] = None
    accepted_evidence_types: List[str] = Field(default_factory=list)
    accepted_equivalents: List[str] = Field(default_factory=list)
    rationale: Optional[str] = None
    origin: RequirementOrigin
    review_required: bool
    warning_metadata: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class JobReviewWarningResponse(StrictSchema):
    code: str
    message: str
    severity: str
    requirement_id: Optional[uuid.UUID] = None
    rule: Optional[str] = None
    matched_term: Optional[str] = None


class JobCreateRequest(StrictSchema):
    title: str = Field(min_length=1, max_length=255)
    department: Optional[str] = Field(default=None, max_length=100)
    location: Optional[str] = Field(default=None, max_length=255)
    employment_type: Optional[EmploymentType] = None
    workplace_type: Optional[WorkplaceType] = None
    openings_count: Optional[int] = Field(default=None, ge=1)
    internal_reference: Optional[str] = Field(default=None, max_length=100)
    summary: Optional[str] = Field(default=None, max_length=10000)
    responsibilities: Optional[str] = Field(default=None, max_length=50000)
    job_description_text: Optional[str] = Field(default=None, max_length=100000)
    occupation_concept_id: Optional[uuid.UUID] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Title cannot be empty or whitespace.")
        return cleaned

    @field_validator("department", "location", "internal_reference")
    @classmethod
    def trim_optional_strings(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            cleaned = v.strip()
            return cleaned if cleaned else None
        return None

    @field_validator("summary", "responsibilities", "job_description_text")
    @classmethod
    def trim_text_fields(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            cleaned = v.strip()
            return cleaned if cleaned else None
        return None


class JobDraftUpdateRequest(StrictSchema):
    expected_revision: int = Field(ge=1)
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    department: Optional[str] = Field(default=None, max_length=100)
    location: Optional[str] = Field(default=None, max_length=255)
    employment_type: Optional[EmploymentType] = None
    workplace_type: Optional[WorkplaceType] = None
    openings_count: Optional[int] = Field(default=None, ge=1)
    internal_reference: Optional[str] = Field(default=None, max_length=100)
    summary: Optional[str] = Field(default=None, max_length=10000)
    responsibilities: Optional[str] = Field(default=None, max_length=50000)
    job_description_text: Optional[str] = Field(default=None, max_length=100000)
    occupation_concept_id: Optional[uuid.UUID] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            cleaned = v.strip()
            if not cleaned:
                raise ValueError("Title cannot be empty or whitespace.")
            return cleaned
        return None

    @field_validator("department", "location", "internal_reference")
    @classmethod
    def trim_optional_strings(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            cleaned = v.strip()
            return cleaned if cleaned else None
        return None

    @field_validator("summary", "responsibilities", "job_description_text")
    @classmethod
    def trim_text_fields(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            cleaned = v.strip()
            return cleaned if cleaned else None
        return None


class JobVersionSummaryResponse(StrictSchema):
    job_version_id: uuid.UUID
    version_number: int
    version_status: JobVersionStatus
    revision: int
    title: str
    department: Optional[str] = None
    location: Optional[str] = None
    employment_type: Optional[EmploymentType] = None
    workplace_type: Optional[WorkplaceType] = None
    openings_count: Optional[int] = None
    internal_reference: Optional[str] = None
    requirements_count: int = 0
    ready_by_user_id: Optional[uuid.UUID] = None
    ready_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class JobVersionDetailResponse(StrictSchema):
    job_version_id: uuid.UUID
    job_id: uuid.UUID
    version_number: int
    version_status: JobVersionStatus
    revision: int
    title: str
    department: Optional[str] = None
    location: Optional[str] = None
    employment_type: Optional[EmploymentType] = None
    workplace_type: Optional[WorkplaceType] = None
    openings_count: Optional[int] = None
    internal_reference: Optional[str] = None
    summary: Optional[str] = None
    responsibilities: Optional[str] = None
    job_description_text: Optional[str] = None
    occupation_concept_id: Optional[uuid.UUID] = None
    occupation_title: Optional[str] = None
    taxonomy_version: Optional[str] = None
    parser_version: Optional[str] = None
    created_by_user_id: uuid.UUID
    approved_by_user_id: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    ready_by_user_id: Optional[uuid.UUID] = None
    ready_at: Optional[datetime] = None
    requirements: List[RequirementItemResponse] = Field(default_factory=list)
    review_warnings: List[JobReviewWarningResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class JobSummaryResponse(StrictSchema):
    job_id: uuid.UUID
    created_by_user_id: uuid.UUID
    status: JobLifecycleStatus
    publication_status: JobPublicationStatus = JobPublicationStatus.NOT_RELEASED
    released_at: Optional[datetime] = None
    released_by_user_id: Optional[uuid.UUID] = None
    released_job_version_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime
    current_version: JobVersionSummaryResponse


class JobDetailResponse(StrictSchema):
    job_id: uuid.UUID
    created_by_user_id: uuid.UUID
    status: JobLifecycleStatus
    publication_status: JobPublicationStatus = JobPublicationStatus.NOT_RELEASED
    released_at: Optional[datetime] = None
    released_by_user_id: Optional[uuid.UUID] = None
    released_job_version_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime
    current_version: JobVersionDetailResponse


class JobReadinessFindingResponse(StrictSchema):
    code: str
    severity: str
    message: str
    field: Optional[str] = None
    requirement_id: Optional[uuid.UUID] = None
    blocking: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class JobReadinessResponse(StrictSchema):
    job_id: uuid.UUID
    job_version_id: uuid.UUID
    version_number: int
    revision: int
    version_status: JobVersionStatus
    is_ready: bool
    errors: List[JobReadinessFindingResponse] = Field(default_factory=list)
    warnings: List[JobReadinessFindingResponse] = Field(default_factory=list)
    info: List[JobReadinessFindingResponse] = Field(default_factory=list)


class JobMarkReadyRequest(StrictSchema):
    expected_revision: int


class JobCreateSuccessorRequest(StrictSchema):
    from_version_number: Optional[int] = None


class JobImportAnalyzeTextRequest(StrictSchema):
    text: str = Field(min_length=10, max_length=100000)

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 10:
            raise ValueError("Job description text must contain at least 10 non-whitespace characters.")
        return cleaned


class JobSuggestionResponse(StrictSchema):
    suggestion_id: str
    target_field: str
    proposed_value: Any
    normalized_value: Optional[str] = None
    source_excerpt: str
    start_offset: int
    end_offset: int
    extraction_method: str
    confidence: str = "HIGH"
    review_status: str = "PENDING"


class JobImportWarningResponse(StrictSchema):
    code: str
    message: str
    source_excerpt: Optional[str] = None
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    rule: Optional[str] = None
    matched_term: Optional[str] = None


class JobImportAnalyzeResponse(StrictSchema):
    session_id: uuid.UUID
    job_id: Optional[uuid.UUID] = None
    job_version_id: Optional[uuid.UUID] = None
    expected_revision: Optional[int] = None
    source_type: JobImportSourceType
    source_filename: Optional[str] = None
    source_text: str
    parser_version: str
    taxonomy_version: Optional[str] = None
    status: JobImportStatus
    suggestions: List[JobSuggestionResponse] = Field(default_factory=list)
    warnings: List[JobImportWarningResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class JobImportUpdateSuggestionRequest(StrictSchema):
    review_status: str
    edited_value: Optional[Any] = None


class JobImportApplyRequest(StrictSchema):
    expected_revision: Optional[int] = None
    accepted_suggestion_ids: List[str] = Field(default_factory=list)
    edited_suggestions: Optional[Dict[str, Any]] = None

