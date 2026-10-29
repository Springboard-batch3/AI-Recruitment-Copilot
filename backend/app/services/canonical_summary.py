"""Canonical Evidence Summary Calculation Service.

Provides a single deterministic calculation for evidence summaries across:
- Match result comparisons (CandidateComparisonView)
- Detailed Requirement Gap Reports (RequirementGapReportView)

Ensures comparison summaries and detailed gap reports always agree identically.
"""

from typing import Any, Dict, List, Optional
from app.models.job import RequirementNecessity
from app.schemas.requirement_gap import EvidenceSummaryResponse
from app.schemas.requirement_match import MatchResultState


def compute_canonical_evidence_summary(
    requirement_contributions: Optional[List[Any]],
    extraction_confidence: Optional[str] = "MEDIUM",
) -> Optional[EvidenceSummaryResponse]:
    """Compute canonical EvidenceSummaryResponse from requirement contributions or gap report items.

    If requirement_contributions is None or empty, returns None to represent
    'Summary unavailable' rather than silently defaulting counts to 0.
    """
    if requirement_contributions is None:
        return None

    if len(requirement_contributions) == 0:
        return None

    total_criteria = len(requirement_contributions)
    required_total = 0
    required_evidence_found = 0
    required_partial_evidence = 0
    required_not_found = 0
    required_needs_verification = 0

    preferred_total = 0
    preferred_evidence_found = 0

    overall_evidence_found = 0
    overall_partial_evidence = 0
    overall_not_found = 0

    for item in requirement_contributions:
        # Support dict, ORM objects, and Pydantic models
        if isinstance(item, dict):
            raw_nec = item.get("necessity")
            is_mand = item.get("is_mandatory")
            state = item.get("gap_state") or item.get("match_state") or item.get("source_match_state")
        else:
            raw_nec = getattr(item, "necessity", None)
            is_mand = getattr(item, "is_mandatory", None)
            state = getattr(item, "gap_state", None) or getattr(item, "match_state", None) or getattr(item, "source_match_state", None)

        if raw_nec is not None:
            if isinstance(raw_nec, RequirementNecessity):
                is_required = (raw_nec == RequirementNecessity.MUST_HAVE)
            else:
                is_required = str(raw_nec).lower() in ("must_have", "required")
        elif is_mand is not None:
            is_required = bool(is_mand)
        else:
            is_required = True

        if isinstance(state, MatchResultState):
            state = state.value
        elif hasattr(state, "value"):
            state = state.value
        state_str = str(state).upper() if state else ""

        is_covered = state_str in (
            MatchResultState.MATCHED.value,
            "FULLY_SATISFIED",
            "COVERED",
        )
        is_partial = state_str in (
            MatchResultState.PARTIALLY_MATCHED.value,
            "PARTIALLY_SATISFIED",
            "PARTIALLY_COVERED",
        )
        is_not_evidenced = state_str in (
            MatchResultState.NOT_EVIDENCED.value,
            "NOT_SATISFIED",
            "NOT_EVIDENCED",
        )
        is_unverified = state_str in (
            MatchResultState.INSUFFICIENT_EVIDENCE.value,
            MatchResultState.CONFLICTING_EVIDENCE.value,
            "UNVERIFIED",
            "INSUFFICIENT_DATA",
            "CONFLICTING_DATA",
            "CONFLICTING_REVIEW_REQUIRED",
            "NEEDS_VERIFICATION",
        )

        # Overall tallies
        if is_covered:
            overall_evidence_found += 1
        elif is_partial:
            overall_partial_evidence += 1
        elif is_not_evidenced:
            overall_not_found += 1

        # Tier tallies
        if is_required:
            required_total += 1
            if is_covered:
                required_evidence_found += 1
            elif is_partial:
                required_partial_evidence += 1
            elif is_unverified:
                required_needs_verification += 1
            elif is_not_evidenced:
                required_not_found += 1
        else:
            preferred_total += 1
            if is_covered:
                preferred_evidence_found += 1

    return EvidenceSummaryResponse(
        total_criteria=total_criteria,
        required_total=required_total,
        required_evidence_found=required_evidence_found,
        required_partial_evidence=required_partial_evidence,
        required_not_found=required_not_found,
        required_needs_verification=required_needs_verification,
        preferred_total=preferred_total,
        preferred_evidence_found=preferred_evidence_found,
        overall_evidence_found=overall_evidence_found,
        overall_partial_evidence=overall_partial_evidence,
        overall_not_found=overall_not_found,
        extraction_confidence=extraction_confidence or "MEDIUM",
    )
