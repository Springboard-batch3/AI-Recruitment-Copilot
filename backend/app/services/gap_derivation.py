import copy
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from app.models.job import RequirementCategory, RequirementNecessity
from app.schemas.matching import verify_no_prohibited_keys
from app.schemas.requirement_gap import (
    GAP_POLICY_VERSION,
    GAP_SCHEMA_VERSION,
    GapReviewActionCode,
    RequirementGapRecord,
    RequirementGapSetContract,
    RequirementGapState,
    verify_no_prohibited_decision_phrasing,
)
from app.schemas.requirement_match import MatchResultState
from app.services.gap_validation import (
    GapSourceValidationError,
    UnsupportedMatchResultStateError,
    map_match_state_to_gap_state,
)

logger = logging.getLogger("ai_hiring.services.gap_derivation")

# Controlled Base Wording Templates
CONTROLLED_BASE_WORDING: Dict[RequirementGapState, str] = {
    RequirementGapState.COVERED: "Documented evidence met this requirement.",
    RequirementGapState.PARTIALLY_COVERED: "Documented evidence partially met the configured requirement.",
    RequirementGapState.NOT_EVIDENCED: "No qualifying evidence was found in the selected profile version.",
    RequirementGapState.UNVERIFIED: "Possible relevant evidence was identified, but it requires human confirmation before credit can be assigned.",
    RequirementGapState.CONFLICTING_REVIEW_REQUIRED: "Relevant evidence was inconsistent. Review the linked evidence before using this finding.",
    RequirementGapState.NOT_APPLICABLE: "This requirement was excluded from active evaluation and is retained for audit.",
}


class GapDerivationError(Exception):
    """Domain exception raised during deterministic gap derivation."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _derive_experience_shortfall(
    source_item: Dict[str, Any],
    gap_state: RequirementGapState,
) -> Tuple[Optional[float], Optional[str], Optional[str], Optional[str]]:
    """Derives measurable experience shortfall strictly from trusted, stored Phase 2C values.

    Returns:
        (measurable_shortfall_value, shortfall_unit, shortfall_reason_code, exact_shortfall_phrase)
    """
    category = str(source_item.get("category", ""))
    if category != RequirementCategory.EXPERIENCE.value:
        return None, None, None, None

    expected_val = source_item.get("expected_value")
    observed_val = source_item.get("observed_value")
    reason_codes = source_item.get("reason_codes", [])

    # Check for date conflict in stored finding
    if "INVERTED_DATE_INTERVAL" in reason_codes or gap_state == RequirementGapState.CONFLICTING_REVIEW_REQUIRED:
        return None, None, "CONFLICTING_TIMELINE_INTERVALS", None

    # Check for imprecise dates in stored finding
    if "IMPRECISE_DATE_PRECISION" in reason_codes or gap_state == RequirementGapState.UNVERIFIED:
        return None, None, "UNVERIFIED_DATE_PRECISION", None

    # Parse required months from expected_value (e.g., "36 months")
    req_months: Optional[int] = None
    if expected_val:
        m_req = re.search(r"^(\d+)\s*months?", str(expected_val).strip(), re.IGNORECASE)
        if m_req:
            req_months = int(m_req.group(1))

    # Also check if reason_codes contain EXPERIENCE_BELOW_THRESHOLD_X_OF_Y_MONTHS
    obs_from_code: Optional[int] = None
    req_from_code: Optional[int] = None
    for code in reason_codes:
        m_code = re.search(r"^EXPERIENCE_BELOW_THRESHOLD_(\d+)_OF_(\d+)_MONTHS$", code)
        if m_code:
            obs_from_code = int(m_code.group(1))
            req_from_code = int(m_code.group(2))
            break

    if req_from_code is not None:
        req_months = req_from_code

    if req_months is None or req_months <= 0:
        # Non-duration experience requirement (e.g. role title presence)
        return None, None, "NON_DURATION_EXPERIENCE_REQUIREMENT", None

    # Parse documented months from observed_value (e.g., "24 months (Software Engineer)")
    doc_months: Optional[int] = None
    if obs_from_code is not None:
        doc_months = obs_from_code
    elif observed_val:
        m_obs = re.search(r"^(\d+)\s*months?", str(observed_val).strip(), re.IGNORECASE)
        if m_obs:
            doc_months = int(m_obs.group(1))

    # Never assume doc_months = 0 when no duration was documented.
    # If state is NOT_EVIDENCED or doc_months is missing:
    if doc_months is None:
        if gap_state == RequirementGapState.COVERED:
            # Full match established by Phase 2C
            return 0.0, "months", "THRESHOLD_SATISFIED", None
        # Rule A: Required months, no duration extracted -> return None shortfall, reason DOCUMENTED_DURATION_UNAVAILABLE
        return None, None, "DOCUMENTED_DURATION_UNAVAILABLE", None

    # Both req_months and doc_months are known and comparable
    shortfall = max(req_months - doc_months, 0)
    exact_phrase = None
    if shortfall > 0:
        exact_phrase = f"{shortfall}-month difference to verify against the configured {req_months}-month target."
        reason_code = "DURATION_BELOW_THRESHOLD"
    else:
        reason_code = "THRESHOLD_SATISFIED"

    return float(shortfall), "months", reason_code, exact_phrase


def _determine_review_action(
    gap_state: RequirementGapState,
    shortfall_value: Optional[float],
    source_item: Dict[str, Any],
) -> GapReviewActionCode:
    """Assigns controlled recruiter review action tied strictly to the requirement state."""
    if gap_state == RequirementGapState.NOT_APPLICABLE:
        return GapReviewActionCode.NO_ACTION_REQUIRED

    if gap_state == RequirementGapState.COVERED:
        review_rec = bool(source_item.get("review_recommended", False))
        return GapReviewActionCode.REVIEW_EVIDENCE_OPTIONAL if review_rec else GapReviewActionCode.NO_ACTION_REQUIRED

    if gap_state == RequirementGapState.PARTIALLY_COVERED:
        if shortfall_value is not None and shortfall_value > 0.0:
            return GapReviewActionCode.VERIFY_THRESHOLD_SHORTFALL
        return GapReviewActionCode.VERIFY_ADDITIONAL_DOCUMENTATION

    if gap_state == RequirementGapState.NOT_EVIDENCED:
        return GapReviewActionCode.VERIFY_ADDITIONAL_DOCUMENTATION

    if gap_state == RequirementGapState.UNVERIFIED:
        return GapReviewActionCode.CONFIRM_UNCERTAIN_EVIDENCE

    if gap_state == RequirementGapState.CONFLICTING_REVIEW_REQUIRED:
        return GapReviewActionCode.RESOLVE_EVIDENCE_CONFLICT

    return GapReviewActionCode.NO_ACTION_REQUIRED


def _generate_controlled_wording(
    gap_state: RequirementGapState,
    exact_shortfall_phrase: Optional[str],
) -> str:
    """Generates recruiter-facing wording using versioned deterministic templates.

    Verifies absence of prohibited evaluation, decision, or speculative phrasing.
    """
    base = CONTROLLED_BASE_WORDING[gap_state]
    if gap_state == RequirementGapState.PARTIALLY_COVERED and exact_shortfall_phrase:
        wording = f"{base} {exact_shortfall_phrase}"
    else:
        wording = base

    verify_no_prohibited_decision_phrasing(wording)
    return wording


def derive_requirement_gap_record(
    source_gap: RequirementGapRecord,
    source_item: Dict[str, Any],
) -> RequirementGapRecord:
    """Derives a completed, evidence-linked RequirementGapRecord from a validated Phase 2D.1 gap record.

    Invariants:
    - Pure transformation without external lookups, OCR, models, or new matching.
    - Derived shortfall is calculated strictly when comparable trusted inputs exist.
    - Non-quantifiable requirements receive null shortfalls without invented percentages.
    - Controlled wording and review action codes are deterministically generated.
    - Prohibited hiring and character phrases are strictly excluded.
    """
    gap_state = source_gap.gap_state

    # 1. Derive measurable shortfall
    shortfall_val, shortfall_unit, shortfall_reason, exact_shortfall_phrase = _derive_experience_shortfall(
        source_item=source_item,
        gap_state=gap_state,
    )

    # 2. Assign review action code
    action_code = _determine_review_action(
        gap_state=gap_state,
        shortfall_value=shortfall_val,
        source_item=source_item,
    )

    # 3. Generate controlled wording
    wording = _generate_controlled_wording(
        gap_state=gap_state,
        exact_shortfall_phrase=exact_shortfall_phrase,
    )

    # Review required flag
    is_review_required = (
        source_gap.review_required
        or gap_state in {
            RequirementGapState.UNVERIFIED,
            RequirementGapState.CONFLICTING_REVIEW_REQUIRED,
        }
        or (gap_state == RequirementGapState.PARTIALLY_COVERED and source_gap.necessity == RequirementNecessity.MUST_HAVE)
    )

    # Build and return immutable record
    return RequirementGapRecord(
        match_run_id=source_gap.match_run_id,
        match_result_id=source_gap.match_result_id,
        requisition_id=source_gap.requisition_id,
        job_version_id=source_gap.job_version_id,
        requisition_revision=source_gap.requisition_revision,
        profile_id=source_gap.profile_id,
        profile_version=source_gap.profile_version,
        evaluation_date=source_gap.evaluation_date,
        match_input_schema_version=source_gap.match_input_schema_version,
        matcher_algorithm_version=source_gap.matcher_algorithm_version,
        matching_rule_version=source_gap.matching_rule_version,
        scoring_policy_version=source_gap.scoring_policy_version,
        taxonomy_version=source_gap.taxonomy_version,
        requirement_id=source_gap.requirement_id,
        requirement_name=source_gap.requirement_name,
        display_order=source_gap.display_order,
        category=source_gap.category,
        necessity=source_gap.necessity,
        priority=source_gap.priority,
        source_match_state=source_gap.source_match_state,
        source_match_value=source_gap.source_match_value,
        gap_state=gap_state,
        required_value=source_gap.required_value,
        documented_value=source_gap.documented_value,
        measurable_shortfall_value=shortfall_val,
        shortfall_unit=shortfall_unit,
        shortfall_reason_code=shortfall_reason,
        evidence_references=source_gap.evidence_references,
        review_required=is_review_required,
        review_action_code=action_code,
        must_have_warning_reference=source_gap.must_have_warning_reference,
        recruiter_wording=wording,
        recruiter_wording_placeholder=wording,
        gap_schema_version=GAP_SCHEMA_VERSION,
        gap_policy_version=GAP_POLICY_VERSION,
    )


def derive_requirement_gaps(
    validated_gap_set: RequirementGapSetContract,
    score_decomposition: Dict[str, Any],
) -> RequirementGapSetContract:
    """Pure gap derivation service. Accepts only validated Phase 2D.1 contracts.

    Guarantees:
    - Never accepts raw resume text, candidate identity, or draft jobs.
    - Exactly one gap record produced per source requirement result.
    - Persisted requirement order and IDs are preserved.
    - NOT_APPLICABLE records remain present for audit.
    - No duplicate requirement IDs.
    - If count or association is inconsistent, fails safely without returning a partial set.
    - Input structures are not mutated.
    """
    if validated_gap_set is None:
        raise GapDerivationError(
            code="GAP_SOURCE_EMPTY",
            message="validated_gap_set cannot be None.",
        )

    # Privacy check on decomposition
    verify_no_prohibited_keys(score_decomposition)

    req_contributions: List[Dict[str, Any]] = score_decomposition.get("requirement_contributions", [])
    contrib_map: Dict[str, Dict[str, Any]] = {}
    for c in req_contributions:
        r_id = str(c.get("requirement_id", ""))
        if r_id:
            contrib_map[r_id] = c

    derived_gaps: List[RequirementGapRecord] = []
    seen_ids = set()

    for gap in validated_gap_set.gaps:
        r_id_str = str(gap.requirement_id)
        if r_id_str not in contrib_map:
            raise GapDerivationError(
                code="GAP_SOURCE_REQUIREMENT_MISSING",
                message=f"Requirement contribution missing for requirement ID '{gap.requirement_id}'.",
            )

        if gap.requirement_id in seen_ids:
            raise GapDerivationError(
                code="GAP_SOURCE_DUPLICATE_REQUIREMENT",
                message=f"Duplicate requirement ID '{gap.requirement_id}' in validated gap set.",
            )
        seen_ids.add(gap.requirement_id)

        source_item = contrib_map[r_id_str]
        derived = derive_requirement_gap_record(gap, source_item)
        derived_gaps.append(derived)

    # Preserve display order strictly
    derived_gaps.sort(key=lambda g: g.display_order)

    # Atomic collection validation
    if len(derived_gaps) != validated_gap_set.total_requirements:
        raise GapDerivationError(
            code="GAP_COUNT_MISMATCH",
            message=(
                f"Derived gap count ({len(derived_gaps)}) does not match source "
                f"requirement count ({validated_gap_set.total_requirements})."
            ),
        )

    return RequirementGapSetContract(
        gap_schema_version=GAP_SCHEMA_VERSION,
        gap_policy_version=GAP_POLICY_VERSION,
        match_run_id=validated_gap_set.match_run_id,
        match_result_id=validated_gap_set.match_result_id,
        requisition_id=validated_gap_set.requisition_id,
        job_version_id=validated_gap_set.job_version_id,
        requisition_revision=validated_gap_set.requisition_revision,
        profile_id=validated_gap_set.profile_id,
        profile_version=validated_gap_set.profile_version,
        evaluation_date=validated_gap_set.evaluation_date,
        matcher_algorithm_version=validated_gap_set.matcher_algorithm_version,
        scoring_policy_version=validated_gap_set.scoring_policy_version,
        total_requirements=len(derived_gaps),
        gaps=derived_gaps,
    )
