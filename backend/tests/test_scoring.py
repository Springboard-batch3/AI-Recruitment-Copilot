from datetime import datetime, timezone
from decimal import Decimal
import math
import uuid
import pytest

from app.models.job import (
    RequirementCategory,
    RequirementNecessity,
    RequirementPriority,
)
from app.schemas.matching import (
    CandidateFeatureView,
    EvidenceReferenceFeature,
    FrozenRequisitionMatchingView,
    MatchInputSnapshot,
    RequirementMatchingItem,
)
from app.schemas.requirement_match import (
    MatchComparisonMethod,
    MatchResultState,
    RequirementMatch,
    RequirementMatchResultSet,
)
from app.schemas.scoring import (
    AssessmentConfidenceTier,
    ImportanceSource,
    ScoringCalculationMethod,
    ScoringWarningCode,
    SCORING_POLICY_VERSION,
)
from app.services.scoring import (
    DuplicateRequirementResultError,
    EmptyApplicableRequirementSetError,
    InvalidWeightError,
    MissingImportanceConfigurationError,
    PRIORITY_IMPORTANCE_MAP,
    ZeroAggregateDenominatorError,
    calculate_compatibility_score,
    resolve_requirement_importance,
)


def _make_match(
    requirement_id: uuid.UUID = None,
    display_order: int = 0,
    category: RequirementCategory = RequirementCategory.SKILL,
    necessity: RequirementNecessity = RequirementNecessity.MUST_HAVE,
    priority: RequirementPriority = RequirementPriority.MEDIUM,
    approved_weight: float = None,
    result_state: MatchResultState = MatchResultState.MATCHED,
    match_value: float = 1.0,
    comparison_method: MatchComparisonMethod = MatchComparisonMethod.EXACT_NORMALIZED_LABEL,
    evidence_references: list = None,
    review_recommended: bool = False,
) -> RequirementMatch:
    req_id = requirement_id or uuid.uuid4()
    ev_refs = sorted(evidence_references or (["ev-default"] if match_value > 0.0 else []))
    return RequirementMatch(
        requirement_id=req_id,
        display_order=display_order,
        category=category,
        necessity=necessity,
        priority=priority,
        approved_weight=approved_weight,
        result_state=result_state,
        match_value=match_value,
        comparison_method=comparison_method,
        evidence_references=ev_refs,
        reason_codes=[],
        evidence_confidence=0.9 if match_value > 0.0 else None,
        review_recommended=review_recommended,
        matching_rule_version="phase2c2_deterministic_v1",
    )


def _make_result_set(matches: list) -> RequirementMatchResultSet:
    return RequirementMatchResultSet(
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        evaluation_date=datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc),
        requisition_id=uuid.uuid4(),
        job_version_id=uuid.uuid4(),
        revision=1,
        profile_id=uuid.uuid4(),
        profile_version=1,
        matches=sorted(matches, key=lambda m: (m.display_order, str(m.requirement_id))),
    )


# ==========================================
# 1. REQUIRED WEIGHT TESTS
# ==========================================

def test_priority_default_used_when_custom_weight_absent():
    """1. Priority default is used when custom weight is absent."""
    m = _make_match(priority=RequirementPriority.CRITICAL, approved_weight=None)
    imp, src = resolve_requirement_importance(m)
    assert imp == Decimal("4.0")
    assert src == ImportanceSource.PRIORITY_DEFAULT

    m_low = _make_match(priority=RequirementPriority.LOW, approved_weight=None)
    imp_low, src_low = resolve_requirement_importance(m_low)
    assert imp_low == Decimal("1.0")
    assert src_low == ImportanceSource.PRIORITY_DEFAULT


def test_custom_weight_replaces_rather_than_multiplies_priority():
    """2. Custom weight replaces rather than multiplies priority weight."""
    # Priority is LOW (1.0), but custom weight is 8.5
    m = _make_match(priority=RequirementPriority.LOW, approved_weight=8.5)
    imp, src = resolve_requirement_importance(m)
    assert imp == Decimal("8.5")
    assert src == ImportanceSource.APPROVED_CUSTOM_WEIGHT


def test_must_have_status_does_not_alter_weight():
    """3. Must-have status does not alter weight."""
    m_must = _make_match(necessity=RequirementNecessity.MUST_HAVE, priority=RequirementPriority.HIGH)
    m_pref = _make_match(necessity=RequirementNecessity.PREFERRED, priority=RequirementPriority.HIGH)

    imp_must, _ = resolve_requirement_importance(m_must)
    imp_pref, _ = resolve_requirement_importance(m_pref)
    assert imp_must == imp_pref == Decimal("3.0")


def test_negative_weight_is_rejected():
    """4. Negative weight is rejected."""
    # Test at schema boundary
    with pytest.raises(Exception):
        _make_match(approved_weight=-1.5)

    # Test at service level
    m = _make_match()
    object.__setattr__(m, "approved_weight", -1.5)
    with pytest.raises(InvalidWeightError):
        resolve_requirement_importance(m)


def test_nan_and_infinite_weights_are_rejected():
    """5. NaN and infinite weights are rejected."""
    # Test at schema boundary
    with pytest.raises(Exception):
        _make_match(approved_weight=float("nan"))

    # Test at service level
    m = _make_match()
    object.__setattr__(m, "approved_weight", float("nan"))
    with pytest.raises(InvalidWeightError):
        resolve_requirement_importance(m)

    object.__setattr__(m, "approved_weight", float("inf"))
    with pytest.raises(InvalidWeightError):
        resolve_requirement_importance(m)


def test_zero_weight_follows_approved_policy():
    """6. Zero weight follows the approved policy (intentional zero weight / 0 earned)."""
    m = _make_match(approved_weight=0.0)
    imp, src = resolve_requirement_importance(m)
    assert imp == Decimal("0.0")
    assert src == ImportanceSource.APPROVED_CUSTOM_WEIGHT


def test_missing_importance_configuration_fails_safely():
    """7. Missing importance configuration fails safely."""
    # Construct match with invalid priority not in map
    m = _make_match()
    # Temporarily remove priority from map or simulate unmapped
    orig = PRIORITY_IMPORTANCE_MAP.copy()
    try:
        PRIORITY_IMPORTANCE_MAP.clear()
        with pytest.raises(MissingImportanceConfigurationError):
            resolve_requirement_importance(m)
    finally:
        PRIORITY_IMPORTANCE_MAP.update(orig)


# ==========================================
# 2. REQUIRED COMPONENT TESTS
# ==========================================

def test_correct_weighted_component_calculation():
    """1. Correct weighted component calculation."""
    # 2 skills:
    # Skill A: priority HIGH (3.0), match_value 1.0 -> earned 3.0
    # Skill B: priority MEDIUM (2.0), match_value 0.5 -> earned 1.0
    # Total importance = 5.0, total earned = 4.0 -> component_score = 4.0 / 5.0 = 0.8
    m1 = _make_match(category=RequirementCategory.SKILL, priority=RequirementPriority.HIGH, match_value=1.0, display_order=0)
    m2 = _make_match(category=RequirementCategory.SKILL, priority=RequirementPriority.MEDIUM, match_value=0.5, display_order=1)

    res = calculate_compatibility_score(_make_result_set([m1, m2]))
    comp = res.component_scores[RequirementCategory.SKILL]
    assert comp.status == "APPLICABLE"
    assert comp.requirement_count == 2
    assert comp.applicable_count == 2
    assert comp.total_importance == 5.0
    assert comp.earned_contribution == 4.0
    assert comp.raw_component_score == 0.8
    assert comp.display_component_score == 80.0


def test_all_matched_produces_component_score_one():
    """2. All matched produces component score 1.0."""
    m1 = _make_match(category=RequirementCategory.EXPERIENCE, match_value=1.0, display_order=0)
    m2 = _make_match(category=RequirementCategory.EXPERIENCE, match_value=1.0, display_order=1)

    res = calculate_compatibility_score(_make_result_set([m1, m2]))
    comp = res.component_scores[RequirementCategory.EXPERIENCE]
    assert comp.raw_component_score == 1.0
    assert comp.display_component_score == 100.0


def test_no_matches_produces_component_score_zero():
    """3. No matches produces component score 0.0."""
    m = _make_match(
        category=RequirementCategory.EDUCATION,
        result_state=MatchResultState.NOT_EVIDENCED,
        match_value=0.0,
        evidence_references=[],
    )
    res = calculate_compatibility_score(_make_result_set([m]))
    comp = res.component_scores[RequirementCategory.EDUCATION]
    assert comp.raw_component_score == 0.0
    assert comp.display_component_score == 0.0


def test_partial_values_calculated_correctly():
    """4. Partial values are calculated correctly."""
    m = _make_match(
        category=RequirementCategory.CERTIFICATION,
        result_state=MatchResultState.PARTIALLY_MATCHED,
        match_value=0.75,
        evidence_references=["ev-cert"],
    )
    res = calculate_compatibility_score(_make_result_set([m]))
    comp = res.component_scores[RequirementCategory.CERTIFICATION]
    assert comp.raw_component_score == 0.75
    assert comp.display_component_score == 75.0


def test_not_applicable_is_excluded_from_component():
    """5. NOT_APPLICABLE is excluded."""
    m1 = _make_match(category=RequirementCategory.SKILL, match_value=1.0, display_order=0)
    m2 = _make_match(
        category=RequirementCategory.SKILL,
        result_state=MatchResultState.NOT_APPLICABLE,
        match_value=0.0,
        evidence_references=[],
        display_order=1,
    )
    res = calculate_compatibility_score(_make_result_set([m1, m2]))
    comp = res.component_scores[RequirementCategory.SKILL]
    assert comp.requirement_count == 2
    assert comp.applicable_count == 1
    assert comp.raw_component_score == 1.0


def test_uncertain_states_remain_in_denominator():
    """6. Uncertain states do not disappear from the denominator."""
    # 1 MATCHED (imp 2.0), 1 INSUFFICIENT_EVIDENCE (imp 2.0)
    # Total importance = 4.0, earned = 2.0 -> score = 0.5
    m1 = _make_match(category=RequirementCategory.SKILL, priority=RequirementPriority.MEDIUM, match_value=1.0, display_order=0)
    m2 = _make_match(
        category=RequirementCategory.SKILL,
        priority=RequirementPriority.MEDIUM,
        result_state=MatchResultState.INSUFFICIENT_EVIDENCE,
        match_value=0.0,
        evidence_references=[],
        display_order=1,
    )
    res = calculate_compatibility_score(_make_result_set([m1, m2]))
    comp = res.component_scores[RequirementCategory.SKILL]
    assert comp.applicable_count == 2
    assert comp.total_importance == 4.0
    assert comp.raw_component_score == 0.5


def test_empty_component_marked_not_applicable():
    """7. Empty component is marked NOT_APPLICABLE."""
    m = _make_match(category=RequirementCategory.SKILL)
    res = calculate_compatibility_score(_make_result_set([m]))
    comp_lang = res.component_scores[RequirementCategory.LANGUAGE]
    assert comp_lang.status == "NOT_APPLICABLE"
    assert comp_lang.requirement_count == 0
    assert comp_lang.raw_component_score is None


def test_zero_denominator_configuration_fails_safely():
    """8. Zero-denominator configuration fails safely."""
    # All requirements have approved_weight 0.0
    m = _make_match(approved_weight=0.0)
    with pytest.raises(ZeroAggregateDenominatorError):
        calculate_compatibility_score(_make_result_set([m]))


# ==========================================
# 3. REQUIRED OVERALL SCORE TESTS
# ==========================================

def test_all_applicable_matched_produces_100():
    """1. All applicable requirements matched produces 100."""
    m1 = _make_match(category=RequirementCategory.SKILL, match_value=1.0, display_order=0)
    m2 = _make_match(category=RequirementCategory.EXPERIENCE, match_value=1.0, display_order=1)
    res = calculate_compatibility_score(_make_result_set([m1, m2]))
    assert res.raw_compatibility_score == 100.0
    assert res.display_compatibility_score == 100.0


def test_all_unmatched_produces_zero():
    """2. All unmatched produces 0."""
    m1 = _make_match(
        category=RequirementCategory.SKILL,
        result_state=MatchResultState.NOT_EVIDENCED,
        match_value=0.0,
        evidence_references=[],
        display_order=0,
    )
    m2 = _make_match(
        category=RequirementCategory.EXPERIENCE,
        result_state=MatchResultState.NOT_EVIDENCED,
        match_value=0.0,
        evidence_references=[],
        display_order=1,
    )
    res = calculate_compatibility_score(_make_result_set([m1, m2]))
    assert res.raw_compatibility_score == 0.0
    assert res.display_compatibility_score == 0.0


def test_mixed_requirements_produce_exact_expected_value():
    """3. Mixed requirements produce an exact expected value."""
    # Req 1: CRITICAL (4.0), match_value 1.0 -> 4.0 earned
    # Req 2: HIGH (3.0), match_value 0.5 -> 1.5 earned
    # Req 3: MEDIUM (2.0), match_value 0.0 -> 0.0 earned
    # Total importance = 9.0
    # Total earned = 5.5
    # Overall = 100 * (5.5 / 9.0) = 61.111111... -> display 61.11
    m1 = _make_match(priority=RequirementPriority.CRITICAL, match_value=1.0, display_order=0)
    m2 = _make_match(
        priority=RequirementPriority.HIGH,
        result_state=MatchResultState.PARTIALLY_MATCHED,
        match_value=0.5,
        display_order=1,
    )
    m3 = _make_match(
        priority=RequirementPriority.MEDIUM,
        result_state=MatchResultState.NOT_EVIDENCED,
        match_value=0.0,
        evidence_references=[],
        display_order=2,
    )
    res = calculate_compatibility_score(_make_result_set([m1, m2, m3]))
    assert abs(res.raw_compatibility_score - 61.111111) < 1e-5
    assert res.display_compatibility_score == 61.11


def test_score_remains_within_bounds():
    """4. Score remains within 0–100."""
    m = _make_match(match_value=1.0)
    res = calculate_compatibility_score(_make_result_set([m]))
    assert 0.0 <= res.raw_compatibility_score <= 100.0


def test_direct_requirement_weighted_formula_used():
    """5 & 6. Direct requirement-weighted formula is used when category weights do not exist."""
    m = _make_match()
    res = calculate_compatibility_score(_make_result_set([m]))
    assert res.calculation_method == ScoringCalculationMethod.REQUIREMENT_WEIGHTED_DIRECT


def test_inactive_categories_are_not_in_denominator():
    """7. Inactive categories do not dilute active categories."""
    # Only SKILL requirement exists. Overall score should be 100.0, not penalized by empty EDUCATION/EXPERIENCE
    m = _make_match(category=RequirementCategory.SKILL, match_value=1.0)
    res = calculate_compatibility_score(_make_result_set([m]))
    assert res.raw_compatibility_score == 100.0


def test_requirement_order_does_not_change_mathematical_result():
    """8. Requirement order does not change the mathematical result."""
    m1 = _make_match(priority=RequirementPriority.CRITICAL, match_value=1.0, display_order=0)
    m2 = _make_match(priority=RequirementPriority.LOW, match_value=0.5, display_order=1)

    res1 = calculate_compatibility_score(_make_result_set([m1, m2]))
    # Reverse input list
    res2 = calculate_compatibility_score(_make_result_set([m2, m1]))

    assert res1.raw_compatibility_score == res2.raw_compatibility_score
    assert res1.display_compatibility_score == res2.display_compatibility_score


def test_display_rounding_does_not_change_raw_value():
    """9. Display rounding does not change the raw value."""
    # 1.0 / 3.0 = 33.333333... %
    m1 = _make_match(priority=RequirementPriority.LOW, match_value=1.0, display_order=0)
    m2 = _make_match(
        priority=RequirementPriority.MEDIUM,
        result_state=MatchResultState.NOT_EVIDENCED,
        match_value=0.0,
        evidence_references=[],
        display_order=1,
    )
    res = calculate_compatibility_score(_make_result_set([m1, m2]))
    assert res.display_compatibility_score == 33.33
    assert res.raw_compatibility_score != 33.33
    assert abs(res.raw_compatibility_score - 33.333333) < 1e-5


def test_repeated_calculation_serializes_identically():
    """10. Repeated calculation serializes identically."""
    m1 = _make_match(priority=RequirementPriority.HIGH, match_value=1.0, display_order=0)
    m2 = _make_match(priority=RequirementPriority.MEDIUM, match_value=0.8, display_order=1)
    rs = _make_result_set([m1, m2])

    res1 = calculate_compatibility_score(rs)
    res2 = calculate_compatibility_score(rs)

    assert res1.model_dump(mode="json") == res2.model_dump(mode="json")


def test_small_custom_weight_change_has_expected_effect():
    """11. A small custom-weight change has the mathematically expected effect."""
    m1 = _make_match(approved_weight=2.0, match_value=1.0, display_order=0)
    m2 = _make_match(
        approved_weight=2.0,
        result_state=MatchResultState.NOT_EVIDENCED,
        match_value=0.0,
        evidence_references=[],
        display_order=1,
    )
    res1 = calculate_compatibility_score(_make_result_set([m1, m2]))
    # 2.0 / 4.0 = 50.0%
    assert res1.raw_compatibility_score == 50.0

    # Increase weight of matched requirement from 2.0 to 3.0 -> 3.0 / 5.0 = 60.0%
    m1_increased = _make_match(
        requirement_id=m1.requirement_id,
        approved_weight=3.0,
        match_value=1.0,
        display_order=0,
    )
    res2 = calculate_compatibility_score(_make_result_set([m1_increased, m2]))
    assert res2.raw_compatibility_score == 60.0


def test_no_pass_fail_threshold_is_produced():
    """12. No pass/fail threshold is produced."""
    m = _make_match(match_value=0.1)
    res = calculate_compatibility_score(_make_result_set([m]))
    data = res.model_dump(mode="json")
    assert "passed" not in data
    assert "failed" not in data
    assert "qualification" not in data
    assert "status" not in data or data.get("status") not in {"PASSED", "FAILED"}


# ==========================================
# 4. REQUIRED WARNING TESTS
# ==========================================

def test_matched_must_have_produces_no_warning():
    """1. Matched must-have produces no missing warning."""
    m = _make_match(necessity=RequirementNecessity.MUST_HAVE, result_state=MatchResultState.MATCHED, match_value=1.0)
    res = calculate_compatibility_score(_make_result_set([m]))
    assert len(res.must_have_warnings) == 0


def test_partial_must_have_produces_correct_warning():
    """2. Partial must-have produces the correct warning."""
    m = _make_match(
        necessity=RequirementNecessity.MUST_HAVE,
        result_state=MatchResultState.PARTIALLY_MATCHED,
        match_value=0.6,
    )
    res = calculate_compatibility_score(_make_result_set([m]))
    assert len(res.must_have_warnings) == 1
    w = res.must_have_warnings[0]
    assert w.code == ScoringWarningCode.MUST_HAVE_PARTIALLY_MATCHED
    assert "partially evidenced" in w.message


def test_not_evidenced_must_have_produces_correct_warning():
    """3. Not-evidenced must-have produces the correct warning."""
    m = _make_match(
        necessity=RequirementNecessity.MUST_HAVE,
        result_state=MatchResultState.NOT_EVIDENCED,
        match_value=0.0,
        evidence_references=[],
    )
    res = calculate_compatibility_score(_make_result_set([m]))
    assert len(res.must_have_warnings) == 1
    w = res.must_have_warnings[0]
    assert w.code == ScoringWarningCode.MUST_HAVE_NOT_EVIDENCED
    assert w.message == "No qualifying evidence was found in the current candidate profile."


def test_insufficient_evidence_must_have_produces_correct_warning():
    """4. Insufficient-evidence must-have produces the correct warning."""
    m = _make_match(
        necessity=RequirementNecessity.MUST_HAVE,
        result_state=MatchResultState.INSUFFICIENT_EVIDENCE,
        match_value=0.0,
        evidence_references=[],
    )
    res = calculate_compatibility_score(_make_result_set([m]))
    assert len(res.must_have_warnings) == 1
    w = res.must_have_warnings[0]
    assert w.code == ScoringWarningCode.MUST_HAVE_INSUFFICIENT_EVIDENCE


def test_conflicting_must_have_produces_correct_warning():
    """5. Conflicting must-have produces the correct warning."""
    m = _make_match(
        necessity=RequirementNecessity.MUST_HAVE,
        result_state=MatchResultState.CONFLICTING_EVIDENCE,
        match_value=0.0,
        evidence_references=[],
    )
    res = calculate_compatibility_score(_make_result_set([m]))
    assert len(res.must_have_warnings) == 1
    w = res.must_have_warnings[0]
    assert w.code == ScoringWarningCode.MUST_HAVE_CONFLICTING_EVIDENCE


def test_preferred_missing_requirement_does_not_produce_must_have_warning():
    """6. Preferred missing requirement does not produce a must-have warning."""
    m = _make_match(
        necessity=RequirementNecessity.PREFERRED,
        result_state=MatchResultState.NOT_EVIDENCED,
        match_value=0.0,
        evidence_references=[],
    )
    res = calculate_compatibility_score(_make_result_set([m]))
    assert len(res.must_have_warnings) == 0


def test_warning_does_not_change_compatibility_calculation():
    """7. Warning does not change the compatibility calculation."""
    # Two identical requirements in terms of priority and match value (0.0):
    # One is MUST_HAVE (generates warning), one is PREFERRED (no warning).
    # Both must result in 0.0 compatibility without arbitrary penalties.
    m_must = _make_match(
        necessity=RequirementNecessity.MUST_HAVE,
        result_state=MatchResultState.NOT_EVIDENCED,
        match_value=0.0,
        evidence_references=[],
    )
    m_pref = _make_match(
        necessity=RequirementNecessity.PREFERRED,
        result_state=MatchResultState.NOT_EVIDENCED,
        match_value=0.0,
        evidence_references=[],
    )

    res_must = calculate_compatibility_score(_make_result_set([m_must]))
    res_pref = calculate_compatibility_score(_make_result_set([m_pref]))

    assert res_must.raw_compatibility_score == res_pref.raw_compatibility_score == 0.0
    assert len(res_must.must_have_warnings) == 1
    assert len(res_pref.must_have_warnings) == 0


def test_warning_contains_no_rejection_language():
    """8. Warning contains no rejection language."""
    m = _make_match(
        necessity=RequirementNecessity.MUST_HAVE,
        result_state=MatchResultState.NOT_EVIDENCED,
        match_value=0.0,
        evidence_references=[],
    )
    res = calculate_compatibility_score(_make_result_set([m]))
    for w in res.must_have_warnings:
        msg_lower = w.message.lower()
        for forbidden in ["unqualified", "reject", "failed", "should not proceed", "lacks"]:
            assert forbidden not in msg_lower


def test_no_automatic_decision_is_created():
    """9. No automatic decision is created."""
    m = _make_match(
        necessity=RequirementNecessity.MUST_HAVE,
        result_state=MatchResultState.NOT_EVIDENCED,
        match_value=0.0,
        evidence_references=[],
    )
    res = calculate_compatibility_score(_make_result_set([m]))
    assert res.decision is None
    assert res.automatic_rejection is False


# ==========================================
# 5. REQUIRED SEPARATION / PRIVACY TESTS
# ==========================================

def test_confidence_changes_do_not_change_compatibility():
    """1. Confidence changes do not change compatibility."""
    # Match with high confidence
    m1 = _make_match(match_value=1.0)
    res1 = calculate_compatibility_score(_make_result_set([m1]))

    # Simulate lower confidence by introducing review_recommended or snapshot warning
    m2 = _make_match(requirement_id=m1.requirement_id, match_value=1.0, review_recommended=True)
    res2 = calculate_compatibility_score(_make_result_set([m2]))

    assert res1.raw_compatibility_score == res2.raw_compatibility_score == 100.0


def test_compatibility_changes_do_not_fabricate_confidence():
    """2. Compatibility changes do not fabricate confidence."""
    # High score with conflicting evidence should NOT have HIGH confidence
    m1 = _make_match(priority=RequirementPriority.CRITICAL, match_value=1.0, display_order=0)
    m2 = _make_match(
        priority=RequirementPriority.LOW,
        result_state=MatchResultState.CONFLICTING_EVIDENCE,
        match_value=0.0,
        evidence_references=[],
        display_order=1,
    )
    res = calculate_compatibility_score(_make_result_set([m1, m2]))
    # Compatibility is 80% (4.0 / 5.0)
    assert res.raw_compatibility_score == 80.0
    # Confidence must reflect the conflict (NOT HIGH)
    assert res.confidence_diagnostics.confidence_tier != AssessmentConfidenceTier.HIGH
    assert res.confidence_diagnostics.conflicting_evidence_count == 1


def test_identity_modifications_do_not_change_the_score():
    """3. Identity modifications do not change the score."""
    m = _make_match(match_value=1.0)
    rs1 = _make_result_set([m])
    rs2 = _make_result_set([m])
    # Different candidate profile IDs
    rs2 = rs2.model_copy(update={"profile_id": uuid.uuid4()})

    res1 = calculate_compatibility_score(rs1)
    res2 = calculate_compatibility_score(rs2)

    assert res1.raw_compatibility_score == res2.raw_compatibility_score


def test_serialized_scoring_result_contains_no_prohibited_identity_fields():
    """4. Serialized scoring result contains no prohibited identity fields."""
    m = _make_match()
    res = calculate_compatibility_score(_make_result_set([m]))
    data = res.model_dump(mode="json")
    for prohibited in ["full_name", "candidate_name", "email", "phone", "gender", "race", "dob"]:
        assert prohibited not in data


def test_raw_resume_and_raw_job_description_text_are_absent():
    """5. Raw resume and raw job-description text are absent."""
    m = _make_match()
    res = calculate_compatibility_score(_make_result_set([m]))
    data = res.model_dump(mode="json")
    for raw_key in ["raw_text", "raw_bytes", "raw_resume_text", "job_description_text"]:
        assert raw_key not in data


def test_exact_versions_are_retained():
    """6. Exact job/profile/taxonomy/matcher/scoring-policy versions are retained."""
    m = _make_match()
    rs = _make_result_set([m])
    res = calculate_compatibility_score(rs)

    assert res.scoring_policy_version == SCORING_POLICY_VERSION
    assert res.matcher_algorithm_version == rs.matcher_algorithm_version
    assert res.match_input_schema_version == rs.match_input_schema_version
    assert res.job_version_id == rs.job_version_id
    assert res.profile_id == rs.profile_id
    assert res.profile_version == rs.profile_version


def test_every_credited_contribution_traces_to_match_and_evidence():
    """7. Every credited contribution traces to a RequirementMatch and evidence reference."""
    ev_id = "ev-trace-1"
    m = _make_match(match_value=1.0, evidence_references=[ev_id])
    res = calculate_compatibility_score(_make_result_set([m]))

    contrib = res.requirement_contributions[0]
    assert contrib.requirement_id == m.requirement_id
    assert contrib.evidence_references == [ev_id]
    assert contrib.weighted_contribution == 100.0
