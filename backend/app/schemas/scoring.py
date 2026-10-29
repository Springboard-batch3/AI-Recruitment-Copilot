import enum
import math
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from app.models.job import (
    RequirementCategory,
    RequirementNecessity,
    RequirementPriority,
)
from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.requirement_match import (
    MatchComparisonMethod,
    MatchResultState,
)

SCORING_POLICY_VERSION: str = "phase2c4_scoring_v1"
DECIMAL_CALCULATION_PRECISION: Decimal = Decimal("0.000001")
DISPLAY_ROUNDING_PRECISION: Decimal = Decimal("0.01")


class ScoringCalculationMethod(str, enum.Enum):
    REQUIREMENT_WEIGHTED_DIRECT = "REQUIREMENT_WEIGHTED_DIRECT"
    CATEGORY_WEIGHTED = "CATEGORY_WEIGHTED"


class ImportanceSource(str, enum.Enum):
    APPROVED_CUSTOM_WEIGHT = "APPROVED_CUSTOM_WEIGHT"
    PRIORITY_DEFAULT = "PRIORITY_DEFAULT"


class AssessmentConfidenceTier(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class ScoringWarningCode(str, enum.Enum):
    MUST_HAVE_PARTIALLY_MATCHED = "MUST_HAVE_PARTIALLY_MATCHED"
    MUST_HAVE_NOT_EVIDENCED = "MUST_HAVE_NOT_EVIDENCED"
    MUST_HAVE_INSUFFICIENT_EVIDENCE = "MUST_HAVE_INSUFFICIENT_EVIDENCE"
    MUST_HAVE_CONFLICTING_EVIDENCE = "MUST_HAVE_CONFLICTING_EVIDENCE"
    PROFILE_REVIEW_RECOMMENDED = "PROFILE_REVIEW_RECOMMENDED"
    EVIDENCE_CONFIDENCE_LOW = "EVIDENCE_CONFIDENCE_LOW"


class ScoringWarning(FrozenSchema):
    code: ScoringWarningCode
    requirement_id: Optional[UUID] = None
    category: Optional[RequirementCategory] = None
    result_state: Optional[MatchResultState] = None
    message: str
    evidence_references: List[str] = Field(default_factory=list)
    review_required: bool = True

    @model_validator(mode="after")
    def validate_warning_safety(self) -> "ScoringWarning":
        # Ensure message does not contain prohibited rejection language
        prohibited_phrases = [
            "unqualified",
            "reject candidate",
            "should not proceed",
            "candidate failed",
            "lacks the skill",
        ]
        msg_lower = self.message.lower()
        for phrase in prohibited_phrases:
            if phrase in msg_lower:
                raise ValueError(f"Warning message contains prohibited phrase '{phrase}': {self.message}")
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class RequirementContribution(FrozenSchema):
    requirement_id: UUID
    requirement_name: str = Field(default="Requirement", min_length=1)
    display_order: int = Field(ge=0)
    category: RequirementCategory
    necessity: RequirementNecessity
    priority: RequirementPriority
    importance_source: ImportanceSource
    configured_importance: float = Field(ge=0.0)
    effective_importance: float = Field(ge=0.0, le=1.0)
    match_state: MatchResultState
    match_value: float = Field(ge=0.0, le=1.0)
    weighted_contribution: float = Field(ge=0.0, le=100.0)
    evidence_references: List[str] = Field(default_factory=list)
    expected_value: Optional[str] = None
    observed_value: Optional[str] = None
    comparison_method: MatchComparisonMethod
    reason_codes: List[str] = Field(default_factory=list)
    review_recommended: bool = False

    @model_validator(mode="after")
    def validate_safety(self) -> "RequirementContribution":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class ComponentScore(FrozenSchema):
    category: RequirementCategory
    status: str = Field(pattern="^(APPLICABLE|NOT_APPLICABLE)$")
    requirement_count: int = Field(ge=0)
    applicable_count: int = Field(ge=0)
    total_importance: float = Field(ge=0.0)
    earned_contribution: float = Field(ge=0.0)
    raw_component_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    display_component_score: Optional[float] = Field(default=None, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def validate_component(self) -> "ComponentScore":
        if self.status == "NOT_APPLICABLE":
            if self.raw_component_score is not None or self.display_component_score is not None:
                raise ValueError("NOT_APPLICABLE component must not have a component score.")
        else:
            if self.raw_component_score is None or self.display_component_score is None:
                raise ValueError("APPLICABLE component must have a component score.")
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class AssessmentConfidenceDiagnostics(FrozenSchema):
    confidence_tier: AssessmentConfidenceTier
    applicable_requirement_count: int = Field(ge=0)
    determinately_evaluated_count: int = Field(ge=0)
    requirements_with_qualifying_evidence: int = Field(ge=0)
    insufficient_evidence_count: int = Field(ge=0)
    conflicting_evidence_count: int = Field(ge=0)
    profile_review_required: bool = False
    reasons_lowering_confidence: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_safety(self) -> "AssessmentConfidenceDiagnostics":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class CompatibilityScoreResult(FrozenSchema):
    scoring_policy_version: str = SCORING_POLICY_VERSION
    matcher_algorithm_version: str
    match_input_schema_version: str
    requisition_id: UUID
    job_version_id: UUID
    revision: int = Field(ge=1)
    profile_id: UUID
    profile_version: int = Field(ge=1)
    calculation_method: ScoringCalculationMethod
    requirement_contributions: List[RequirementContribution]
    component_scores: Dict[RequirementCategory, ComponentScore]
    raw_compatibility_score: float = Field(ge=0.0, le=100.0)
    display_compatibility_score: float = Field(ge=0.0, le=100.0)
    confidence_diagnostics: AssessmentConfidenceDiagnostics
    must_have_warnings: List[ScoringWarning] = Field(default_factory=list)
    general_warnings: List[ScoringWarning] = Field(default_factory=list)
    decision: Optional[Any] = None
    automatic_rejection: bool = False

    @model_validator(mode="after")
    def validate_result_integrity(self) -> "CompatibilityScoreResult":
        # 1. Automatic rejection must be permanently False
        if self.automatic_rejection is not False:
            raise ValueError("automatic_rejection must be permanently False.")
        # 2. Decision field must be None
        if self.decision is not None:
            raise ValueError("Human decision field must be absent or None in Phase 2C.3.")
        # 3. Verify no prohibited identity or protected keys exist anywhere
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self
