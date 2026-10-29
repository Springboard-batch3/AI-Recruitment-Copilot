import copy
import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest

from app.models.job import (
    RequirementCategory,
    RequirementNecessity,
    RequirementPriority,
)
from app.models.matching import (
    MatchResultRecord,
    MatchRunLifecycleStatus,
    MatchRunRecord,
)
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
from app.services.gap_derivation import (
    GapDerivationError,
    derive_requirement_gap_record,
    derive_requirement_gaps,
)
from app.services.gap_validation import validate_and_adapt_match_result_to_gap_set


def _build_test_environment(contributions=None, evaluation_date=None, must_have_warnings=None):
    run_id = uuid.uuid4()
    job_id = uuid.uuid4()
    job_version_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    result_id = uuid.uuid4()
    eval_date = evaluation_date or datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

    run_record = MatchRunRecord(
        run_id=run_id,
        recruiter_id=uuid.uuid4(),
        job_id=job_id,
        job_version_id=job_version_id,
        revision=1,
        evaluation_date=eval_date,
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="1.0.0",
        scoring_policy_version="phase2c3_scoring_v1",
        candidate_input_fingerprint="abc",
        input_fingerprint="def",
        idempotency_key=f"idemp-{uuid.uuid4()}",
        lifecycle_status=MatchRunLifecycleStatus.SUCCEEDED,
        candidate_count=1,
    )

    if contributions is None:
        contributions = [
            {
                "requirement_id": str(uuid.uuid4()),
                "display_order": 0,
                "category": RequirementCategory.SKILL.value,
                "necessity": RequirementNecessity.MUST_HAVE.value,
                "priority": RequirementPriority.CRITICAL.value,
                "match_state": MatchResultState.MATCHED.value,
                "match_value": 1.0,
                "evidence_references": ["resume.pdf#ev1"],
                "expected_value": "Python",
                "observed_value": "Python",
                "review_recommended": False,
            },
            {
                "requirement_id": str(uuid.uuid4()),
                "display_order": 1,
                "category": RequirementCategory.EXPERIENCE.value,
                "necessity": RequirementNecessity.PREFERRED.value,
                "priority": RequirementPriority.HIGH.value,
                "match_state": MatchResultState.PARTIALLY_MATCHED.value,
                "match_value": 0.6667,
                "evidence_references": ["resume.pdf#ev2"],
                "expected_value": "36 months",
                "observed_value": "24 months (Software Engineer)",
                "reason_codes": ["EXPERIENCE_BELOW_THRESHOLD_24_OF_36_MONTHS"],
                "review_recommended": False,
            },
            {
                "requirement_id": str(uuid.uuid4()),
                "display_order": 2,
                "category": RequirementCategory.CERTIFICATION.value,
                "necessity": RequirementNecessity.PREFERRED.value,
                "priority": RequirementPriority.MEDIUM.value,
                "match_state": MatchResultState.NOT_EVIDENCED.value,
                "match_value": 0.0,
                "evidence_references": [],
                "expected_value": "AWS Solutions Architect",
                "observed_value": None,
                "review_recommended": False,
            },
        ]

    decomp = {
        "scoring_policy_version": "phase2c3_scoring_v1",
        "matcher_algorithm_version": "1.0.0",
        "match_input_schema_version": "1.0.0",
        "requisition_id": str(job_id),
        "job_version_id": str(job_version_id),
        "revision": 1,
        "profile_id": str(profile_id),
        "profile_version": 1,
        "raw_compatibility_score": 75.0,
        "display_compatibility_score": 75.0,
        "requirement_contributions": contributions,
        "must_have_warnings": must_have_warnings or [],
    }

    result_record = MatchResultRecord(
        result_id=result_id,
        run_id=run_id,
        profile_id=profile_id,
        profile_version=1,
        raw_compatibility_score=Decimal("75.000000"),
        display_compatibility_score=Decimal("75.00"),
        confidence_tier="HIGH",
        rank=1,
        is_tied=False,
        score_decomposition=decomp,
    )

    validated_gap_set = validate_and_adapt_match_result_to_gap_set(result_record, run_record)
    return validated_gap_set, decomp, run_record, result_record


# 1. One gap record is produced for every source requirement result
def test_one_gap_record_per_source_requirement():
    gap_set, decomp, _, _ = _build_test_environment()
    derived = derive_requirement_gaps(gap_set, decomp)

    assert len(derived.gaps) == len(gap_set.gaps)
    assert derived.total_requirements == len(gap_set.gaps)
    assert derived.total_requirements == 3


# 2. Requirement IDs and stored order are preserved
def test_requirement_ids_and_order_preserved():
    gap_set, decomp, _, _ = _build_test_environment()
    derived = derive_requirement_gaps(gap_set, decomp)

    for src_gap, der_gap in zip(gap_set.gaps, derived.gaps):
        assert src_gap.requirement_id == der_gap.requirement_id
        assert src_gap.display_order == der_gap.display_order
        assert der_gap.display_order in [0, 1, 2]


# 3. Every supported state produces correct gap state and controlled wording
def test_supported_states_produce_correct_gap_state_and_wording():
    states_to_test = [
        (MatchResultState.MATCHED, RequirementGapState.COVERED, 1.0, ["ev1"], "Documented evidence met this requirement."),
        (MatchResultState.PARTIALLY_MATCHED, RequirementGapState.PARTIALLY_COVERED, 0.5, ["ev2"], "Documented evidence partially met the configured requirement."),
        (MatchResultState.NOT_EVIDENCED, RequirementGapState.NOT_EVIDENCED, 0.0, [], "No qualifying evidence was found in the selected profile version."),
        (MatchResultState.INSUFFICIENT_EVIDENCE, RequirementGapState.UNVERIFIED, 0.0, ["ev3"], "Possible relevant evidence was identified, but it requires human confirmation before credit can be assigned."),
        (MatchResultState.CONFLICTING_EVIDENCE, RequirementGapState.CONFLICTING_REVIEW_REQUIRED, 0.0, ["ev4"], "Relevant evidence was inconsistent. Review the linked evidence before using this finding."),
        (MatchResultState.NOT_APPLICABLE, RequirementGapState.NOT_APPLICABLE, 0.0, [], "This requirement was excluded from active evaluation and is retained for audit."),
    ]

    contributions = []
    for idx, (m_state, _, m_val, ev_refs, _) in enumerate(states_to_test):
        contributions.append({
            "requirement_id": str(uuid.uuid4()),
            "display_order": idx,
            "category": RequirementCategory.SKILL.value,
            "necessity": RequirementNecessity.PREFERRED.value,
            "priority": RequirementPriority.MEDIUM.value,
            "match_state": m_state.value,
            "match_value": m_val,
            "evidence_references": ev_refs,
            "expected_value": "Req Label",
            "observed_value": "Doc Val" if ev_refs else None,
        })

    gap_set, decomp, _, _ = _build_test_environment(contributions=contributions)
    derived = derive_requirement_gaps(gap_set, decomp)

    assert len(derived.gaps) == len(states_to_test)
    for idx, (_, expected_gap_state, _, _, expected_prefix) in enumerate(states_to_test):
        gap = derived.gaps[idx]
        assert gap.gap_state == expected_gap_state
        assert gap.recruiter_wording.startswith(expected_prefix)


# 4. NOT_APPLICABLE remains present for audit
def test_not_applicable_remains_present():
    contributions = [
        {
            "requirement_id": str(uuid.uuid4()),
            "display_order": 0,
            "category": RequirementCategory.OTHER.value,
            "necessity": RequirementNecessity.PREFERRED.value,
            "priority": RequirementPriority.LOW.value,
            "match_state": MatchResultState.NOT_APPLICABLE.value,
            "match_value": 0.0,
            "evidence_references": [],
        }
    ]
    gap_set, decomp, _, _ = _build_test_environment(contributions=contributions)
    derived = derive_requirement_gaps(gap_set, decomp)

    assert len(derived.gaps) == 1
    gap = derived.gaps[0]
    assert gap.gap_state == RequirementGapState.NOT_APPLICABLE
    assert gap.measurable_shortfall_value is None
    assert gap.review_action_code == GapReviewActionCode.NO_ACTION_REQUIRED
    assert "retained for audit" in gap.recruiter_wording


# 5. Experience shortfall is calculated correctly
def test_experience_shortfall_calculated_correctly():
    contributions = [
        {
            "requirement_id": str(uuid.uuid4()),
            "display_order": 0,
            "category": RequirementCategory.EXPERIENCE.value,
            "necessity": RequirementNecessity.MUST_HAVE.value,
            "priority": RequirementPriority.HIGH.value,
            "match_state": MatchResultState.PARTIALLY_MATCHED.value,
            "match_value": 0.5,
            "evidence_references": ["job.pdf#1"],
            "expected_value": "48 months",
            "observed_value": "24 months (Lead Developer)",
            "reason_codes": ["EXPERIENCE_BELOW_THRESHOLD_24_OF_48_MONTHS"],
        }
    ]
    gap_set, decomp, _, _ = _build_test_environment(contributions=contributions)
    derived = derive_requirement_gaps(gap_set, decomp)

    gap = derived.gaps[0]
    assert gap.measurable_shortfall_value == 24.0
    assert gap.shortfall_unit == "months"
    assert gap.shortfall_reason_code == "DURATION_BELOW_THRESHOLD"
    assert "24-month difference to verify against the configured 48-month target" in gap.recruiter_wording
    assert gap.review_action_code == GapReviewActionCode.VERIFY_THRESHOLD_SHORTFALL


# 6. Experience above the threshold produces zero shortfall
def test_experience_above_threshold_produces_zero_shortfall():
    contributions = [
        {
            "requirement_id": str(uuid.uuid4()),
            "display_order": 0,
            "category": RequirementCategory.EXPERIENCE.value,
            "necessity": RequirementNecessity.MUST_HAVE.value,
            "priority": RequirementPriority.HIGH.value,
            "match_state": MatchResultState.MATCHED.value,
            "match_value": 1.0,
            "evidence_references": ["job.pdf#1"],
            "expected_value": "36 months",
            "observed_value": "48 months (Senior Engineer)",
        }
    ]
    gap_set, decomp, _, _ = _build_test_environment(contributions=contributions)
    derived = derive_requirement_gaps(gap_set, decomp)

    gap = derived.gaps[0]
    assert gap.measurable_shortfall_value == 0.0
    assert gap.shortfall_unit == "months"
    assert gap.shortfall_reason_code == "THRESHOLD_SATISFIED"
    assert gap.gap_state == RequirementGapState.COVERED
    assert gap.review_action_code == GapReviewActionCode.NO_ACTION_REQUIRED


# 7. Missing or uncertain numeric inputs produce null plus an explicit reason
def test_uncertain_numeric_inputs_produce_null_with_reason():
    contributions = [
        {
            "requirement_id": str(uuid.uuid4()),
            "display_order": 0,
            "category": RequirementCategory.EXPERIENCE.value,
            "necessity": RequirementNecessity.MUST_HAVE.value,
            "priority": RequirementPriority.HIGH.value,
            "match_state": MatchResultState.INSUFFICIENT_EVIDENCE.value,
            "match_value": 0.0,
            "evidence_references": ["job.pdf#1"],
            "expected_value": "24 months",
            "observed_value": "Engineer",
            "reason_codes": ["IMPRECISE_DATE_PRECISION"],
        },
        {
            "requirement_id": str(uuid.uuid4()),
            "display_order": 1,
            "category": RequirementCategory.EXPERIENCE.value,
            "necessity": RequirementNecessity.MUST_HAVE.value,
            "priority": RequirementPriority.HIGH.value,
            "match_state": MatchResultState.CONFLICTING_EVIDENCE.value,
            "match_value": 0.0,
            "evidence_references": ["job.pdf#2"],
            "expected_value": "24 months",
            "observed_value": "Engineer",
            "reason_codes": ["INVERTED_DATE_INTERVAL"],
        },
    ]
    gap_set, decomp, _, _ = _build_test_environment(contributions=contributions)
    derived = derive_requirement_gaps(gap_set, decomp)

    # Imprecise dates -> UNVERIFIED
    g1 = derived.gaps[0]
    assert g1.measurable_shortfall_value is None
    assert g1.shortfall_reason_code == "UNVERIFIED_DATE_PRECISION"
    assert g1.review_action_code == GapReviewActionCode.CONFIRM_UNCERTAIN_EVIDENCE

    # Conflicting dates -> CONFLICTING_REVIEW_REQUIRED
    g2 = derived.gaps[1]
    assert g2.measurable_shortfall_value is None
    assert g2.shortfall_reason_code == "CONFLICTING_TIMELINE_INTERVALS"
    assert g2.review_action_code == GapReviewActionCode.RESOLVE_EVIDENCE_CONFLICT


# 8. Historical recency uses the stored evaluation date
def test_historical_recency_uses_stored_evaluation_date():
    # Stored evaluation date in the past
    past_date = datetime(2023, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    contributions = [
        {
            "requirement_id": str(uuid.uuid4()),
            "display_order": 0,
            "category": RequirementCategory.EXPERIENCE.value,
            "necessity": RequirementNecessity.MUST_HAVE.value,
            "priority": RequirementPriority.HIGH.value,
            "match_state": MatchResultState.PARTIALLY_MATCHED.value,
            "match_value": 0.5,
            "evidence_references": ["job.pdf#1"],
            "expected_value": "24 months",
            "observed_value": "24 months",
            "reason_codes": ["RECENCY_THRESHOLD_EXCEEDED"],
        }
    ]
    gap_set, decomp, _, _ = _build_test_environment(contributions=contributions, evaluation_date=past_date)
    derived = derive_requirement_gaps(gap_set, decomp)

    assert derived.evaluation_date == past_date
    assert derived.gaps[0].evaluation_date == past_date


# 9. Non-quantifiable requirements do not receive invented percentages
def test_non_quantifiable_requirements_do_not_receive_invented_percentages():
    non_quant_categories = [
        (RequirementCategory.SKILL, "Python"),
        (RequirementCategory.EDUCATION, "B.S. Computer Science"),
        (RequirementCategory.CERTIFICATION, "AWS Certified Developer"),
        (RequirementCategory.RESPONSIBILITY, "Led engineering team"),
        (RequirementCategory.LANGUAGE, "English"),
        (RequirementCategory.OTHER, "Portfolio"),
    ]
    contributions = []
    for idx, (cat, val) in enumerate(non_quant_categories):
        contributions.append({
            "requirement_id": str(uuid.uuid4()),
            "display_order": idx,
            "category": cat.value,
            "necessity": RequirementNecessity.MUST_HAVE.value,
            "priority": RequirementPriority.HIGH.value,
            "match_state": MatchResultState.PARTIALLY_MATCHED.value,
            "match_value": 0.5,  # Phase 2C match value must NOT be converted to 50% knowledge gap
            "evidence_references": [f"ev_{idx}"],
            "expected_value": val,
            "observed_value": f"Observed {val}",
        })

    gap_set, decomp, _, _ = _build_test_environment(contributions=contributions)
    derived = derive_requirement_gaps(gap_set, decomp)

    for gap in derived.gaps:
        assert gap.measurable_shortfall_value is None
        assert gap.shortfall_unit is None
        assert "%" not in gap.recruiter_wording


# 10. Evidence, warnings and uncertainty are preserved
def test_evidence_warnings_and_uncertainty_preserved():
    warning_code = "MUST_HAVE_PARTIALLY_MATCHED"
    req_id = uuid.uuid4()
    contributions = [
        {
            "requirement_id": str(req_id),
            "display_order": 0,
            "category": RequirementCategory.SKILL.value,
            "necessity": RequirementNecessity.MUST_HAVE.value,
            "priority": RequirementPriority.HIGH.value,
            "match_state": MatchResultState.PARTIALLY_MATCHED.value,
            "match_value": 0.75,
            "evidence_references": ["p1.pdf#evA", "p1.pdf#evB"],
            "expected_value": "Docker",
            "observed_value": "Basic Docker",
            "review_recommended": True,
        }
    ]
    must_have_warnings = [
        {
            "code": warning_code,
            "requirement_id": str(req_id),
            "message": "Must-have warning",
        }
    ]

    gap_set, decomp, _, _ = _build_test_environment(
        contributions=contributions,
        must_have_warnings=must_have_warnings,
    )
    derived = derive_requirement_gaps(gap_set, decomp)

    gap = derived.gaps[0]
    assert gap.evidence_references == ["p1.pdf#evA", "p1.pdf#evB"]
    assert gap.must_have_warning_reference == warning_code
    assert gap.review_required is True


# 11. Must-have findings never create a rejection
def test_must_have_findings_never_create_rejection():
    contributions = [
        {
            "requirement_id": str(uuid.uuid4()),
            "display_order": 0,
            "category": RequirementCategory.SKILL.value,
            "necessity": RequirementNecessity.MUST_HAVE.value,
            "priority": RequirementPriority.CRITICAL.value,
            "match_state": MatchResultState.NOT_EVIDENCED.value,
            "match_value": 0.0,
            "evidence_references": [],
            "expected_value": "Go",
            "observed_value": None,
        }
    ]
    gap_set, decomp, _, _ = _build_test_environment(contributions=contributions)
    derived = derive_requirement_gaps(gap_set, decomp)

    gap = derived.gaps[0]
    assert gap.gap_state == RequirementGapState.NOT_EVIDENCED
    # Ensure no rejection fields or language exist
    assert "reject" not in gap.recruiter_wording.lower()
    assert "unqualified" not in gap.recruiter_wording.lower()
    assert not hasattr(gap, "rejection_decision")
    assert not hasattr(gap, "hiring_decision")


# 12. Prohibited language is strictly absent
def test_prohibited_language_strictly_absent():
    gap_set, decomp, _, _ = _build_test_environment()
    derived = derive_requirement_gaps(gap_set, decomp)

    prohibited = [
        "unqualified", "reject", "hire", "best candidate", "worst candidate",
        "culture fit", "cultural fit", "poor fit", "intelligence", "honesty",
        "personality", "lacks a skill", "lacks skill"
    ]

    for gap in derived.gaps:
        verify_no_prohibited_decision_phrasing(gap.recruiter_wording)
        for term in prohibited:
            assert term not in gap.recruiter_wording.lower()


# 13. Repeated derivation produces identical output (determinism)
def test_repeated_derivation_produces_identical_output():
    gap_set, decomp, _, _ = _build_test_environment()
    run1 = derive_requirement_gaps(gap_set, decomp)
    run2 = derive_requirement_gaps(gap_set, decomp)

    assert run1.model_dump(mode="json") == run2.model_dump(mode="json")


# 14. Input structures are not mutated
def test_input_structures_not_mutated():
    gap_set, decomp, _, _ = _build_test_environment()
    orig_gap_set = copy.deepcopy(gap_set.model_dump(mode="json"))
    orig_decomp = copy.deepcopy(decomp)

    derive_requirement_gaps(gap_set, decomp)

    assert gap_set.model_dump(mode="json") == orig_gap_set
    assert decomp == orig_decomp


# 15. Identity changes cannot change derived output
def test_identity_independence():
    # Gap derivation does not accept nor read identity fields
    gap_set, decomp, _, _ = _build_test_environment()
    res1 = derive_requirement_gaps(gap_set, decomp)

    # Re-running with same requirement provenance
    res2 = derive_requirement_gaps(gap_set, decomp)
    assert res1.gaps == res2.gaps


# 16. Phase 2C scores, confidence and rank remain unchanged
def test_phase_2c_scores_remain_unchanged():
    gap_set, decomp, run_rec, res_rec = _build_test_environment()
    orig_score = float(res_rec.raw_compatibility_score)
    orig_rank = res_rec.rank
    orig_tier = res_rec.confidence_tier

    derived = derive_requirement_gaps(gap_set, decomp)

    # Derived set does not recalculate or touch score records
    assert float(res_rec.raw_compatibility_score) == orig_score
    assert res_rec.rank == orig_rank
    assert res_rec.confidence_tier == orig_tier


# 17. Partial derivation is not returned when one record fails
def test_partial_derivation_not_returned_on_failure():
    gap_set, decomp, _, _ = _build_test_environment()
    # Remove one requirement contribution from decomposition so lookup fails
    decomp["requirement_contributions"] = decomp["requirement_contributions"][:1]

    with pytest.raises(GapDerivationError) as exc:
        derive_requirement_gaps(gap_set, decomp)
    assert exc.value.code == "GAP_SOURCE_REQUIREMENT_MISSING"
