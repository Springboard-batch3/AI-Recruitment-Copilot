import hashlib
import json
import logging
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from app.models.job import RequirementCategory, RequirementNecessity, RequirementPriority
from app.models.matching import MatchResultRecord, MatchRunRecord
from app.schemas.explanation import (
    ControlledStateLanguage,
    DISCLAIMER_TEXT,
    EXPLANATION_TEMPLATE_VERSION,
    CalculationSummary,
    ComponentCalculationItem,
    MatchExplanationResponse,
    MustHaveWarningItem,
    PartialMatchItem,
    StrengthItem,
    UncertaintyItem,
    compute_explanation_checksum,
    verify_no_prohibited_decision_language,
)
from app.schemas.matching import verify_no_prohibited_keys
from app.schemas.requirement_match import MatchResultState
from app.schemas.scoring import ScoringCalculationMethod

logger = logging.getLogger("ai_hiring.services.explanation")


class ExplanationFaithfulnessError(Exception):
    """Raised when an explanation deviates from the immutable stored match decomposition."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def generate_match_explanation(
    result_record: MatchResultRecord,
    run_record: MatchRunRecord,
    template_version: str = EXPLANATION_TEMPLATE_VERSION,
) -> MatchExplanationResponse:
    """Generates a deterministic, evidence-addressable match explanation exclusively
    from the immutable stored result_record and score_decomposition.

    Never recalculates against latest candidate or job inputs.
    Never modifies stored scores, warnings, ranks, or creates decisions.
    """
    decomp = result_record.score_decomposition or {}

    # 1. Extract requirements contributions from stored decomposition
    req_contributions: List[Dict[str, Any]] = decomp.get("requirement_contributions", [])
    component_scores_raw: Dict[str, Any] = decomp.get("component_scores", {})
    confidence_diagnostics_raw: Dict[str, Any] = decomp.get("confidence_diagnostics", {})
    must_have_warnings_raw: List[Dict[str, Any]] = decomp.get("must_have_warnings", [])
    general_warnings_raw: List[Dict[str, Any]] = decomp.get("general_warnings", [])

    matched_items: List[Dict[str, Any]] = []
    partial_items: List[Dict[str, Any]] = []
    not_evidenced_items: List[Dict[str, Any]] = []
    insufficient_items: List[Dict[str, Any]] = []
    conflicting_items: List[Dict[str, Any]] = []
    not_applicable_items: List[Dict[str, Any]] = []

    all_evidence_refs: Set[str] = set()

    for item in req_contributions:
        m_state = str(item.get("match_state", ""))
        refs = item.get("evidence_references", [])
        for r in refs:
            all_evidence_refs.add(r)

        if m_state == MatchResultState.MATCHED.value:
            matched_items.append(item)
        elif m_state == MatchResultState.PARTIALLY_MATCHED.value:
            partial_items.append(item)
        elif m_state == MatchResultState.NOT_EVIDENCED.value:
            not_evidenced_items.append(item)
        elif m_state == MatchResultState.INSUFFICIENT_EVIDENCE.value:
            insufficient_items.append(item)
        elif m_state == MatchResultState.CONFLICTING_EVIDENCE.value:
            conflicting_items.append(item)
        elif m_state == MatchResultState.NOT_APPLICABLE.value:
            not_applicable_items.append(item)

    # 2. Build Strengths: Credited MATCHED requirements with evidence references
    # Deterministic ordering: 1) weighted_contribution desc, 2) display_order asc, 3) requirement_id asc
    sorted_matched = sorted(
        matched_items,
        key=lambda x: (
            -float(x.get("weighted_contribution", 0.0)),
            int(x.get("display_order", 0)),
            str(x.get("requirement_id", "")),
        ),
    )

    strengths: List[StrengthItem] = []
    for m in sorted_matched:
        refs = sorted(list(m.get("evidence_references", [])))
        if refs:  # Strengths must have qualifying evidence
            strengths.append(
                StrengthItem(
                    requirement_id=uuid.UUID(str(m["requirement_id"])),
                    category=RequirementCategory(m["category"]),
                    display_order=int(m.get("display_order", 0)),
                    necessity=RequirementNecessity(m["necessity"]),
                    priority=RequirementPriority(m["priority"]),
                    match_state=MatchResultState.MATCHED,
                    weighted_contribution=float(m.get("weighted_contribution", 0.0)),
                    statement=ControlledStateLanguage.MATCHED.value,
                    evidence_references=refs,
                    reason_codes=list(m.get("reason_codes", [])),
                )
            )

    # 3. Build Partial Matches
    sorted_partials = sorted(
        partial_items,
        key=lambda x: (
            -float(x.get("weighted_contribution", 0.0)),
            int(x.get("display_order", 0)),
            str(x.get("requirement_id", "")),
        ),
    )
    partial_matches: List[PartialMatchItem] = []
    for p in sorted_partials:
        refs = sorted(list(p.get("evidence_references", [])))
        reasons = list(p.get("reason_codes", []))
        exact_reason = f"Documented evidence partially satisfied requirement ({', '.join(reasons)})" if reasons else "Documented evidence partially satisfied requirement."
        partial_matches.append(
            PartialMatchItem(
                requirement_id=uuid.UUID(str(p["requirement_id"])),
                category=RequirementCategory(p["category"]),
                display_order=int(p.get("display_order", 0)),
                necessity=RequirementNecessity(p["necessity"]),
                priority=RequirementPriority(p["priority"]),
                match_state=MatchResultState.PARTIALLY_MATCHED,
                match_value=float(p.get("match_value", 0.0)),
                weighted_contribution=float(p.get("weighted_contribution", 0.0)),
                statement=ControlledStateLanguage.PARTIALLY_MATCHED.value,
                exact_reason=exact_reason,
                evidence_references=refs,
                reason_codes=reasons,
            )
        )

    # 4. Build Must-Have Warnings exactly matching stored warnings
    must_have_warnings: List[MustHaveWarningItem] = []
    for w in must_have_warnings_raw:
        w_refs = sorted(list(w.get("evidence_references", [])))
        for wr in w_refs:
            all_evidence_refs.add(wr)
        req_id_val = uuid.UUID(str(w["requirement_id"])) if w.get("requirement_id") else None
        cat_val = RequirementCategory(w["category"]) if w.get("category") else None
        st_val = MatchResultState(w["result_state"]) if w.get("result_state") else None

        must_have_warnings.append(
            MustHaveWarningItem(
                code=str(w.get("code", "")),
                requirement_id=req_id_val,
                category=cat_val,
                result_state=st_val,
                message=str(w.get("message", "")),
                evidence_references=w_refs,
                review_required=bool(w.get("review_required", True)),
            )
        )

    # 5. Build Uncertainties (Insufficient, conflicting evidence, or review limitations)
    uncertainties: List[UncertaintyItem] = []
    for ins in insufficient_items:
        refs = sorted(list(ins.get("evidence_references", [])))
        uncertainties.append(
            UncertaintyItem(
                requirement_id=uuid.UUID(str(ins["requirement_id"])),
                category=RequirementCategory(ins["category"]),
                result_state=MatchResultState.INSUFFICIENT_EVIDENCE,
                uncertainty_type="INSUFFICIENT_EVIDENCE",
                statement=ControlledStateLanguage.INSUFFICIENT_EVIDENCE.value,
                evidence_references=refs,
            )
        )
    for conf in conflicting_items:
        refs = sorted(list(conf.get("evidence_references", [])))
        uncertainties.append(
            UncertaintyItem(
                requirement_id=uuid.UUID(str(conf["requirement_id"])),
                category=RequirementCategory(conf["category"]),
                result_state=MatchResultState.CONFLICTING_EVIDENCE,
                uncertainty_type="CONFLICTING_EVIDENCE",
                statement=ControlledStateLanguage.CONFLICTING_EVIDENCE.value,
                evidence_references=refs,
            )
        )

    if bool(confidence_diagnostics_raw.get("profile_review_required", False)):
        uncertainties.append(
            UncertaintyItem(
                uncertainty_type="PROFILE_REVIEW_RECOMMENDED",
                statement="Overall profile evidence contains ambiguous items warranting recruiter verification.",
                evidence_references=[],
            )
        )

    # 6. Build Deterministic Summary
    # Example: "Documented evidence fully matched 4 requirements, partially matched 2 requirements, and did not provide qualifying evidence for 1 requirement."
    non_cred_count = len(not_evidenced_items) + len(insufficient_items) + len(conflicting_items)
    summary_parts = [
        f"Documented evidence fully matched {len(matched_items)} requirement{'s' if len(matched_items) != 1 else ''}",
        f"partially matched {len(partial_items)} requirement{'s' if len(partial_items) != 1 else ''}",
    ]
    if non_cred_count > 0:
        summary_parts.append(
            f"and did not provide qualifying evidence for {non_cred_count} requirement{'s' if non_cred_count != 1 else ''} in the selected profile version."
        )
    else:
        summary_parts.append("with all applicable requirements evidenced.")
    deterministic_summary = ", ".join(summary_parts[:2]) + ", " + summary_parts[2]

    # 7. Build Calculation Summary
    component_scores_items: Dict[str, ComponentCalculationItem] = {}
    for cat_name, cs_dict in component_scores_raw.items():
        component_scores_items[cat_name] = ComponentCalculationItem(
            category=RequirementCategory(cs_dict["category"]),
            status=str(cs_dict.get("status", "")),
            requirement_count=int(cs_dict.get("requirement_count", 0)),
            applicable_count=int(cs_dict.get("applicable_count", 0)),
            total_importance=float(cs_dict.get("total_importance", 0.0)),
            earned_contribution=float(cs_dict.get("earned_contribution", 0.0)),
            raw_component_score=float(cs_dict["raw_component_score"]) if cs_dict.get("raw_component_score") is not None else None,
            display_component_score=float(cs_dict["display_component_score"]) if cs_dict.get("display_component_score") is not None else None,
        )

    calc_method = ScoringCalculationMethod(decomp.get("calculation_method", ScoringCalculationMethod.REQUIREMENT_WEIGHTED_DIRECT.value))
    calc_summary = CalculationSummary(
        calculation_method=calc_method,
        scoring_policy_version=str(decomp.get("scoring_policy_version", run_record.scoring_policy_version)),
        raw_compatibility_score=float(result_record.raw_compatibility_score),
        display_compatibility_score=float(result_record.display_compatibility_score),
        total_requirements=len(req_contributions),
        matched_count=len(matched_items),
        partially_matched_count=len(partial_items),
        not_evidenced_count=len(not_evidenced_items),
        insufficient_evidence_count=len(insufficient_items),
        conflicting_evidence_count=len(conflicting_items),
        excluded_not_applicable_count=len(not_applicable_items),
        earned_contribution_total=float(sum(float(m.get("weighted_contribution", 0.0)) for m in req_contributions)),
        possible_contribution_total=100.0,
        component_scores=component_scores_items,
    )

    all_evidence_list = sorted(list(all_evidence_refs))

    explanation_payload_dict = {
        "result_id": str(result_record.result_id),
        "run_id": str(result_record.run_id),
        "revision": run_record.revision,
        "profile_id": str(result_record.profile_id),
        "profile_version": result_record.profile_version,
        "explanation_template_version": template_version,
        "matcher_algorithm_version": run_record.matcher_algorithm_version,
        "scoring_policy_version": run_record.scoring_policy_version,
        "raw_compatibility_score": float(result_record.raw_compatibility_score),
        "display_compatibility_score": float(result_record.display_compatibility_score),
        "confidence_tier": result_record.confidence_tier,
        "confidence_diagnostics": confidence_diagnostics_raw,
        "deterministic_summary": deterministic_summary,
        "strengths": [s.model_dump(mode="json") for s in strengths],
        "partial_matches": [p.model_dump(mode="json") for p in partial_matches],
        "must_have_warnings": [w.model_dump(mode="json") for w in must_have_warnings],
        "uncertainties": [u.model_dump(mode="json") for u in uncertainties],
        "calculation_summary": calc_summary.model_dump(mode="json"),
        "all_evidence_references": all_evidence_list,
        "disclaimer": DISCLAIMER_TEXT,
    }

    # Verify no prohibited keys in payload dictionary
    verify_no_prohibited_keys(explanation_payload_dict)

    explanation_checksum = compute_explanation_checksum(explanation_payload_dict)

    explanation_response = MatchExplanationResponse(
        explanation_id=uuid.uuid4(),
        result_id=result_record.result_id,
        run_id=result_record.run_id,
        revision=run_record.revision,
        profile_id=result_record.profile_id,
        profile_version=result_record.profile_version,
        explanation_template_version=template_version,
        matcher_algorithm_version=run_record.matcher_algorithm_version,
        scoring_policy_version=run_record.scoring_policy_version,
        raw_compatibility_score=float(result_record.raw_compatibility_score),
        display_compatibility_score=float(result_record.display_compatibility_score),
        confidence_tier=result_record.confidence_tier,
        confidence_diagnostics=confidence_diagnostics_raw,
        deterministic_summary=deterministic_summary,
        strengths=strengths,
        partial_matches=partial_matches,
        must_have_warnings=must_have_warnings,
        uncertainties=uncertainties,
        calculation_summary=calc_summary,
        all_evidence_references=all_evidence_list,
        explanation_checksum=explanation_checksum,
        disclaimer=DISCLAIMER_TEXT,
    )

    # Perform rigorous faithfulness validation
    validate_explanation_faithfulness(explanation_response, result_record, run_record)

    return explanation_response


def validate_explanation_faithfulness(
    explanation: MatchExplanationResponse,
    result_record: MatchResultRecord,
    run_record: MatchRunRecord,
) -> None:
    """Validates that the explanation strictly agrees with the stored result decomposition."""
    decomp = result_record.score_decomposition or {}
    req_contributions = decomp.get("requirement_contributions", [])
    req_lookup: Dict[str, Dict[str, Any]] = {
        str(rc["requirement_id"]): rc for rc in req_contributions
    }

    # 1. Exact score faithfulness
    if abs(explanation.raw_compatibility_score - float(result_record.raw_compatibility_score)) > 1e-5:
        raise ExplanationFaithfulnessError("SCORE_MISMATCH", "Explanation raw score does not match stored result.")
    if abs(explanation.display_compatibility_score - float(result_record.display_compatibility_score)) > 1e-2:
        raise ExplanationFaithfulnessError("DISPLAY_SCORE_MISMATCH", "Explanation display score does not match stored result.")

    # 2. Exact version faithfulness
    if explanation.run_id != result_record.run_id:
        raise ExplanationFaithfulnessError("RUN_MISMATCH", "Explanation run_id does not match result.")
    if explanation.result_id != result_record.result_id:
        raise ExplanationFaithfulnessError("RESULT_MISMATCH", "Explanation result_id does not match.")
    if explanation.revision != run_record.revision:
        raise ExplanationFaithfulnessError("REVISION_MISMATCH", "Explanation revision does not match.")
    if explanation.profile_id != result_record.profile_id or explanation.profile_version != result_record.profile_version:
        raise ExplanationFaithfulnessError("PROFILE_MISMATCH", "Explanation profile ID or version does not match result.")

    # 3. Strengths faithfulness
    for s in explanation.strengths:
        req_id_str = str(s.requirement_id)
        if req_id_str not in req_lookup:
            raise ExplanationFaithfulnessError("UNKNOWN_REQUIREMENT", f"Strength references unrecorded requirement {req_id_str}")
        stored_req = req_lookup[req_id_str]
        if stored_req.get("match_state") != MatchResultState.MATCHED.value:
            raise ExplanationFaithfulnessError("INVALID_STRENGTH_STATE", f"Requirement {req_id_str} is not MATCHED in stored result.")
        stored_refs = set(stored_req.get("evidence_references", []))
        for ref in s.evidence_references:
            if ref not in stored_refs:
                raise ExplanationFaithfulnessError("UNRECORDED_EVIDENCE", f"Evidence {ref} not present in stored requirement {req_id_str}")

    # 4. Partial matches faithfulness
    for p in explanation.partial_matches:
        req_id_str = str(p.requirement_id)
        if req_id_str not in req_lookup:
            raise ExplanationFaithfulnessError("UNKNOWN_REQUIREMENT", f"Partial match references unrecorded requirement {req_id_str}")
        stored_req = req_lookup[req_id_str]
        if stored_req.get("match_state") != MatchResultState.PARTIALLY_MATCHED.value:
            raise ExplanationFaithfulnessError("INVALID_PARTIAL_STATE", f"Requirement {req_id_str} is not PARTIALLY_MATCHED in stored result.")

    # 5. Warning count faithfulness
    stored_mh_warnings = decomp.get("must_have_warnings", [])
    if len(explanation.must_have_warnings) != len(stored_mh_warnings):
        raise ExplanationFaithfulnessError("WARNING_COUNT_MISMATCH", "Must-have warning count does not match stored result.")

    # 6. Safety check: no prohibited decision language or identity keys
    verify_no_prohibited_decision_language(explanation.deterministic_summary)
    verify_no_prohibited_keys(explanation.model_dump(mode="json"))
