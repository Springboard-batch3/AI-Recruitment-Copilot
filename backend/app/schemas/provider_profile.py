from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.phase3_enums import (
    PrivacyReviewStatus,
    ProviderApiMode,
    ProviderCompatibilityStatus,
    ProviderEgressClass,
    ProviderOperation,
    ProviderType,
)

PROVIDER_PROFILE_SCHEMA_VERSION: str = "1.0.0"


class ProviderProfile(FrozenSchema):
    """Immutable provider profile configuration.
    
    Contains server-owned destination URLs and secret references.
    Never stores or serializes plaintext secrets or API keys.
    """
    profile_id: UUID = Field(default_factory=uuid4)
    organization_id: Optional[UUID] = None
    display_name: str = Field(min_length=2, max_length=100)
    provider_type: ProviderType = ProviderType.DISABLED
    api_mode: ProviderApiMode = ProviderApiMode.CHAT_COMPLETIONS
    base_url: Optional[str] = Field(default=None, max_length=1000)
    model_identifier: Optional[str] = Field(default=None, max_length=255)
    secret_ref: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Controlled reference to secret (e.g. 'env:LLM_API_KEY'), never plaintext."
    )
    supported_operations: List[ProviderOperation] = Field(default_factory=list)
    connect_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    request_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    max_input_tokens: int = Field(default=4096, gt=0, le=128000)
    max_output_tokens: int = Field(default=600, gt=0, le=8192)
    max_response_bytes: int = Field(default=262144, gt=0, le=52428800)  # default 256KB, max 50MB
    max_concurrency: int = Field(default=1, ge=1, le=20)
    retry_limit: int = Field(default=0, ge=0, le=3)
    egress_class: ProviderEgressClass = ProviderEgressClass.LOCAL_ONLY
    privacy_review_status: PrivacyReviewStatus = PrivacyReviewStatus.PENDING_REVIEW
    enabled: bool = False
    is_archived: bool = False
    compatibility_status: ProviderCompatibilityStatus = ProviderCompatibilityStatus.NOT_TESTED
    tested_at: Optional[datetime] = None
    model_source: Optional[str] = Field(default=None, max_length=255, description="Owner-supplied model origin (e.g. HuggingFace repo/file)")
    model_licence: Optional[str] = Field(default=None, max_length=100, description="Owner-supplied model license tag")
    model_checksum: Optional[str] = Field(default=None, max_length=128, description="Owner-supplied SHA256 checksum")
    last_compatibility_report: Optional[dict] = Field(default=None, description="Safe diagnostic summary from last compatibility run")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1, ge=1)
    schema_version: str = Field(default=PROVIDER_PROFILE_SCHEMA_VERSION)

    @field_validator("secret_ref")
    @classmethod
    def validate_secret_ref_format(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return None
        if not cleaned.startswith("env:"):
            raise ValueError("secret_ref must use the controlled format 'env:<VARIABLE_NAME>'.")
        var_name = cleaned.split("env:", 1)[1].strip()
        if not var_name or not var_name.isidentifier():
            raise ValueError(f"secret_ref contains invalid environment variable name: '{var_name}'.")
        return cleaned

    @model_validator(mode="after")
    def validate_profile_invariants(self) -> "ProviderProfile":
        verify_no_prohibited_keys(self.model_dump())

        # Disallow raw secrets in secret_ref
        if self.secret_ref:
            lower_ref = self.secret_ref.lower()
            if any(lower_ref.startswith(prefix) for prefix in ["sk-", "bearer ", "token ", "key-"]):
                raise ValueError("Plaintext API key detected in secret_ref. Must use 'env:<VAR>' reference format.")

        # Local vs Remote Egress alignment
        if self.provider_type == ProviderType.LOCAL_OPENAI_COMPATIBLE:
            if self.egress_class != ProviderEgressClass.LOCAL_ONLY:
                raise ValueError("LOCAL_OPENAI_COMPATIBLE provider must have egress_class set to LOCAL_ONLY.")

        if self.provider_type == ProviderType.REMOTE_OPENAI_COMPATIBLE:
            if self.egress_class != ProviderEgressClass.APPROVED_REMOTE:
                raise ValueError("REMOTE_OPENAI_COMPATIBLE provider must have egress_class set to APPROVED_REMOTE.")

        return self


class ProviderEligibilityResult(FrozenSchema):
    """Evaluation of whether a provider profile is eligible for dispatch."""
    is_eligible: bool
    rejection_reasons: List[str] = Field(default_factory=list)
    egress_class: ProviderEgressClass
    provider_type: ProviderType
    display_name: str
