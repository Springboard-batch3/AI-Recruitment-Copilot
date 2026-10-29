from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ProcessingJobStatus


class ResumeUploadResponse(BaseModel):
    artifact_id: UUID
    job_id: UUID
    status: ProcessingJobStatus = Field(default=ProcessingJobStatus.QUEUED)

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class ResumeStatusResponse(BaseModel):
    artifact_id: UUID
    job_id: UUID
    detected_format: Optional[ArtifactFormat] = None
    validation_status: ArtifactValidationStatus
    processing_status: ProcessingJobStatus
    attempt_count: int
    safe_error_code: Optional[str] = None

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class ResumeProcessResponse(BaseModel):
    artifact_id: UUID
    job_id: UUID
    extraction_id: UUID
    processing_status: ProcessingJobStatus = Field(default=ProcessingJobStatus.SUCCEEDED)
    review_required: bool
    block_count: int

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class ResumeExtractionBlockResponse(BaseModel):
    block_id: UUID
    sequence_index: int
    page_number: Optional[int] = None
    structural_location: Optional[str] = None
    block_type: str
    extraction_method: str
    raw_text: str
    normalized_text: str
    confidence: Optional[float] = None
    review_required: bool

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class ResumeExtractionResponse(BaseModel):
    artifact_id: UUID
    extraction_id: UUID
    extractor_version: str
    review_required: bool
    block_count: int
    blocks: list[ResumeExtractionBlockResponse]

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class ResumeUploadErrorDetail(BaseModel):
    code: str
    message: str

    model_config = ConfigDict(
        extra="forbid",
    )


class CandidateListItemResponse(BaseModel):
    artifact_id: UUID
    original_filename: str
    detected_format: Optional[ArtifactFormat] = None
    validation_status: ArtifactValidationStatus
    created_at: str
    has_extraction: bool
    latest_profile_version: Optional[int] = None
    latest_profile_id: Optional[UUID] = None
    full_name: Optional[str] = None
    headline: Optional[str] = None
    review_required: bool = False
    provenance: Optional[str] = None

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )
