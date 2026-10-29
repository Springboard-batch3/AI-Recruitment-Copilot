import uuid
from decimal import Decimal
import pytest

from app.models.job import (
    RequirementCategory,
    RequirementNecessity,
    RequirementPriority,
)
from app.schemas.matching import (
    MATCH_INPUT_SCHEMA_VERSION,
    MATCHER_ALGORITHM_VERSION,
)
from app.schemas.requirement_match import (
    MatchComparisonMethod,
    MatchResultState,
)
from app.schemas.scoring import (
    AssessmentConfidenceDiagnostics,
    AssessmentConfidenceTier,
    CompatibilityScoreResult,
    ComponentScore,
    ImportanceSource,
    RequirementContribution,
    ScoringCalculationMethod,
    ScoringWarning,
    ScoringWarningCode,
)
from app.services.match_orchestration import (
    compute_deterministic_ranking,
)


def _create_mock_score_result(
    raw_score: float,
    display_score: float,
    confidence_tier: AssessmentConfidenceTier = AssessmentConfidenceTier.HIGH,
    warnings: list = None,
) -> CompatibilityScoreResult:
    warnings = warnings or []
    req_id = uuid.uuid4()
    req_contrib = RequirementContribution(
        requirement_id=req_id,
        display_order=0,
        category=RequirementCategory.SKILL,
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        importance_source=ImportanceSource.APPROVED_CUSTOM_WEIGHT,
        configured_importance=5.0,
        effective_importance=1.0,
        match_state=MatchResultState.MATCHED,
        match_value=1.0,
        weighted_contribution=raw_score,
        evidence_references=["ev-1"],
        comparison_method=MatchComparisonMethod.EXACT_NORMALIZED_LABEL,
        reason_codes=[],
    )

    component_scores = {
        RequirementCategory.SKILL: ComponentScore(
            category=RequirementCategory.SKILL,
            status="APPLICABLE",
            requirement_count=1,
            applicable_count=1,
            total_importance=5.0,
            earned_contribution=raw_score,
            raw_component_score=raw_score / 100.0,
            display_component_score=display_score,
        )
    }

    confidence_diag = AssessmentConfidenceDiagnostics(
        confidence_tier=confidence_tier,
        applicable_requirement_count=1,
        determinately_evaluated_count=1,
        requirements_with_qualifying_evidence=1,
        insufficient_evidence_count=0,
        conflicting_evidence_count=0,
        profile_review_required=False,
    )

    return CompatibilityScoreResult(
        matcher_algorithm_version=MATCHER_ALGORITHM_VERSION,
        match_input_schema_version=MATCH_INPUT_SCHEMA_VERSION,
        requisition_id=uuid.uuid4(),
        job_version_id=uuid.uuid4(),
        revision=1,
        profile_id=uuid.uuid4(),
        profile_version=1,
        calculation_method=ScoringCalculationMethod.REQUIREMENT_WEIGHTED_DIRECT,
        requirement_contributions=[req_contrib],
        component_scores=component_scores,
        raw_compatibility_score=raw_score,
        display_compatibility_score=display_score,
        confidence_diagnostics=confidence_diag,
        must_have_warnings=warnings,
        general_warnings=[],
        decision=None,
        automatic_rejection=False,
    )


def test_higher_raw_score_ranks_first():
    """Candidates with higher raw scores strictly rank ahead of lower scores."""
    p1 = uuid.uuid4()
    p2 = uuid.uuid4()
    s1 = _create_mock_score_result(raw_score=92.500000, display_score=92.50)
    s2 = _create_mock_score_result(raw_score=85.000000, display_score=85.00)

    ranked = compute_deterministic_ranking([(p2, 1, s2), (p1, 1, s1)])

    assert len(ranked) == 2
    assert ranked[0].profile_id == p1
    assert ranked[0].rank == 1
    assert ranked[1].profile_id == p2
    assert ranked[1].rank == 2


def test_rounded_display_score_does_not_determine_order():
    """Precision test: Two candidates with identical display score (84.44) but different raw scores."""
    p1 = uuid.uuid4()
    p2 = uuid.uuid4()
    # 84.444445 > 84.444440 even though both round to 84.44
    s1 = _create_mock_score_result(raw_score=84.444445, display_score=84.44)
    s2 = _create_mock_score_result(raw_score=84.444440, display_score=84.44)

    ranked = compute_deterministic_ranking([(p2, 1, s2), (p1, 1, s1)])

    assert ranked[0].profile_id == p1
    assert ranked[0].rank == 1
    assert ranked[1].profile_id == p2
    assert ranked[1].rank == 2
    assert not ranked[0].is_tied


def test_exact_ties_receive_same_dense_rank():
    """Mathematically equal raw scores receive the same dense rank, followed by rank + 1."""
    p1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
    p2 = uuid.UUID("22222222-2222-2222-2222-222222222222")
    p3 = uuid.UUID("33333333-3333-3333-3333-333333333333")

    s1 = _create_mock_score_result(raw_score=92.500000, display_score=92.50)
    s2 = _create_mock_score_result(raw_score=92.500000, display_score=92.50)
    s3 = _create_mock_score_result(raw_score=80.000000, display_score=80.00)

    ranked = compute_deterministic_ranking([(p1, 1, s1), (p2, 1, s2), (p3, 1, s3)])

    assert ranked[0].rank == 1
    assert ranked[0].is_tied is True
    assert ranked[0].tie_group_id == "tie_rank_1"

    assert ranked[1].rank == 1
    assert ranked[1].is_tied is True
    assert ranked[1].tie_group_id == "tie_rank_1"

    # Dense rank: next rank is 2 (not 3)
    assert ranked[2].rank == 2
    assert ranked[2].is_tied is False
    assert ranked[2].tie_group_id is None
    assert ranked[2].profile_id == p3


def test_confidence_and_warnings_do_not_break_ties():
    """Confidence tier and warning counts must NOT alter compatibility ties."""
    p1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
    p2 = uuid.UUID("22222222-2222-2222-2222-222222222222")

    warn = ScoringWarning(
        code=ScoringWarningCode.MUST_HAVE_PARTIALLY_MATCHED,
        message="Requirement only partially evidenced.",
    )

    # p1 has HIGH confidence and 0 warnings; p2 has LOW confidence and 1 warning
    # Both have identical raw score 90.000000
    s1 = _create_mock_score_result(raw_score=90.000000, display_score=90.00, confidence_tier=AssessmentConfidenceTier.HIGH)
    s2 = _create_mock_score_result(raw_score=90.000000, display_score=90.00, confidence_tier=AssessmentConfidenceTier.LOW, warnings=[warn])

    ranked = compute_deterministic_ranking([(p2, 1, s2), (p1, 1, s1)])

    assert ranked[0].rank == 1
    assert ranked[1].rank == 1
    assert ranked[0].is_tied is True
    assert ranked[1].is_tied is True


def test_stable_pseudonymous_key_makes_tied_responses_reproducible():
    """Tied candidates are deterministically ordered by (str(profile_id), profile_version)."""
    p_a = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    p_b = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    s_a = _create_mock_score_result(raw_score=75.000000, display_score=75.00)
    s_b = _create_mock_score_result(raw_score=75.000000, display_score=75.00)

    # Pass in b first, then a
    ranked1 = compute_deterministic_ranking([(p_b, 1, s_b), (p_a, 1, s_a)])
    # Pass in a first, then b
    ranked2 = compute_deterministic_ranking([(p_a, 1, s_a), (p_b, 1, s_b)])

    # Both must return in exact same order: 'aaaaaaaa...' before 'bbbbbbbb...'
    assert [r.profile_id for r in ranked1] == [p_a, p_b]
    assert [r.profile_id for r in ranked2] == [p_a, p_b]


def test_ranking_never_creates_automated_decision():
    """Ranking results must contain NO automated decision making, shortlist flags, or rejection."""
    p = uuid.uuid4()
    s = _create_mock_score_result(raw_score=30.000000, display_score=30.00)

    ranked = compute_deterministic_ranking([(p, 1, s)])
    res = ranked[0]
    assert res.score_result.automatic_rejection is False
    assert res.score_result.decision is None
