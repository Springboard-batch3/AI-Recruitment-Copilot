from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.phase3_enums import ATSExportLifecycle

ATS_DTO_SCHEMA_VERSION: str = "1.0.0"


class ATSJobReferenceDTO(FrozenSchema):
    """Canonical ATS job requisition binding."""
    ats_job_id: str = Field(min_length=1, max_length=255)
    platform_job_id: UUID
    platform_job_version: int = Field(ge=1)
    remote_job_code: Optional[str] = None
    title: str = Field(min_length=1, max_length=255)


class ATSCandidateReferenceDTO(FrozenSchema):
    """Canonical ATS candidate binding."""
    ats_candidate_id: str = Field(min_length=1, max_length=255)
    platform_candidate_id: UUID
    platform_profile_version: int = Field(ge=1)
    remote_candidate_code: Optional[str] = None


class ATSApplicationReferenceDTO(FrozenSchema):
    """Canonical ATS job application binding."""
    ats_application_id: str = Field(min_length=1, max_length=255)
    ats_job_id: str = Field(min_length=1, max_length=255)
    ats_candidate_id: str = Field(min_length=1, max_length=255)
    current_stage: Optional[str] = None


class ATSAttachmentMetadataDTO(FrozenSchema):
    """Metadata describing an attachment exported to ATS."""
    attachment_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(ge=1)
    sha256_hash: str = Field(min_length=64, max_length=64)


class ATSAssessmentNotePreviewDTO(FrozenSchema):
    """Preview of recruiter note exported to ATS."""
    note_id: UUID
    application_id: str
    author_display_name: str = Field(min_length=1)
    note_text: str = Field(min_length=10, max_length=10000)
    include_deterministic_summary: bool = True
    include_interview_observations: bool = True
    schema_version: str = Field(default=ATS_DTO_SCHEMA_VERSION)

    @model_validator(mode="after")
    def validate_invariants(self) -> "ATSAssessmentNotePreviewDTO":
        verify_no_prohibited_keys(self.model_dump())
        return self


class ATSPaginatedResultDTO(FrozenSchema):
    """Generic paginated container for ATS entity synchronization."""
    items: List[Dict[str, Any]]
    total_count: int = Field(ge=0)
    page_token: Optional[str] = None
    has_more: bool = False


class ATSSyncCheckpointDTO(FrozenSchema):
    """Checkpoint marker for incremental ATS synchronization."""
    connector_id: str
    last_synced_at: datetime
    checkpoint_token: Optional[str] = None
    records_processed: int = Field(ge=0)


class ATSWriteResultDTO(FrozenSchema):
    """Outcome of an ATS write or export operation."""
    export_job_id: UUID
    ats_record_id: Optional[str] = None
    lifecycle_status: ATSExportLifecycle
    success: bool
    safe_error_code: Optional[str] = None
    safe_error_message: Optional[str] = None
    requires_reconciliation: bool = False
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ATSHealthResultDTO(FrozenSchema):
    """Connectivity and credential health check status."""
    connector_id: str
    is_healthy: bool
    latency_ms: Optional[float] = Field(default=None, ge=0.0)
    safe_status_message: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
