from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.phase3_enums import (
    PrivacyReviewStatus,
    ProviderApiMode,
    ProviderCompatibilityStatus,
    ProviderEgressClass,
    ProviderOperation,
    ProviderType,
)

PROVIDER_ADMIN_SCHEMA_VERSION: str = "1.0.0"


class CreateProviderProfileRequest(BaseModel):
    """Command schema for creating a provider profile (Admin only).
    
    Strictly forbids mass-assignment fields (e.g. organization_id, profile_id,
    enabled, compatibility_status, tested_at, resolved_secret, privacy_reviewed_by).
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: str = Field(min_length=2, max_length=100)
    provider_type: ProviderType = ProviderType.LOCAL_OPENAI_COMPATIBLE
    api_mode: ProviderApiMode = ProviderApiMode.CHAT_COMPLETIONS
    base_url: Optional[str] = Field(default=None, max_length=1000)
    model_identifier: Optional[str] = Field(default=None, max_length=255)
    secret_ref: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Controlled reference to secret in format 'env:<VARIABLE_NAME>', never plaintext."
    )
    supported_operations: List[ProviderOperation] = Field(
        default_factory=lambda: [
            ProviderOperation.REWORD_QUESTION,
            ProviderOperation.CREATE_BOUNDED_VARIANTS,
        ]
    )
    connect_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    request_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    max_input_tokens: int = Field(default=4096, gt=0, le=128000)
    max_output_tokens: int = Field(default=600, gt=0, le=8192)
    max_response_bytes: int = Field(default=262144, gt=0, le=52428800)
    max_concurrency: int = Field(default=1, ge=1, le=20)
    retry_limit: int = Field(default=0, ge=0, le=3)
    egress_class: ProviderEgressClass = ProviderEgressClass.LOCAL_ONLY
    model_source: Optional[str] = Field(default=None, max_length=255, description="Owner-supplied source repository/file")
    model_licence: Optional[str] = Field(default=None, max_length=100, description="Owner-supplied model license")
    model_checksum: Optional[str] = Field(default=None, max_length=128, description="Owner-supplied SHA256 checksum")

    @field_validator("secret_ref")
    @classmethod
    def validate_secret_ref(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return None
        if not cleaned.startswith("env:"):
            raise ValueError("secret_ref must use format 'env:<VARIABLE_NAME>'.")
        var_name = cleaned.split("env:", 1)[1].strip()
        if not var_name or not var_name.isidentifier():
            raise ValueError(f"secret_ref contains invalid environment variable name: '{var_name}'.")
        return cleaned

    @model_validator(mode="after")
    def validate_request_invariants(self) -> "CreateProviderProfileRequest":
        if self.secret_ref:
            lower = self.secret_ref.lower()
            if any(lower.startswith(p) for p in ["sk-", "bearer ", "token ", "key-"]):
                raise ValueError("Plaintext API key detected in secret_ref. Must use 'env:<VAR>' reference format.")

        if self.provider_type == ProviderType.LOCAL_OPENAI_COMPATIBLE:
            if self.egress_class != ProviderEgressClass.LOCAL_ONLY:
                raise ValueError("LOCAL_OPENAI_COMPATIBLE provider must have egress_class set to LOCAL_ONLY.")

        if self.provider_type == ProviderType.REMOTE_OPENAI_COMPATIBLE:
            if self.egress_class != ProviderEgressClass.APPROVED_REMOTE:
                raise ValueError("REMOTE_OPENAI_COMPATIBLE provider must have egress_class set to APPROVED_REMOTE.")

        return self


class UpdateProviderProfileRequest(BaseModel):
    """Command schema for updating an inactive provider profile (Admin only)."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1, description="Expected current profile version for optimistic concurrency.")
    display_name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    provider_type: Optional[ProviderType] = None
    api_mode: Optional[ProviderApiMode] = None
    base_url: Optional[str] = Field(default=None, max_length=1000)
    model_identifier: Optional[str] = Field(default=None, max_length=255)
    secret_ref: Optional[str] = Field(default=None, max_length=255)
    supported_operations: Optional[List[ProviderOperation]] = None
    connect_timeout_seconds: Optional[float] = Field(default=None, gt=0.0, le=60.0)
    request_timeout_seconds: Optional[float] = Field(default=None, gt=0.0, le=300.0)
    max_input_tokens: Optional[int] = Field(default=None, gt=0, le=128000)
    max_output_tokens: Optional[int] = Field(default=None, gt=0, le=8192)
    max_response_bytes: Optional[int] = Field(default=None, gt=0, le=52428800)
    max_concurrency: Optional[int] = Field(default=None, ge=1, le=20)
    retry_limit: Optional[int] = Field(default=None, ge=0, le=3)
    egress_class: Optional[ProviderEgressClass] = None
    model_source: Optional[str] = Field(default=None, max_length=255)
    model_licence: Optional[str] = Field(default=None, max_length=100)
    model_checksum: Optional[str] = Field(default=None, max_length=128)

    @field_validator("secret_ref")
    @classmethod
    def validate_secret_ref(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return None
        if not cleaned.startswith("env:"):
            raise ValueError("secret_ref must use format 'env:<VARIABLE_NAME>'.")
        var_name = cleaned.split("env:", 1)[1].strip()
        if not var_name or not var_name.isidentifier():
            raise ValueError(f"secret_ref contains invalid environment variable name: '{var_name}'.")
        return cleaned


class ActivateProviderProfileRequest(BaseModel):
    """Command schema for activating a provider profile."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1, description="Expected profile version.")
    reason: Optional[str] = Field(default=None, max_length=500, description="Reason for activation.")
    confirm_remote_egress: bool = Field(
        default=False,
        description="Explicit confirmation required when activating an APPROVED_REMOTE provider."
    )


class DisableProviderProfileRequest(BaseModel):
    """Command schema for disabling a provider profile."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1, description="Expected profile version.")
    reason: Optional[str] = Field(default=None, max_length=500, description="Reason for disabling.")


class ArchiveProviderProfileRequest(BaseModel):
    """Command schema for archiving or unarchiving a provider profile."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1, description="Expected profile version.")
    archive: bool = Field(default=True, description="True to archive, False to restore.")


class SelectProviderModelRequest(BaseModel):
    """Command schema for directly updating the selected model on a provider profile."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_identifier: str = Field(min_length=1, max_length=255, description="Exact discovered or configured model identifier.")


class TestProviderCompatibilityRequest(BaseModel):
    """Command schema for executing an Admin-only compatibility check."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1, description="Expected profile version.")
    timeout_override_seconds: Optional[float] = Field(default=None, gt=0.0, le=60.0)


class ProviderCompatibilityReportResponse(FrozenSchema):
    """Safe diagnostic report of an Admin compatibility test run.
    
    Contains strictly non-confidential test metadata, latency metrics, and discovered capabilities.
    Zero exposure of API keys, Authorization headers, or full prompt/completion bodies.
    """
    profile_id: UUID
    display_name: str
    provider_type: ProviderType
    model_identifier: Optional[str] = None
    compatibility_status: ProviderCompatibilityStatus
    is_compatible: bool
    safe_failure_code: Optional[str] = None
    safe_failure_message: Optional[str] = None
    reachability_passed: bool
    auth_passed: bool
    model_discovered: bool
    discovered_model_ids: List[str] = Field(default_factory=list)
    dry_run_completion_passed: bool
    question_wording_passed: bool = False
    non_streaming_enforced: bool
    unicode_handling_passed: bool
    recommended_action: Optional[str] = None
    latency_ms: float
    tested_at: datetime
    model_source: Optional[str] = None
    model_licence: Optional[str] = None
    model_checksum: Optional[str] = None
    suite_version: str = "1.0.0"


class AdminProviderProfileResponse(FrozenSchema):
    """Safe provider profile representation for Administrator management.
    
    Contains sanitized URLs and masked secret references.
    Never exposes resolved secrets or plaintext API keys.
    """
    profile_id: UUID
    organization_id: Optional[UUID] = None
    display_name: str
    provider_type: ProviderType
    api_mode: ProviderApiMode
    base_url_masked: Optional[str] = None
    model_identifier: Optional[str] = None
    secret_status: str = Field(description="CONFIGURED | MISSING | NOT_REQUIRED")
    secret_ref_name: Optional[str] = None
    supported_operations: List[ProviderOperation] = Field(default_factory=list)
    connect_timeout_seconds: float
    request_timeout_seconds: float
    max_input_tokens: int
    max_output_tokens: int
    max_response_bytes: int
    max_concurrency: int
    retry_limit: int
    egress_class: ProviderEgressClass
    privacy_review_status: PrivacyReviewStatus
    enabled: bool
    is_archived: bool = False
    compatibility_status: ProviderCompatibilityStatus
    tested_at: Optional[datetime] = None
    model_source: Optional[str] = None
    model_licence: Optional[str] = None
    model_checksum: Optional[str] = None
    last_compatibility_report: Optional[dict] = None
    version: int
    created_at: datetime
    updated_at: datetime


class RecruiterAIAvailabilityResponse(FrozenSchema):
    """Minimal, safe AI capability descriptor for Recruiters.
    
    Zero exposure of destination URLs, ports, internal paths, secret references,
    environment variable names, or internal diagnostic errors.
    """
    ai_enabled: bool
    provider_class: str = Field(description="DISABLED | LOCAL | REMOTE")
    availability_label: str
    egress_class: ProviderEgressClass
    active_model_name: Optional[str] = None
    message: Optional[str] = None


class InterviewContextResponse(FrozenSchema):
    """Safe response for the Recruiter Interview Workspace Shell."""
    job_id: UUID
    job_title: str
    job_version_id: UUID
    job_revision: int
    candidate_id: Optional[UUID] = None
    candidate_name: Optional[str] = None
    candidate_email: Optional[str] = None
    resume_artifact_id: Optional[UUID] = None
    match_run_id: Optional[UUID] = None
    match_score: Optional[float] = None
    skill_gap_summary: Optional[str] = None
    ai_capability: RecruiterAIAvailabilityResponse
    interview_plan_state: str = "EMPTY_PLAN"
    message: str = "Interview workspace ready. Standardized core competencies and supplemental probes will be generated in Phase 3B."
