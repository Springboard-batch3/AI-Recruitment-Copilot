"""Phase 3D.2: Bounded Provisional Assessment Contract.

Defines strict, narrow domain contracts for AI provisional assessment of
submitted interview simulation answers against authoritative anchored rubrics.
"""

import enum
import re
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import Field, field_validator, model_validator

from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.safety_policy import (
    SAFETY_POLICY_HASH,
    SAFETY_POLICY_VERSION,
    check_prohibited_topics,
)

ASSESSMENT_REQUEST_SCHEMA_VERSION: str = "1.0.0"
ASSESSMENT_RESPONSE_SCHEMA_VERSION: str = "1.0.0"
ASSESSMENT_PROMPT_VERSION: str = "1.0.0"

# Explicit prohibited recommendation keywords in output
PROHIBITED_RECOMMENDATION_KEYWORDS = [
    r"\bhire\b",
    r"\bdo not hire\b",
    r"\breject\b",
    r"\badvance\b",
    r"\bpass\b",
    r"\bfail\b",
    r"\bcandidate should be hired\b",
    r"\bstrongly recommend\b",
    r"\bweak candidate\b",
    r"\bculture fit\b",
    r"\bpersonality\b",
    r"\bhonest\b",
    r"\bdishonest\b",
    r"\bliar\b",
    r"\btrustworthy\b",
]


class AssessmentStatus(str, enum.Enum):
    """Provisional assessment status classification."""
    PROVISIONAL = "PROVISIONAL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class BoundedAssessmentSegment(FrozenSchema):
    """Segment representation sent to provider model."""
    segment_id: str = Field(min_length=1, max_length=64)
    sequence_order: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=2000)


class BoundedRubricPayload(FrozenSchema):
    """Authoritative anchored rubric data provided to model."""
    rubric_id: str = Field(min_length=1, max_length=64)
    rubric_version: int = Field(ge=1)
    competency_definition: str = Field(min_length=5, max_length=1000)
    question_intent: str = Field(min_length=5, max_length=1000)
    observable_indicators: List[str] = Field(min_length=1)
    rating_anchors: Dict[int, str] = Field(min_length=1)
    allowed_levels: List[int] = Field(min_length=1)
    sufficient_evidence_guidance: Optional[str] = Field(default=None, max_length=1000)
    insufficient_evidence_guidance: Optional[str] = Field(default=None, max_length=1000)
    prohibited_inferences: List[str] = Field(default_factory=list)


class BoundedAssessmentRequest(FrozenSchema):
    """Strict bounded provisional assessment request contract.
    
    Contains only allowlisted, server-assembled rubric and opaque segment data.
    Excludes candidate identity, contact details, resume, Phase 2 rank, ATS info,
    other candidates, and credentials.
    """
    operation: str = Field(default="PROVISIONAL_ASSESSMENT", min_length=1, max_length=50)
    request_schema_version: str = Field(default=ASSESSMENT_REQUEST_SCHEMA_VERSION)
    prompt_template_version: str = Field(default=ASSESSMENT_PROMPT_VERSION)
    policy_version: str = Field(default=SAFETY_POLICY_VERSION)
    policy_hash: str = Field(default=SAFETY_POLICY_HASH, min_length=64, max_length=64)

    attempt_id: UUID
    question_id: UUID
    question_text: str = Field(min_length=5, max_length=2000)
    rubric: BoundedRubricPayload
    answer_segments: List[BoundedAssessmentSegment] = Field(min_length=1)
    citation_allowlist: List[str] = Field(min_length=1)
    prohibited_inferences: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_request_invariants(self) -> "BoundedAssessmentRequest":
        verify_no_prohibited_keys(self.model_dump())
        
        # Check text safety
        violation = check_prohibited_topics(self.question_text)
        if violation:
            raise ValueError(f"Prohibited topic '{violation[0]}' in question text.")
        
        violation = check_prohibited_topics(self.rubric.competency_definition)
        if violation:
            raise ValueError(f"Prohibited topic '{violation[0]}' in rubric competency definition.")

        return self


class StructuredCriterionObservation(FrozenSchema):
    """Single observation mapped to a rubric criterion and cited to answer segments."""
    criterion_id: str = Field(min_length=1, max_length=100)
    observation: str = Field(min_length=5, max_length=2000)
    cited_segment_ids: List[str] = Field(min_length=1)


class BoundedAssessmentResponse(FrozenSchema):
    """Strict provider provisional assessment response contract."""
    operation: str = Field(default="PROVISIONAL_ASSESSMENT")
    response_schema_version: str = Field(default=ASSESSMENT_RESPONSE_SCHEMA_VERSION)
    provisional_level: Optional[int] = Field(default=None)
    insufficient_evidence_reason: Optional[str] = Field(default=None, max_length=200)
    criterion_observations: List[StructuredCriterionObservation] = Field(default_factory=list)
    cited_segment_ids: List[str] = Field(default_factory=list)
    uncertainty_codes: List[str] = Field(default_factory=list)
    safe_validation_metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_response_invariants(self) -> "BoundedAssessmentResponse":
        verify_no_prohibited_keys(self.model_dump())

        # Prohibited recommendation check in observations
        for obs in self.criterion_observations:
            for pattern in PROHIBITED_RECOMMENDATION_KEYWORDS:
                if re.search(pattern, obs.observation, re.IGNORECASE):
                    raise ValueError(f"Prohibited recommendation or trait inference detected in observation: '{obs.observation[:60]}...'")

        return self


class ProvisionalAssessmentItemResponse(FrozenSchema):
    """Authoritative API DTO for a question's provisional AI assessment."""
    question_id: UUID
    assessment_status: str
    provisional_level: Optional[int] = None
    insufficient_evidence_reason: Optional[str] = None
    criterion_observations: List[Dict[str, Any]] = Field(default_factory=list)
    cited_segment_ids: List[str] = Field(default_factory=list)
    uncertainty_codes: List[str] = Field(default_factory=list)
    validation_report: Dict[str, Any] = Field(default_factory=dict)
    model_invocation_metadata: Dict[str, Any] = Field(default_factory=dict)


class SimulationAssessmentRunResponse(FrozenSchema):
    """Response returned when provisional assessment is triggered for a simulation attempt."""
    attempt_id: UUID
    lifecycle_status: str
    total_assessed: int
    provisional_count: int
    insufficient_evidence_count: int
    validation_rejected_count: int
    manual_review_required_count: int
    assessments: Dict[str, ProvisionalAssessmentItemResponse]
    safe_message: str
