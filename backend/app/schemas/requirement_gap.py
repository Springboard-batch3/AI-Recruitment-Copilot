import enum
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field, model_validator

from app.models.job import (
    RequirementCategory,
    RequirementNecessity,
    RequirementPriority,
)
from app.schemas.candidate import StrictSchema
from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.requirement_match import MatchResultState

GAP_SCHEMA_VERSION: str = "1.1.0"
GAP_POLICY_VERSION: str = "phase2d5_gap_v1"
WORDING_TEMPLATE_VERSION: str = "phase2d5_wording_v1"

PROHIBITED_DECISION_PHRASES = [
    "unqualified",
    "reject this candidate",
    "reject candidate",
    "should not proceed",
    "do not hire",
    "hire this candidate",
    "best candidate",
    "worst candidate",
    "cultural fit",
    "culture fit",
    "lacks ability",
    "lacks a skill",
    "lacks skill",
    "poor fit",
    "excellent fit",
    "will perform well",
    "personality",
    "intelligence",
    "honesty",
    "employability guarantee",
]


def verify_no_prohibited_decision_phrasing(text: str) -> None:
    """Verifies that generated text contains no prohibited employment decision, speculative, or character assessment phrasing."""
    text_lower = text.lower()
    for phrase in PROHIBITED_DECISION_PHRASES:
        if phrase in text_lower:
            raise ValueError(f"Prohibited decision, speculative, or evaluative phrasing detected: '{phrase}'")


class RequirementGapState(str, enum.Enum):
    """Canonical Phase 2D gap-state enum.

    Semantics:
    - COVERED: Documented evidence met the requirement.
    - PARTIALLY_COVERED: Documented evidence partially satisfied the requirement.
    - NOT_EVIDENCED: No qualifying evidence was documented in the profile.
    - UNVERIFIED: Possible evidence exists but requires human verification.
    - CONFLICTING_REVIEW_REQUIRED: Relevant evidence was conflicting or contradictory.
    - NOT_APPLICABLE: Requirement was excluded from matching under stored configuration.
    """

    COVERED = "COVERED"
    PARTIALLY_COVERED = "PARTIALLY_COVERED"
    NOT_EVIDENCED = "NOT_EVIDENCED"
    UNVERIFIED = "UNVERIFIED"
    CONFLICTING_REVIEW_REQUIRED = "CONFLICTING_REVIEW_REQUIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


# Exhaustive mapping from Phase 2C MatchResultState to Phase 2D RequirementGapState
MATCH_STATE_TO_GAP_STATE_MAP: Dict[MatchResultState, RequirementGapState] = {
    MatchResultState.MATCHED: RequirementGapState.COVERED,
    MatchResultState.PARTIALLY_MATCHED: RequirementGapState.PARTIALLY_COVERED,
    MatchResultState.NOT_EVIDENCED: RequirementGapState.NOT_EVIDENCED,
    MatchResultState.INSUFFICIENT_EVIDENCE: RequirementGapState.UNVERIFIED,
    MatchResultState.CONFLICTING_EVIDENCE: RequirementGapState.CONFLICTING_REVIEW_REQUIRED,
    MatchResultState.NOT_APPLICABLE: RequirementGapState.NOT_APPLICABLE,
}


class GapReviewActionCode(str, enum.Enum):
    """Controlled recruiter review action recommendations for gap findings."""

    NO_ACTION_REQUIRED = "NO_ACTION_REQUIRED"
    VERIFY_THRESHOLD_SHORTFALL = "VERIFY_THRESHOLD_SHORTFALL"
    VERIFY_ADDITIONAL_DOCUMENTATION = "VERIFY_ADDITIONAL_DOCUMENTATION"
    CONFIRM_UNCERTAIN_EVIDENCE = "CONFIRM_UNCERTAIN_EVIDENCE"
    RESOLVE_EVIDENCE_CONFLICT = "RESOLVE_EVIDENCE_CONFLICT"
    REVIEW_EVIDENCE_OPTIONAL = "REVIEW_EVIDENCE_OPTIONAL"


class RequirementGapRecord(FrozenSchema):
    """Canonical, forward-compatible Phase 2D gap record representing the interpretation
    of a single requirement finding from an immutable Phase 2C match result.

    Maintains complete source provenance and requirement provenance while strictly
    excluding all candidate identity, contact, protected characteristics, and raw document text.
    """

    # Source Provenance
    match_run_id: UUID
    match_result_id: UUID
    requisition_id: UUID
    job_version_id: UUID
    requisition_revision: int = Field(ge=1)
    profile_id: UUID
    profile_version: int = Field(ge=1)
    evaluation_date: datetime
    match_input_schema_version: str
    matcher_algorithm_version: str
    matching_rule_version: str
    scoring_policy_version: str
    taxonomy_version: Optional[str] = None

    # Requirement Provenance
    requirement_id: UUID
    requirement_name: str = Field(default="Requirement", min_length=1)
    display_order: int = Field(ge=0)
    category: RequirementCategory
    necessity: RequirementNecessity
    priority: RequirementPriority
    source_match_state: MatchResultState
    source_match_value: float = Field(ge=0.0, le=1.0)

    # Gap Fields
    gap_state: RequirementGapState
    required_value: Optional[str] = None
    documented_value: Optional[str] = None
    measurable_shortfall_value: Optional[float] = Field(default=None, ge=0.0)
    shortfall_unit: Optional[str] = None
    shortfall_reason_code: Optional[str] = None
    evidence_references: List[str] = Field(default_factory=list)
    review_required: bool = False
    review_action_code: GapReviewActionCode = GapReviewActionCode.NO_ACTION_REQUIRED
    must_have_warning_reference: Optional[str] = None
    recruiter_wording: Optional[str] = None
    recruiter_wording_placeholder: Optional[str] = None
    gap_schema_version: str = GAP_SCHEMA_VERSION
    gap_policy_version: str = GAP_POLICY_VERSION

    @model_validator(mode="after")
    def validate_gap_integrity(self) -> "RequirementGapRecord":
        # 1. Evidence references must be deterministically sorted
        if self.evidence_references != sorted(self.evidence_references):
            raise ValueError("evidence_references must be deterministically sorted.")

        # 2. State-specific invariants
        if self.gap_state == RequirementGapState.NOT_EVIDENCED:
            if self.source_match_value > 0.0:
                raise ValueError("NOT_EVIDENCED gap state must have source_match_value == 0.0.")
            if len(self.evidence_references) > 0:
                raise ValueError("NOT_EVIDENCED gap state must not contain credited evidence references.")

        if self.gap_state == RequirementGapState.NOT_APPLICABLE:
            if self.source_match_value > 0.0:
                raise ValueError("NOT_APPLICABLE gap state must have source_match_value == 0.0.")

        if self.gap_state == RequirementGapState.COVERED:
            if len(self.evidence_references) == 0:
                raise ValueError("COVERED gap state must contain at least one valid credited evidence reference.")
            if not self.documented_value or self.documented_value.strip() == "" or self.documented_value.lower() == "none documented":
                raise ValueError("COVERED gap state must contain a safe documented-value summary and cannot say 'None documented'.")

        if self.gap_state == RequirementGapState.PARTIALLY_COVERED:
            if len(self.evidence_references) == 0:
                raise ValueError("PARTIALLY_COVERED gap state must contain qualifying evidence references.")

        # 3. Controlled wording validation
        if self.recruiter_wording is not None:
            verify_no_prohibited_decision_phrasing(self.recruiter_wording)

        # 4. Verify no prohibited identity or protected keys exist
        verify_no_prohibited_keys(self.model_dump(mode="json"))

        return self


class RequirementGapSetContract(FrozenSchema):
    """Container representing the complete, immutable set of gap records
    derived from a single candidate match result.
    """

    gap_schema_version: str = GAP_SCHEMA_VERSION
    gap_policy_version: str = GAP_POLICY_VERSION
    match_run_id: UUID
    match_result_id: UUID
    requisition_id: UUID
    job_version_id: UUID
    requisition_revision: int = Field(ge=1)
    profile_id: UUID
    profile_version: int = Field(ge=1)
    evaluation_date: datetime
    matcher_algorithm_version: str
    scoring_policy_version: str
    total_requirements: int = Field(ge=0)
    gaps: List[RequirementGapRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_gap_set(self) -> "RequirementGapSetContract":
        if len(self.gaps) != self.total_requirements:
            raise ValueError(
                f"total_requirements ({self.total_requirements}) does not match count of gap records ({len(self.gaps)})."
            )

        # Ensure order matches display_order strictly
        orders = [g.display_order for g in self.gaps]
        if orders != sorted(orders):
            raise ValueError("Requirement gap records must follow strict display_order ascending.")

        # Ensure no duplicate requirement IDs
        req_ids = [g.requirement_id for g in self.gaps]
        if len(req_ids) != len(set(req_ids)):
            raise ValueError("Requirement gap records must have unique requirement IDs.")

        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class EvidenceSummaryResponse(StrictSchema):
    """Authoritative, deterministic evidence summary shared identically across comparison and gap reports."""
    total_criteria: int = Field(ge=0)
    required_total: int = Field(ge=0)
    required_evidence_found: int = Field(ge=0)
    required_partial_evidence: int = Field(ge=0)
    required_not_found: int = Field(ge=0)
    required_needs_verification: int = Field(ge=0)
    preferred_total: int = Field(ge=0)
    preferred_evidence_found: int = Field(ge=0)
    overall_evidence_found: int = Field(ge=0)
    overall_partial_evidence: int = Field(ge=0)
    overall_not_found: int = Field(ge=0)
    extraction_confidence: str

    @model_validator(mode="after")
    def validate_privacy(self) -> "EvidenceSummaryResponse":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


# Phase 2D.3 API & Canonical Report Response Schemas
class GapReportCreateRequest(StrictSchema):
    match_result_id: UUID
    idempotency_key: Optional[str] = Field(default=None, max_length=255)


class GapReportItemResponse(StrictSchema):
    item_id: UUID
    report_id: UUID
    requirement_id: UUID
    requirement_name: str = Field(default="Requirement", min_length=1)
    display_order: int
    category: RequirementCategory
    necessity: RequirementNecessity
    priority: RequirementPriority
    source_match_state: MatchResultState
    source_match_value: float
    gap_state: RequirementGapState
    required_value: Optional[str] = None
    documented_value: Optional[str] = None
    measurable_shortfall_value: Optional[float] = None
    shortfall_unit: Optional[str] = None
    shortfall_reason_code: Optional[str] = None
    evidence_references: List[str] = Field(default_factory=list)
    review_required: bool
    review_action_code: GapReviewActionCode
    must_have_warning_reference: Optional[str] = None
    recruiter_wording: str
    created_at: datetime

    @model_validator(mode="after")
    def validate_privacy(self) -> "GapReportItemResponse":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class GapReportSummaryResponse(StrictSchema):
    report_id: UUID
    match_run_id: UUID
    match_result_id: UUID
    job_id: UUID
    job_version_id: UUID
    revision: int
    profile_id: UUID
    profile_version: int
    evaluation_date: datetime
    matcher_algorithm_version: str
    matching_rule_version: str
    scoring_policy_version: str
    match_input_schema_version: str
    taxonomy_version: Optional[str] = None
    gap_schema_version: str
    gap_policy_version: str
    wording_template_version: str
    source_fingerprint: str
    report_fingerprint: str
    total_requirements: int
    covered_count: int
    partially_covered_count: int
    not_evidenced_count: int
    unverified_count: int
    conflicting_count: int
    not_applicable_count: int
    must_have_warning_count: int
    reused: bool = False
    created_at: datetime
    report_url: str

    @model_validator(mode="after")
    def validate_privacy(self) -> "GapReportSummaryResponse":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class GapReportDetailResponse(StrictSchema):
    report_id: UUID
    match_run_id: UUID
    match_result_id: UUID
    job_id: UUID
    job_version_id: UUID
    revision: int
    profile_id: UUID
    profile_version: int
    evaluation_date: datetime
    matcher_algorithm_version: str
    matching_rule_version: str
    scoring_policy_version: str
    match_input_schema_version: str
    taxonomy_version: Optional[str] = None
    gap_schema_version: str
    gap_policy_version: str
    wording_template_version: str
    source_fingerprint: str
    report_fingerprint: str
    total_requirements: int
    covered_count: int
    partially_covered_count: int
    not_evidenced_count: int
    unverified_count: int
    conflicting_count: int
    not_applicable_count: int
    must_have_warning_count: int
    must_have_total_count: int = 0
    must_have_evidenced_count: int = 0
    evidence_summary: Optional[EvidenceSummaryResponse] = None
    compatibility_score: Optional[float] = None
    confidence_tier: Optional[str] = None
    legacy_report_notice: Optional[str] = None
    created_at: datetime
    items: List[GapReportItemResponse]

    @model_validator(mode="after")
    def validate_privacy(self) -> "GapReportDetailResponse":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class CanonicalGapReportResponse(StrictSchema):
    report_id: UUID
    match_run_id: UUID
    match_result_id: UUID
    job_id: UUID
    job_version_id: UUID
    revision: int
    profile_id: UUID
    profile_version: int
    evaluation_date: datetime
    matcher_algorithm_version: str
    matching_rule_version: str
    scoring_policy_version: str
    match_input_schema_version: str
    taxonomy_version: Optional[str] = None
    gap_schema_version: str
    gap_policy_version: str
    wording_template_version: str
    source_fingerprint: str
    report_fingerprint: str
    total_requirements: int
    summary_counts: Dict[str, int]
    must_have_warning_count: int
    must_have_total_count: int = 0
    must_have_evidenced_count: int = 0
    evidence_summary: Optional[EvidenceSummaryResponse] = None
    compatibility_score: Optional[float] = None
    confidence_tier: Optional[str] = None
    legacy_report_notice: Optional[str] = None
    gaps: List[GapReportItemResponse]
    all_evidence_references: List[str]
    disclaimer: str = (
        "This requirement-gap report compares documented professional evidence against recruiter-approved "
        "job requirements based on an immutable Phase 2C match result. It supports human review and is not an "
        "employment decision. Missing evidence does not prove that a candidate lacks a qualification."
    )
    created_at: datetime

    @model_validator(mode="after")
    def validate_invariants(self) -> "CanonicalGapReportResponse":
        # Invariant: total_requirements == sum of gap state counts
        counts = self.summary_counts
        covered = counts.get(RequirementGapState.COVERED.value, 0)
        partially = counts.get(RequirementGapState.PARTIALLY_COVERED.value, 0)
        not_ev = counts.get(RequirementGapState.NOT_EVIDENCED.value, 0)
        unver = counts.get(RequirementGapState.UNVERIFIED.value, 0)
        conflict = counts.get(RequirementGapState.CONFLICTING_REVIEW_REQUIRED.value, 0)
        not_app = counts.get(RequirementGapState.NOT_APPLICABLE.value, 0)

        sum_counts = covered + partially + not_ev + unver + conflict + not_app
        if self.total_requirements != sum_counts:
            raise ValueError(
                f"total_requirements ({self.total_requirements}) must equal sum of state counts ({sum_counts})."
            )

        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self

