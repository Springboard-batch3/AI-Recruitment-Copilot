import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from app.models.job import (
    RequirementCategory,
    RequirementNecessity,
    RequirementPriority,
)
from app.schemas.matching import (
    MatchInputSnapshot,
    verify_no_prohibited_keys,
)
from app.schemas.requirement_match import (
    MatchResultState,
    RequirementMatch,
    RequirementMatchResultSet,
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
    DECIMAL_CALCULATION_PRECISION,
    DISPLAY_ROUNDING_PRECISION,
    SCORING_POLICY_VERSION,
)


class ScoringDomainError(Exception):
    """Base exception for domain scoring errors."""
    pass


class InvalidWeightError(ScoringDomainError):
    """Raised when an invalid weight (negative, NaN, infinite) is encountered."""
    pass


class MissingImportanceConfigurationError(ScoringDomainError):
    """Raised when no valid importance (custom weight or priority default) can be resolved."""
    pass


class ZeroAggregateDenominatorError(ScoringDomainError):
    """Raised when total applicable importance across all requirements is zero."""
    pass


class EmptyApplicableRequirementSetError(ScoringDomainError):
    """Raised when the requisition has no applicable requirements to score."""
    pass


class MissingRequirementResultError(ScoringDomainError):
    """Raised when a requirement in the requisition is missing from the match results."""
    pass


class DuplicateRequirementResultError(ScoringDomainError):
    """Raised when duplicate requirement results are provided."""
    pass


# Phase 2C.3 Versioned Policy: Priority to default importance mapping
# CRITICAL = 4, HIGH = 3, MEDIUM = 2, LOW = 1
PRIORITY_IMPORTANCE_MAP: Dict[RequirementPriority, Decimal] = {
    RequirementPriority.CRITICAL: Decimal("4.0"),
    RequirementPriority.HIGH: Decimal("3.0"),
    RequirementPriority.MEDIUM: Decimal("2.0"),
    RequirementPriority.LOW: Decimal("1.0"),
}


def resolve_requirement_importance(match: RequirementMatch) -> Tuple[Decimal, ImportanceSource]:
    """Resolves the raw numeric importance of a requirement.

    Resolution precedence:
    1. Approved custom weight override if present and valid (> 0.0).
    2. Versioned priority default mapping (CRITICAL=4, HIGH=3, MEDIUM=2, LOW=1).
    3. Fails if negative, NaN, infinite, or unmappable.
    """
    if match.approved_weight is not None:
        raw_val = match.approved_weight
        if math.isnan(raw_val) or math.isinf(raw_val):
            raise InvalidWeightError(f"Requirement {match.requirement_id} has NaN or infinite custom weight: {raw_val}")
        if raw_val < 0.0:
            raise InvalidWeightError(f"Requirement {match.requirement_id} has negative custom weight: {raw_val}")
        # Note: If approved_weight is 0.0, it represents intentional exclusion (0 weight)
        return Decimal(str(raw_val)), ImportanceSource.APPROVED_CUSTOM_WEIGHT

    if match.priority in PRIORITY_IMPORTANCE_MAP:
        return PRIORITY_IMPORTANCE_MAP[match.priority], ImportanceSource.PRIORITY_DEFAULT

    raise MissingImportanceConfigurationError(
        f"Requirement {match.requirement_id} has no valid approved weight or known priority mapping."
    )


def calculate_compatibility_score(
    result_set: RequirementMatchResultSet,
    snapshot: Optional[MatchInputSnapshot] = None,
) -> CompatibilityScoreResult:
    """Calculates transparent requirement contributions, component scores, overall compatibility,
    assessment-confidence diagnostics, and visible must-have warnings.

    Pure, deterministic mathematical calculation.
    """
    if not result_set.matches:
        raise EmptyApplicableRequirementSetError("Result set contains no requirement matches.")

    # 1. Integrity check: ensure unique requirement IDs
    seen_req_ids: Set[UUID] = set()
    for m in result_set.matches:
        if m.requirement_id in seen_req_ids:
            raise DuplicateRequirementResultError(f"Duplicate requirement match for ID {m.requirement_id}.")
        seen_req_ids.add(m.requirement_id)

    # 2. Separate applicable and non-applicable matches
    # Requirements with result_state == NOT_APPLICABLE do not enter score calculations
    applicable_matches = [m for m in result_set.matches if m.result_state != MatchResultState.NOT_APPLICABLE]
    if not applicable_matches:
        raise EmptyApplicableRequirementSetError("All requirements are marked NOT_APPLICABLE; cannot calculate score.")

    # 3. Resolve importance for every applicable requirement
    req_importances: Dict[UUID, Tuple[Decimal, ImportanceSource]] = {}
    total_importance = Decimal("0.0")

    for m in applicable_matches:
        imp, src = resolve_requirement_importance(m)
        req_importances[m.requirement_id] = (imp, src)
        total_importance += imp

    if total_importance <= Decimal("0.0"):
        raise ZeroAggregateDenominatorError("Total applicable requirement importance is zero.")

    # 4. Calculate requirement contributions
    contributions: List[RequirementContribution] = []
    # Accumulate component sums: category -> {"earned": Decimal, "total_imp": Decimal, "app_count": int, "total_count": int}
    all_categories = {r.category for r in result_set.matches}
    component_accumulators: Dict[RequirementCategory, Dict[str, Any]] = {
        cat: {"earned": Decimal("0.0"), "total_imp": Decimal("0.0"), "applicable_count": 0, "total_count": 0}
        for cat in RequirementCategory
    }

    # Record total counts per category
    for m in result_set.matches:
        component_accumulators[m.category]["total_count"] += 1

    total_weighted_earned = Decimal("0.0")

    # Maintain strict display order from result_set.matches
    for m in result_set.matches:
        if m.result_state == MatchResultState.NOT_APPLICABLE:
            # Add not applicable contribution with 0 weight
            contributions.append(
                RequirementContribution(
                    requirement_id=m.requirement_id,
                    requirement_name=getattr(m, "requirement_name", "Requirement"),
                    display_order=m.display_order,
                    category=m.category,
                    necessity=m.necessity,
                    priority=m.priority,
                    importance_source=ImportanceSource.PRIORITY_DEFAULT,
                    configured_importance=0.0,
                    effective_importance=0.0,
                    match_state=m.result_state,
                    match_value=0.0,
                    weighted_contribution=0.0,
                    evidence_references=m.evidence_references,
                    expected_value=m.expected_value,
                    observed_value=m.observed_value,
                    comparison_method=m.comparison_method,
                    reason_codes=m.reason_codes,
                    review_recommended=m.review_recommended,
                )
            )
            continue

        imp, src = req_importances[m.requirement_id]
        effective_importance = imp / total_importance
        m_val = Decimal(str(m.match_value))

        # Weighted contribution to overall 0-100 score: effective_importance * match_value * 100
        req_contribution = effective_importance * m_val * Decimal("100.0")
        total_weighted_earned += req_contribution

        # Accumulate category components
        component_accumulators[m.category]["earned"] += (imp * m_val)
        component_accumulators[m.category]["total_imp"] += imp
        component_accumulators[m.category]["applicable_count"] += 1

        contributions.append(
            RequirementContribution(
                requirement_id=m.requirement_id,
                requirement_name=getattr(m, "requirement_name", "Requirement"),
                display_order=m.display_order,
                category=m.category,
                necessity=m.necessity,
                priority=m.priority,
                importance_source=src,
                configured_importance=float(imp),
                effective_importance=float(effective_importance.quantize(DECIMAL_CALCULATION_PRECISION, rounding=ROUND_HALF_UP)),
                match_state=m.result_state,
                match_value=m.match_value,
                weighted_contribution=float(req_contribution.quantize(DECIMAL_CALCULATION_PRECISION, rounding=ROUND_HALF_UP)),
                evidence_references=m.evidence_references,
                expected_value=m.expected_value,
                observed_value=m.observed_value,
                comparison_method=m.comparison_method,
                reason_codes=m.reason_codes,
                review_recommended=m.review_recommended,
            )
        )

    # 5. Calculate Component Scores
    component_scores: Dict[RequirementCategory, ComponentScore] = {}
    for cat in RequirementCategory:
        acc = component_accumulators[cat]
        if acc["total_count"] == 0 or acc["applicable_count"] == 0:
            component_scores[cat] = ComponentScore(
                category=cat,
                status="NOT_APPLICABLE",
                requirement_count=acc["total_count"],
                applicable_count=acc["applicable_count"],
                total_importance=float(acc["total_imp"]),
                earned_contribution=float(acc["earned"]),
                raw_component_score=None,
                display_component_score=None,
            )
        else:
            cat_score = acc["earned"] / acc["total_imp"] if acc["total_imp"] > Decimal("0.0") else Decimal("0.0")
            cat_score_clamped = max(Decimal("0.0"), min(Decimal("1.0"), cat_score))
            raw_comp = float(cat_score_clamped.quantize(DECIMAL_CALCULATION_PRECISION, rounding=ROUND_HALF_UP))
            disp_comp = float((cat_score_clamped * Decimal("100.0")).quantize(DISPLAY_ROUNDING_PRECISION, rounding=ROUND_HALF_UP))

            component_scores[cat] = ComponentScore(
                category=cat,
                status="APPLICABLE",
                requirement_count=acc["total_count"],
                applicable_count=acc["applicable_count"],
                total_importance=float(acc["total_imp"]),
                earned_contribution=float(acc["earned"]),
                raw_component_score=raw_comp,
                display_component_score=disp_comp,
            )

    # 6. Overall Compatibility Score (PATH B: REQUIREMENT_WEIGHTED_DIRECT)
    # Clamp between 0.0 and 100.0
    overall_clamped = max(Decimal("0.0"), min(Decimal("100.0"), total_weighted_earned))
    raw_overall = float(overall_clamped.quantize(DECIMAL_CALCULATION_PRECISION, rounding=ROUND_HALF_UP))
    display_overall = float(overall_clamped.quantize(DISPLAY_ROUNDING_PRECISION, rounding=ROUND_HALF_UP))

    # 7. Generate Must-Have Warnings
    must_have_warnings: List[ScoringWarning] = []
    general_warnings: List[ScoringWarning] = []

    for m in result_set.matches:
        is_must_have = m.necessity == RequirementNecessity.MUST_HAVE
        if is_must_have:
            if m.result_state == MatchResultState.PARTIALLY_MATCHED:
                must_have_warnings.append(
                    ScoringWarning(
                        code=ScoringWarningCode.MUST_HAVE_PARTIALLY_MATCHED,
                        requirement_id=m.requirement_id,
                        category=m.category,
                        result_state=m.result_state,
                        message="Requirement is only partially evidenced in the candidate profile.",
                        evidence_references=m.evidence_references,
                        review_required=True,
                    )
                )
            elif m.result_state == MatchResultState.NOT_EVIDENCED:
                must_have_warnings.append(
                    ScoringWarning(
                        code=ScoringWarningCode.MUST_HAVE_NOT_EVIDENCED,
                        requirement_id=m.requirement_id,
                        category=m.category,
                        result_state=m.result_state,
                        message="No qualifying evidence was found in the current candidate profile.",
                        evidence_references=m.evidence_references,
                        review_required=True,
                    )
                )
            elif m.result_state == MatchResultState.INSUFFICIENT_EVIDENCE:
                must_have_warnings.append(
                    ScoringWarning(
                        code=ScoringWarningCode.MUST_HAVE_INSUFFICIENT_EVIDENCE,
                        requirement_id=m.requirement_id,
                        category=m.category,
                        result_state=m.result_state,
                        message="Evidence provided is insufficient to fully demonstrate requirement.",
                        evidence_references=m.evidence_references,
                        review_required=True,
                    )
                )
            elif m.result_state == MatchResultState.CONFLICTING_EVIDENCE:
                must_have_warnings.append(
                    ScoringWarning(
                        code=ScoringWarningCode.MUST_HAVE_CONFLICTING_EVIDENCE,
                        requirement_id=m.requirement_id,
                        category=m.category,
                        result_state=m.result_state,
                        message="Conflicting evidence was detected for this requirement in candidate profile.",
                        evidence_references=m.evidence_references,
                        review_required=True,
                    )
                )

        if m.review_recommended:
            general_warnings.append(
                ScoringWarning(
                    code=ScoringWarningCode.PROFILE_REVIEW_RECOMMENDED,
                    requirement_id=m.requirement_id,
                    category=m.category,
                    result_state=m.result_state,
                    message="Review recommended for this requirement match.",
                    evidence_references=m.evidence_references,
                    review_required=True,
                )
            )

    # 8. Calculate Assessment Confidence Diagnostics (Separately from Compatibility!)
    app_count = len(applicable_matches)
    with_evidence_count = sum(1 for m in applicable_matches if len(m.evidence_references) > 0)
    conflicting_count = sum(1 for m in applicable_matches if m.result_state == MatchResultState.CONFLICTING_EVIDENCE)
    insufficient_count = sum(1 for m in applicable_matches if m.result_state == MatchResultState.INSUFFICIENT_EVIDENCE)

    # Determinately evaluated items: items evaluated conclusively (MATCHED, PARTIALLY_MATCHED, or definitive NOT_EVIDENCED)
    determinate_count = sum(
        1 for m in applicable_matches
        if m.result_state in {MatchResultState.MATCHED, MatchResultState.PARTIALLY_MATCHED, MatchResultState.NOT_EVIDENCED}
    )

    reasons_lowering: List[str] = []
    if conflicting_count > 0:
        reasons_lowering.append(f"{conflicting_count} requirement(s) have conflicting evidence.")
    if insufficient_count > 0:
        reasons_lowering.append(f"{insufficient_count} requirement(s) have insufficient evidence.")

    profile_review_flag = False
    if snapshot and snapshot.candidate_view.review_required:
        profile_review_flag = True
        reasons_lowering.append("Candidate profile carries unresolved review warnings from assembly.")

    # Confidence tier logic:
    # HIGH: 100% determinate evaluation, no conflicting evidence, no profile review flag
    # MEDIUM: >= 75% determinate evaluation, at most 1 conflict or profile review required
    # LOW: < 75% determinate evaluation or multiple conflicts
    determinate_ratio = determinate_count / app_count if app_count > 0 else 0.0

    if determinate_ratio == 1.0 and conflicting_count == 0 and not profile_review_flag:
        tier = AssessmentConfidenceTier.HIGH
    elif determinate_ratio >= 0.75 and conflicting_count <= 1:
        tier = AssessmentConfidenceTier.MEDIUM
    else:
        tier = AssessmentConfidenceTier.LOW

    confidence_diag = AssessmentConfidenceDiagnostics(
        confidence_tier=tier,
        applicable_requirement_count=app_count,
        determinately_evaluated_count=determinate_count,
        requirements_with_qualifying_evidence=with_evidence_count,
        insufficient_evidence_count=insufficient_count,
        conflicting_evidence_count=conflicting_count,
        profile_review_required=profile_review_flag,
        reasons_lowering_confidence=reasons_lowering,
    )

    # 9. Return structured, frozen CompatibilityScoreResult
    return CompatibilityScoreResult(
        scoring_policy_version=SCORING_POLICY_VERSION,
        matcher_algorithm_version=result_set.matcher_algorithm_version,
        match_input_schema_version=result_set.match_input_schema_version,
        requisition_id=result_set.requisition_id,
        job_version_id=result_set.job_version_id,
        revision=result_set.revision,
        profile_id=result_set.profile_id,
        profile_version=result_set.profile_version,
        calculation_method=ScoringCalculationMethod.REQUIREMENT_WEIGHTED_DIRECT,
        requirement_contributions=contributions,
        component_scores=component_scores,
        raw_compatibility_score=raw_overall,
        display_compatibility_score=display_overall,
        confidence_diagnostics=confidence_diag,
        must_have_warnings=must_have_warnings,
        general_warnings=general_warnings,
        decision=None,
        automatic_rejection=False,
    )
