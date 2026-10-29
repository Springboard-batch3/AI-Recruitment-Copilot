import enum
import hashlib
import json
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field, model_validator

from app.models.job import RequirementCategory, RequirementNecessity, RequirementPriority
from app.schemas.candidate import StrictSchema
from app.schemas.matching import verify_no_prohibited_keys
from app.schemas.requirement_match import MatchComparisonMethod, MatchResultState
from app.schemas.scoring import (
    AssessmentConfidenceDiagnostics,
    ComponentScore,
    ScoringCalculationMethod,
    ScoringWarning,
)

EXPLANATION_TEMPLATE_VERSION: str = "phase2c5_explanation_v1"

DISCLAIMER_TEXT: str = (
    "This compatibility result compares documented professional evidence with recruiter-approved "
    "job requirements. It supports human review and is not an employment decision. "
    "Missing evidence does not prove that a candidate lacks a qualification."
)

PROHIBITED_DECISION_PHRASES = [
    "unqualified",
    "reject this candidate",
    "reject candidate",
    "should not proceed",
    "do not hire",
    "hire this candidate",
    "best candidate",
    "cultural fit",
    "culture fit",
    "lacks ability",
    "poor fit",
    "will perform well",
    "personality",
    "intelligence",
    "honesty",
]


def verify_no_prohibited_decision_language(text: str) -> None:
    """Verifies that generated explanation text contains no prohibited employment decision or speculative language."""
    text_lower = text.lower()
    for phrase in PROHIBITED_DECISION_PHRASES:
        if phrase in text_lower:
            raise ValueError(f"Prohibited decision or speculative phrasing detected: '{phrase}'")


class ControlledStateLanguage(str, enum.Enum):
    MATCHED = "Documented evidence met this requirement."
    PARTIALLY_MATCHED = "Documented evidence partially met the configured requirement."
    NOT_EVIDENCED = "No qualifying evidence was found in the selected profile version."
    INSUFFICIENT_EVIDENCE = "Possible evidence requires human verification before credit can be assigned."
    CONFLICTING_EVIDENCE = "Relevant profile information was inconsistent and requires review."
    NOT_APPLICABLE = "This requirement was excluded from the calculation under the stored configuration."


class ExplanationItemSource(StrictSchema):
    requirement_id: UUID
    display_order: int
    category: RequirementCategory
    necessity: RequirementNecessity
    priority: RequirementPriority
    match_state: MatchResultState
    comparison_method: MatchComparisonMethod
    match_value: float = Field(ge=0.0, le=1.0)
    weighted_contribution: float = Field(ge=0.0, le=100.0)
    configured_importance: float = Field(ge=0.0)
    effective_importance: float = Field(ge=0.0, le=1.0)
    evidence_references: List[str] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list)


class StrengthItem(StrictSchema):
    requirement_id: UUID
    category: RequirementCategory
    display_order: int
    necessity: RequirementNecessity
    priority: RequirementPriority
    match_state: MatchResultState
    weighted_contribution: float
    statement: str
    evidence_references: List[str]
    reason_codes: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_strength(self) -> "StrengthItem":
        if self.match_state != MatchResultState.MATCHED:
            raise ValueError("StrengthItem must have match_state MATCHED.")
        if not self.evidence_references:
            raise ValueError("StrengthItem must have non-empty evidence references.")
        verify_no_prohibited_decision_language(self.statement)
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class PartialMatchItem(StrictSchema):
    requirement_id: UUID
    category: RequirementCategory
    display_order: int
    necessity: RequirementNecessity
    priority: RequirementPriority
    match_state: MatchResultState
    match_value: float
    weighted_contribution: float
    statement: str
    exact_reason: str
    evidence_references: List[str]
    reason_codes: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_partial(self) -> "PartialMatchItem":
        if self.match_state != MatchResultState.PARTIALLY_MATCHED:
            raise ValueError("PartialMatchItem must have match_state PARTIALLY_MATCHED.")
        verify_no_prohibited_decision_language(self.statement)
        verify_no_prohibited_decision_language(self.exact_reason)
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class MustHaveWarningItem(StrictSchema):
    code: str
    requirement_id: Optional[UUID] = None
    category: Optional[RequirementCategory] = None
    result_state: Optional[MatchResultState] = None
    message: str
    evidence_references: List[str] = Field(default_factory=list)
    review_required: bool = True

    @model_validator(mode="after")
    def validate_safety(self) -> "MustHaveWarningItem":
        verify_no_prohibited_decision_language(self.message)
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class UncertaintyItem(StrictSchema):
    requirement_id: Optional[UUID] = None
    category: Optional[RequirementCategory] = None
    result_state: Optional[MatchResultState] = None
    uncertainty_type: str
    statement: str
    evidence_references: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_uncertainty(self) -> "UncertaintyItem":
        verify_no_prohibited_decision_language(self.statement)
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class ComponentCalculationItem(StrictSchema):
    category: RequirementCategory
    status: str
    requirement_count: int
    applicable_count: int
    total_importance: float
    earned_contribution: float
    raw_component_score: Optional[float] = None
    display_component_score: Optional[float] = None


class CalculationSummary(StrictSchema):
    calculation_method: ScoringCalculationMethod
    scoring_policy_version: str
    raw_compatibility_score: float
    display_compatibility_score: float
    total_requirements: int
    matched_count: int
    partially_matched_count: int
    not_evidenced_count: int
    insufficient_evidence_count: int
    conflicting_evidence_count: int
    excluded_not_applicable_count: int
    earned_contribution_total: float
    possible_contribution_total: float
    component_scores: Dict[str, ComponentCalculationItem]


class MatchExplanationResponse(StrictSchema):
    explanation_id: Optional[UUID] = None
    result_id: UUID
    run_id: UUID
    revision: int
    profile_id: UUID
    profile_version: int
    explanation_template_version: str = EXPLANATION_TEMPLATE_VERSION
    matcher_algorithm_version: str
    scoring_policy_version: str
    raw_compatibility_score: float
    display_compatibility_score: float
    confidence_tier: str
    confidence_diagnostics: AssessmentConfidenceDiagnostics
    deterministic_summary: str
    strengths: List[StrengthItem] = Field(default_factory=list)
    partial_matches: List[PartialMatchItem] = Field(default_factory=list)
    must_have_warnings: List[MustHaveWarningItem] = Field(default_factory=list)
    uncertainties: List[UncertaintyItem] = Field(default_factory=list)
    calculation_summary: CalculationSummary
    all_evidence_references: List[str] = Field(default_factory=list)
    explanation_checksum: str
    disclaimer: str = DISCLAIMER_TEXT

    @model_validator(mode="after")
    def validate_response(self) -> "MatchExplanationResponse":
        verify_no_prohibited_decision_language(self.deterministic_summary)
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


def compute_explanation_checksum(payload: Dict[str, Any]) -> str:
    """Computes deterministic SHA-256 hash over canonical JSON payload excluding existing checksum/id."""
    clean_dict = {
        k: v
        for k, v in payload.items()
        if k not in {"explanation_checksum", "explanationChecksum", "explanation_id", "explanationId"}
    }
    serialized = json.dumps(clean_dict, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().lower()
