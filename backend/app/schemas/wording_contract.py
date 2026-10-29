import enum
from typing import List, Optional
from uuid import UUID
from pydantic import Field, field_validator, model_validator

from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.phase3_enums import AuthoringOrigin, InterviewLayer, QuestionType, ValidationState
from app.schemas.safety_policy import (
    SAFETY_POLICY_HASH,
    SAFETY_POLICY_ID,
    SAFETY_POLICY_SCHEMA_VERSION,
    SAFETY_POLICY_VERSION,
    check_prohibited_topics,
)

WORDING_REQUEST_SCHEMA_VERSION: str = "1.0.0"
WORDING_RESPONSE_SCHEMA_VERSION: str = "1.0.0"
PROMPT_TEMPLATE_VERSION: str = "1.0.0"


class WordingRationaleCode(str, enum.Enum):
    """Controlled rationale code for wording modification."""
    CLARITY_IMPROVEMENT = "CLARITY_IMPROVEMENT"
    CONCISENESS = "CONCISENESS"
    PROFESSIONAL_TONE = "PROFESSIONAL_TONE"
    STRUCTURE_POLISH = "STRUCTURE_POLISH"
    NEUTRAL_PHRASING = "NEUTRAL_PHRASING"


# =========================================================================
# 1. Recruiter Endpoint Request DTO (Client -> Backend)
# =========================================================================

class RecruiterWordingAssistRequest(FrozenSchema):
    """Recruiter payload to request bounded wording assistance on one draft question.
    
    All authoritative fields (intent, competency, layer, question_type, deterministic text,
    allowed evidence, permitted terminology) are loaded strictly by the server.
    """
    expected_question_version: Optional[int] = Field(default=None, ge=1, description="Optimistic concurrency: expected question version.")
    source_content_hash: Optional[str] = Field(default=None, max_length=64, description="Expected content hash of deterministic source text.")
    max_variants: int = Field(default=1, ge=1, le=3, description="Bounded number of variants requested (1-3).")
    target_tone: Optional[str] = Field(default="PROFESSIONAL", max_length=50)


# =========================================================================
# 2. Strict Internal Bounded Request Contract (Domain -> Provider Adapter)
# =========================================================================

class BoundedWordingRequest(FrozenSchema):
    """Strict bounded wording request contract.
    
    Contains only allowlisted, server-assembled data. Extra fields are forbidden.
    Excludes candidate identity, contact details, full resume, unrestricted JD,
    match score, rank, unrelated requirements, other candidates, credentials,
    provider config, ATS data, and rubric scoring anchors.
    """
    operation: str = Field(default="REWORD_QUESTION", min_length=1, max_length=50)
    request_schema_version: str = Field(default=WORDING_REQUEST_SCHEMA_VERSION)
    prompt_template_version: str = Field(default=PROMPT_TEMPLATE_VERSION)
    policy_version: str = Field(default=SAFETY_POLICY_VERSION)
    policy_hash: str = Field(default=SAFETY_POLICY_HASH, min_length=64, max_length=64)

    source_question_id: UUID
    source_question_version: int = Field(ge=1)
    deterministic_question_text: str = Field(min_length=5, max_length=2000)
    question_intent: str = Field(min_length=5, max_length=1000)
    competency_label: Optional[str] = Field(default=None, max_length=200)
    question_type: QuestionType
    layer: InterviewLayer
    
    permitted_terminology: List[str] = Field(default_factory=list)
    prohibited_topics: List[str] = Field(default_factory=list)
    output_language: str = Field(default="en", max_length=10)
    max_characters: int = Field(default=1000, ge=50, le=2000)
    max_variants: int = Field(default=1, ge=1, le=3)
    require_single_primary_question: bool = True
    allowed_evidence_excerpt: Optional[str] = Field(default=None, max_length=300)
    allowed_reference_ids: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_request_invariants(self) -> "BoundedWordingRequest":
        verify_no_prohibited_keys(self.model_dump())
        
        # Check text safety on input fields
        for field_val in [self.deterministic_question_text, self.question_intent, self.competency_label, self.allowed_evidence_excerpt]:
            if field_val:
                violation = check_prohibited_topics(field_val)
                if violation:
                    raise ValueError(f"Prohibited topic category '{violation[0]}' detected in request field.")
        
        # Supplemental layer evidence restriction
        if self.layer == InterviewLayer.COMMON_CORE and self.allowed_evidence_excerpt is not None:
            raise ValueError("Common core questions must not include evidence excerpts.")

        return self


# =========================================================================
# 3. Strict Provider Response Contract (Provider Model -> Domain Parser)
# =========================================================================

class StructuredWordingVariant(FrozenSchema):
    """One bounded wording proposal."""
    variant_text: str = Field(min_length=5, max_length=2000)
    rationale_code: Optional[WordingRationaleCode] = None
    referenced_identifiers: List[str] = Field(default_factory=list)


class BoundedWordingResponse(FrozenSchema):
    """Strict structured response schema from provider model.
    
    Extra fields are strictly forbidden.
    Must not contain:
    - approval or lifecycle state
    - score, rank or recommendation
    - candidate comparison
    - competency selection
    - requirement changes
    - rubric changes
    - tool calls
    - arbitrary metadata
    """
    operation: str = Field(min_length=1, max_length=50)
    response_schema_version: str = Field(default=WORDING_RESPONSE_SCHEMA_VERSION)
    variants: List[StructuredWordingVariant] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_response_invariants(self) -> "BoundedWordingResponse":
        verify_no_prohibited_keys(self.model_dump())
        if self.operation != "REWORD_QUESTION":
            raise ValueError(f"Unexpected operation in wording response: '{self.operation}'. Expected 'REWORD_QUESTION'.")
        if self.response_schema_version != WORDING_RESPONSE_SCHEMA_VERSION:
            raise ValueError(f"Unsupported response schema version: '{self.response_schema_version}'. Expected '{WORDING_RESPONSE_SCHEMA_VERSION}'.")
        return self


# =========================================================================
# 4. Domain Processing Result DTO (Domain -> Recruiter API Response)
# =========================================================================

class CandidateWordingResult(FrozenSchema):
    """Candidate wording result returned for internal evaluation / Recruiter display.
    
    In Phase 3C.3, this contains the semantic validation outcome and safe validation codes.
    Receiving this result does NOT automatically replace the deterministic question in the DB,
    does NOT approve it, and does NOT alter lifecycle state.
    """
    source_question_id: UUID
    source_question_version: int
    operation: str = "REWORD_QUESTION"
    request_schema_version: str = WORDING_REQUEST_SCHEMA_VERSION
    prompt_template_version: str = PROMPT_TEMPLATE_VERSION
    deterministic_question_text: str
    proposed_variants: List[StructuredWordingVariant]
    allowed_references_checked: bool = True
    is_validated: bool = False
    validation_codes: List[str] = Field(default_factory=list)
    rejection_reasons: List[str] = Field(default_factory=list)
    authoring_origin: AuthoringOrigin = AuthoringOrigin.DETERMINISTIC
    provider_fallback_used: bool = False
    provider_error_code: Optional[str] = None
    safe_error_message: Optional[str] = None
