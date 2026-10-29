import copy
import uuid
from uuid import UUID
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
    MATCH_STATE_TO_GAP_STATE_MAP,
    RequirementGapRecord,
    RequirementGapSetContract,
    RequirementGapState,
)
from app.schemas.requirement_match import MatchResultState
from app.services.gap_validation import (
    GapSourceValidationError,
    UnsupportedMatchResultStateError,
    map_match_state_to_gap_state,
    validate_and_adapt_match_result_to_gap_set,
)


def _build_test_records(status=MatchRunLifecycleStatus.SUCCEEDED, contributions=None, must_have_warnings=None):
    run_id = uuid.uuid4()
    job_id = uuid.uuid4()
    job_version_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    result_id = uuid.uuid4()
    eval_date = datetime.now(timezone.utc)

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
        idempotency_key="test-key-1",
        lifecycle_status=status,
        candidate_count=1,
    )

    req1_id = uuid.uuid4()
    req2_id = uuid.uuid4()

    if contributions is None:
        contributions = [
            {
                "requirement_id": str(req1_id),
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
                "requirement_id": str(req2_id),
                "display_order": 1,
                "category": RequirementCategory.EXPERIENCE.value,
                "necessity": RequirementNecessity.PREFERRED.value,
                "priority": RequirementPriority.MEDIUM.value,
                "match_state": MatchResultState.NOT_EVIDENCED.value,
                "match_value": 0.0,
                "evidence_references": [],
                "expected_value": "Fintech",
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
        "raw_compatibility_score": 80.0,
        "display_compatibility_score": 80.0,
        "requirement_contributions": contributions,
        "must_have_warnings": must_have_warnings or [],
    }

    result_record = MatchResultRecord(
        result_id=result_id,
        run_id=run_id,
        profile_id=profile_id,
        profile_version=1,
        raw_compatibility_score=Decimal("80.000000"),
        display_compatibility_score=Decimal("80.00"),
        confidence_tier="HIGH",
        rank=1,
        is_tied=False,
        score_decomposition=decomp,
    )

    return run_record, result_record


# 1. Every implemented Phase 2C state maps to exactly one gap state
def test_all_implemented_states_map_exhaustively():
    expected_mappings = {
        MatchResultState.MATCHED: RequirementGapState.COVERED,
        MatchResultState.PARTIALLY_MATCHED: RequirementGapState.PARTIALLY_COVERED,
        MatchResultState.NOT_EVIDENCED: RequirementGapState.NOT_EVIDENCED,
        MatchResultState.INSUFFICIENT_EVIDENCE: RequirementGapState.UNVERIFIED,
        MatchResultState.CONFLICTING_EVIDENCE: RequirementGapState.CONFLICTING_REVIEW_REQUIRED,
        MatchResultState.NOT_APPLICABLE: RequirementGapState.NOT_APPLICABLE,
    }

    # Ensure exhaustive enumeration over enum
    assert set(MatchResultState) == set(expected_mappings.keys())

    for source_state, expected_gap_state in expected_mappings.items():
        mapped = map_match_state_to_gap_state(source_state)
        assert mapped == expected_gap_state
        assert MATCH_STATE_TO_GAP_STATE_MAP[source_state] == expected_gap_state


# 2. An unknown source state fails safely
def test_unknown_source_state_fails_safely():
    with pytest.raises(UnsupportedMatchResultStateError) as exc:
        map_match_state_to_gap_state("SOME_UNKNOWN_STATE")
    assert exc.value.code == "UNSUPPORTED_MATCH_RESULT_STATE"

    with pytest.raises(UnsupportedMatchResultStateError) as exc:
        map_match_state_to_gap_state(None)
    assert exc.value.code == "UNSUPPORTED_MATCH_RESULT_STATE"


# 3. A successful complete match result passes validation
def test_successful_complete_match_result_passes_validation():
    run_rec, res_rec = _build_test_records(
        must_have_warnings=[
            {
                "code": "MUST_HAVE_PARTIALLY_MATCHED",
                "requirement_id": str(uuid.uuid4()),
                "message": "Warning test",
            }
        ]
    )

    gap_set = validate_and_adapt_match_result_to_gap_set(res_rec, run_rec)

    assert isinstance(gap_set, RequirementGapSetContract)
    assert gap_set.gap_schema_version == GAP_SCHEMA_VERSION
    assert gap_set.gap_policy_version == GAP_POLICY_VERSION
    assert gap_set.total_requirements == 2
    assert len(gap_set.gaps) == 2

    g1 = gap_set.gaps[0]
    assert g1.gap_state == RequirementGapState.COVERED
    assert g1.source_match_state == MatchResultState.MATCHED
    assert g1.source_match_value == 1.0
    assert g1.evidence_references == ["resume.pdf#ev1"]
    assert g1.display_order == 0

    g2 = gap_set.gaps[1]
    assert g2.gap_state == RequirementGapState.NOT_EVIDENCED
    assert g2.source_match_state == MatchResultState.NOT_EVIDENCED
    assert g2.source_match_value == 0.0
    assert g2.evidence_references == []
    assert g2.display_order == 1


# 4. Failed, running or incomplete match runs are rejected
@pytest.mark.parametrize("bad_status", [
    MatchRunLifecycleStatus.FAILED,
    MatchRunLifecycleStatus.RUNNING,
    MatchRunLifecycleStatus.PENDING,
])
def test_non_succeeded_runs_are_rejected(bad_status):
    run_rec, res_rec = _build_test_records(status=bad_status)
    with pytest.raises(GapSourceValidationError) as exc:
        validate_and_adapt_match_result_to_gap_set(res_rec, run_rec)
    assert exc.value.code == "GAP_SOURCE_RUN_NOT_SUCCEEDED"


# 5. Missing requirement result is detected when matching against job_version
def test_missing_requirement_result_detected():
    run_rec, res_rec = _build_test_records()

    class MockJobRequirement:
        def __init__(self, req_id):
            self.requirement_id = req_id

    class MockJobVersion:
        def __init__(self, job_version_id, requirements):
            self.job_version_id = job_version_id
            self.requirements = requirements

    # Provide a JobVersion that expects 3 requirements, while match result only has 2
    req3_id = uuid.uuid4()
    existing_req_ids = [UUID(c["requirement_id"]) for c in res_rec.score_decomposition["requirement_contributions"]]
    job_ver = MockJobVersion(
        job_version_id=run_rec.job_version_id,
        requirements=[MockJobRequirement(existing_req_ids[0]), MockJobRequirement(existing_req_ids[1]), MockJobRequirement(req3_id)],
    )

    with pytest.raises(GapSourceValidationError) as exc:
        validate_and_adapt_match_result_to_gap_set(res_rec, run_rec, job_version=job_ver)
    assert exc.value.code == "GAP_SOURCE_REQUIREMENT_MISSING"


# 6. Duplicate requirement results are detected
def test_duplicate_requirement_results_detected():
    dup_id = uuid.uuid4()
    contributions = [
        {
            "requirement_id": str(dup_id),
            "display_order": 0,
            "category": RequirementCategory.SKILL.value,
            "necessity": RequirementNecessity.MUST_HAVE.value,
            "priority": RequirementPriority.HIGH.value,
            "match_state": MatchResultState.MATCHED.value,
            "match_value": 1.0,
            "evidence_references": ["ev#1"],
        },
        {
            "requirement_id": str(dup_id),
            "display_order": 1,
            "category": RequirementCategory.SKILL.value,
            "necessity": RequirementNecessity.MUST_HAVE.value,
            "priority": RequirementPriority.HIGH.value,
            "match_state": MatchResultState.MATCHED.value,
            "match_value": 1.0,
            "evidence_references": ["ev#2"],
        },
    ]
    run_rec, res_rec = _build_test_records(contributions=contributions)

    with pytest.raises(GapSourceValidationError) as exc:
        validate_and_adapt_match_result_to_gap_set(res_rec, run_rec)
    assert exc.value.code == "GAP_SOURCE_DUPLICATE_REQUIREMENT"


# 7. Missing version or evaluation provenance is rejected
def test_missing_version_provenance_rejected():
    run_rec, res_rec = _build_test_records()
    run_rec.matcher_algorithm_version = ""

    with pytest.raises(GapSourceValidationError) as exc:
        validate_and_adapt_match_result_to_gap_set(res_rec, run_rec)
    assert exc.value.code == "GAP_SOURCE_VERSION_MISSING"


# 8. Invalid credited-evidence references are rejected
def test_invalid_credited_evidence_rejected():
    # MATCHED without evidence references
    bad_contrib = [
        {
            "requirement_id": str(uuid.uuid4()),
            "display_order": 0,
            "category": RequirementCategory.SKILL.value,
            "necessity": RequirementNecessity.MUST_HAVE.value,
            "priority": RequirementPriority.HIGH.value,
            "match_state": MatchResultState.MATCHED.value,
            "match_value": 1.0,
            "evidence_references": [],  # Empty!
        }
    ]
    run_rec, res_rec = _build_test_records(contributions=bad_contrib)
    with pytest.raises(GapSourceValidationError) as exc:
        validate_and_adapt_match_result_to_gap_set(res_rec, run_rec)
    assert exc.value.code == "GAP_SOURCE_EVIDENCE_INVALID"

    # NOT_EVIDENCED with evidence references
    bad_contrib_ne = [
        {
            "requirement_id": str(uuid.uuid4()),
            "display_order": 0,
            "category": RequirementCategory.SKILL.value,
            "necessity": RequirementNecessity.MUST_HAVE.value,
            "priority": RequirementPriority.HIGH.value,
            "match_state": MatchResultState.NOT_EVIDENCED.value,
            "match_value": 0.0,
            "evidence_references": ["some_evidence#ref"],  # Should be empty!
        }
    ]
    run_rec_ne, res_rec_ne = _build_test_records(contributions=bad_contrib_ne)
    with pytest.raises(GapSourceValidationError) as exc:
        validate_and_adapt_match_result_to_gap_set(res_rec_ne, run_rec_ne)
    assert exc.value.code == "GAP_SOURCE_EVIDENCE_INVALID"


# 9. Identity, contact and raw-document fields are rejected recursively
@pytest.mark.parametrize("prohibited_key", [
    "candidate_name",
    "email",
    "phone",
    "date_of_birth",
    "raw_resume_text",
    "job_description_text",
    "religion",
])
def test_prohibited_identity_fields_rejected(prohibited_key):
    run_rec, res_rec = _build_test_records()
    res_rec.score_decomposition[prohibited_key] = "prohibited_value"

    with pytest.raises(GapSourceValidationError) as exc:
        validate_and_adapt_match_result_to_gap_set(res_rec, run_rec)
    assert exc.value.code == "GAP_SOURCE_PROHIBITED_FIELD"


# 10. Validation does not mutate the Phase 2C input
def test_validation_does_not_mutate_input():
    run_rec, res_rec = _build_test_records()
    orig_decomp = copy.deepcopy(res_rec.score_decomposition)

    validate_and_adapt_match_result_to_gap_set(res_rec, run_rec)

    assert res_rec.score_decomposition == orig_decomp


# 11. Custom-only job requirements remain supported (category == OTHER)
def test_custom_only_job_requirements_supported():
    custom_contrib = [
        {
            "requirement_id": str(uuid.uuid4()),
            "display_order": 0,
            "category": RequirementCategory.OTHER.value,
            "necessity": RequirementNecessity.MUST_HAVE.value,
            "priority": RequirementPriority.HIGH.value,
            "match_state": MatchResultState.MATCHED.value,
            "match_value": 1.0,
            "evidence_references": ["portfolio.pdf#1"],
            "expected_value": "Custom portfolio submission",
            "observed_value": "Verified portfolio present",
        }
    ]
    run_rec, res_rec = _build_test_records(contributions=custom_contrib)
    gap_set = validate_and_adapt_match_result_to_gap_set(res_rec, run_rec)

    assert gap_set.total_requirements == 1
    assert gap_set.gaps[0].category == RequirementCategory.OTHER
    assert gap_set.gaps[0].gap_state == RequirementGapState.COVERED


# 12. NOT_APPLICABLE remains present for audit
def test_not_applicable_remains_present_for_audit():
    na_contrib = [
        {
            "requirement_id": str(uuid.uuid4()),
            "display_order": 0,
            "category": RequirementCategory.EDUCATION.value,
            "necessity": RequirementNecessity.PREFERRED.value,
            "priority": RequirementPriority.LOW.value,
            "match_state": MatchResultState.NOT_APPLICABLE.value,
            "match_value": 0.0,
            "evidence_references": [],
        }
    ]
    run_rec, res_rec = _build_test_records(contributions=na_contrib)
    gap_set = validate_and_adapt_match_result_to_gap_set(res_rec, run_rec)

    assert gap_set.total_requirements == 1
    assert gap_set.gaps[0].gap_state == RequirementGapState.NOT_APPLICABLE
    assert gap_set.gaps[0].source_match_value == 0.0
