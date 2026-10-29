from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.interview_blueprint import PolicyVersionReference
from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    ProviderErrorCode,
    ProviderOperation,
    ValidationErrorCode,
    ValidationState,
)
from app.schemas.safety_policy import check_prohibited_topics

CAPABILITY_SCHEMA_VERSION: str = "1.0.0"


class RewordQuestionPayload(FrozenSchema):
    """Bounded payload for REWORD_QUESTION operation."""
    original_text: str = Field(min_length=10, max_length=2000)
    question_intent: str = Field(min_length=5, max_length=1000)
    target_tone: Optional[str] = Field(default="PROFESSIONAL", max_length=50)


class CreateBoundedVariantsPayload(FrozenSchema):
    """Bounded payload for CREATE_BOUNDED_VARIANTS operation."""
    seed_question_text: str = Field(min_length=10, max_length=2000)
    question_intent: str = Field(min_length=5, max_length=1000)
    max_variants: int = Field(default=3, ge=1, le=5)


class StructureProvisionalAssessmentPayload(FrozenSchema):
    """Bounded payload for STRUCTURE_PROVISIONAL_ASSESSMENT operation."""
    question_intent: str = Field(min_length=5, max_length=1000)
    candidate_response_transcript: str = Field(min_length=10, max_length=8000)
    observable_indicators: List[str] = Field(min_length=1)
    rating_anchors: Dict[int, str] = Field(min_length=3)


class SummarizeSelectedEvidencePayload(FrozenSchema):
    """Bounded payload for SUMMARIZE_SELECTED_EVIDENCE operation."""
    selected_evidence_spans: List[str] = Field(min_length=1, max_length=20)
    requirement_context: str = Field(min_length=5, max_length=1000)


class ProviderCapabilityRequest(FrozenSchema):
    """Provider-neutral model capability request envelope.
    
    Strictly forbids arbitrary prompts, tool definitions, provider URLs, or credentials.
    """
    correlation_id: UUID
    operation: ProviderOperation
    policy_reference: PolicyVersionReference = Field(default_factory=PolicyVersionReference)
    schema_version: str = Field(default=CAPABILITY_SCHEMA_VERSION)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Operation payloads (mutually exclusive based on operation)
    reword_payload: Optional[RewordQuestionPayload] = None
    variant_payload: Optional[CreateBoundedVariantsPayload] = None
    assessment_payload: Optional[StructureProvisionalAssessmentPayload] = None
    evidence_payload: Optional[SummarizeSelectedEvidencePayload] = None

    @model_validator(mode="after")
    def validate_operation_payload_pairing(self) -> "ProviderCapabilityRequest":
        verify_no_prohibited_keys(self.model_dump())

        if self.operation == ProviderOperation.REWORD_QUESTION:
            if not self.reword_payload:
                raise ValueError("reword_payload is required for REWORD_QUESTION operation.")
            violation = check_prohibited_topics(self.reword_payload.original_text)
            if violation:
                raise ValueError(f"Prohibited topic '{violation[0]}' detected in request payload.")

        elif self.operation == ProviderOperation.CREATE_BOUNDED_VARIANTS:
            if not self.variant_payload:
                raise ValueError("variant_payload is required for CREATE_BOUNDED_VARIANTS operation.")
            violation = check_prohibited_topics(self.variant_payload.seed_question_text)
            if violation:
                raise ValueError(f"Prohibited topic '{violation[0]}' detected in request payload.")

        elif self.operation == ProviderOperation.STRUCTURE_PROVISIONAL_ASSESSMENT:
            if not self.assessment_payload:
                raise ValueError("assessment_payload is required for STRUCTURE_PROVISIONAL_ASSESSMENT operation.")

        elif self.operation == ProviderOperation.SUMMARIZE_SELECTED_EVIDENCE:
            if not self.evidence_payload:
                raise ValueError("evidence_payload is required for SUMMARIZE_SELECTED_EVIDENCE operation.")

        return self


class ProviderRewordResult(FrozenSchema):
    """Output structure for question rewording."""
    reworded_text: str = Field(min_length=10, max_length=2000)
    intent_preserved: bool = True
    authoring_origin: AuthoringOrigin = AuthoringOrigin.AI_ASSISTED


class ProviderVariantResult(FrozenSchema):
    """Output structure for question variants."""
    variants: List[str] = Field(min_length=1, max_length=5)
    authoring_origin: AuthoringOrigin = AuthoringOrigin.AI_ASSISTED


class ObservableEvidenceNote(FrozenSchema):
    """Provisional observation mapped to rubric indicators."""
    indicator_text: str = Field(min_length=3)
    documented_observation: str = Field(min_length=5, max_length=2000)
    evidence_strength: str = Field(default="MODERATE")


class ProviderAssessmentResult(FrozenSchema):
    """Output structure for provisional rubric observation structuring."""
    structured_observations: List[ObservableEvidenceNote] = Field(min_length=1)
    provisional_anchor_suggestion: Optional[int] = Field(default=None, ge=1, le=5)
    requires_human_evaluation: bool = True


class ProviderEvidenceSummaryResult(FrozenSchema):
    """Output structure for evidence summarization."""
    factual_summary: str = Field(min_length=10, max_length=2000)
    unsupported_claims_found: bool = False


class ProviderCapabilityResponse(FrozenSchema):
    """Provider-neutral model capability response envelope."""
    correlation_id: UUID
    operation: ProviderOperation
    validation_state: ValidationState = ValidationState.PASSED
    error_code: Optional[ProviderErrorCode] = None
    validation_error_code: Optional[ValidationErrorCode] = None
    safe_error_message: Optional[str] = None
    fallback_used: bool = False
    policy_reference: PolicyVersionReference = Field(default_factory=PolicyVersionReference)
    schema_version: str = Field(default=CAPABILITY_SCHEMA_VERSION)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Operation results
    reword_result: Optional[ProviderRewordResult] = None
    variant_result: Optional[ProviderVariantResult] = None
    assessment_result: Optional[ProviderAssessmentResult] = None
    evidence_result: Optional[ProviderEvidenceSummaryResult] = None

    @model_validator(mode="after")
    def validate_response_invariants(self) -> "ProviderCapabilityResponse":
        verify_no_prohibited_keys(self.model_dump())
        if self.validation_state == ValidationState.BLOCKED and not self.validation_error_code and not self.error_code:
            raise ValueError("Blocked responses must provide an error_code or validation_error_code.")
        return self
