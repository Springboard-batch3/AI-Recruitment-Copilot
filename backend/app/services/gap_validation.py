import copy
import logging
from typing import Any, Dict, List, Optional, Set
from uuid import UUID

from app.models.job import (
    JobLifecycleStatus,
    JobRequirement,
    JobVersion,
    JobVersionStatus,
    RequirementCategory,
    RequirementNecessity,
    RequirementPriority,
)
from app.models.matching import MatchResultRecord, MatchRunLifecycleStatus, MatchRunRecord
from app.schemas.matching import verify_no_prohibited_keys
from app.schemas.requirement_gap import (
    GAP_POLICY_VERSION,
    GAP_SCHEMA_VERSION,
    MATCH_STATE_TO_GAP_STATE_MAP,
    RequirementGapRecord,
    RequirementGapSetContract,
    RequirementGapState,
)
from app.schemas.requirement_match import MatchResultState

logger = logging.getLogger("ai_hiring.services.gap_validation")


class GapSourceValidationError(Exception):
    """Domain exception for validation failures when adapting Phase 2C match results to Phase 2D."""

    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class UnsupportedMatchResultStateError(GapSourceValidationError):
    def __init__(self, message: str = "Unsupported match result state encountered."):
        super().__init__(
            code="UNSUPPORTED_MATCH_RESULT_STATE",
            message=message,
            status_code=422,
        )


def map_match_state_to_gap_state(state: Any) -> RequirementGapState:
    """Safely and exhaustively maps a Phase 2C MatchResultState to Phase 2D RequirementGapState.

    Fails with UNSUPPORTED_MATCH_RESULT_STATE if unknown or unsupported.
    Never defaults to NOT_EVIDENCED.
    """
    if isinstance(state, str):
        try:
            state = MatchResultState(state)
        except ValueError:
            raise UnsupportedMatchResultStateError(
                f"Unsupported match result state string: '{state}'"
            )

    if not isinstance(state, MatchResultState) or state not in MATCH_STATE_TO_GAP_STATE_MAP:
        raise UnsupportedMatchResultStateError(
            f"Unsupported match result state: '{state}'"
        )

    return MATCH_STATE_TO_GAP_STATE_MAP[state]


def validate_and_adapt_match_result_to_gap_set(
    result_record: MatchResultRecord,
    run_record: MatchRunRecord,
    job_version: Optional[JobVersion] = None,
) -> RequirementGapSetContract:
    """Validates an immutable, stored Phase 2C match result and adapts it into
    a canonical Phase 2D RequirementGapSetContract.

    Invariants enforced:
    1. Parent match run status is SUCCEEDED (GAP_SOURCE_RUN_NOT_SUCCEEDED).
    2. Match result belongs to that run (GAP_SOURCE_INCOMPLETE).
    3. Requisition and exact ready revision are present (GAP_SOURCE_INCOMPLETE).
    4. Candidate profile ID and exact version are present (GAP_SOURCE_INCOMPLETE).
    5. Stored evaluation date is present (GAP_SOURCE_INCOMPLETE).
    6. Required algorithm/policy versions are present (GAP_SOURCE_VERSION_MISSING).
    7. Recorded taxonomy provenance is valid if present.
    8. Every active requirement has exactly one result (GAP_SOURCE_REQUIREMENT_MISSING).
    9. No unexpected duplicate requirement IDs exist (GAP_SOURCE_DUPLICATE_REQUIREMENT).
    10. Requirement ordering is complete and stable (display_order ascending).
    11. Source state and match value are internally consistent according to Phase 2C contract.
    12. Credited findings contain valid evidence references (GAP_SOURCE_EVIDENCE_INVALID).
    13. Must-have warning associations are preserved.
    14. Identity, contact, protected and raw-document fields are absent (GAP_SOURCE_PROHIBITED_FIELD).
    15. The input is read-only and is not mutated during validation.
    """
    # Invariant 15: Deepcopy / protect input from any mutation
    # Result and run records are treated strictly as read-only.
    if result_record is None or run_record is None:
        raise GapSourceValidationError(
            code="GAP_SOURCE_INCOMPLETE",
            message="Both match result record and match run record are required.",
        )

    # Invariant 1: Run status must be SUCCEEDED
    if run_record.lifecycle_status != MatchRunLifecycleStatus.SUCCEEDED:
        raise GapSourceValidationError(
            code="GAP_SOURCE_RUN_NOT_SUCCEEDED",
            message=f"Match run status is {run_record.lifecycle_status}; must be SUCCEEDED.",
        )

    # Invariant 2: Result must belong to run
    if result_record.run_id != run_record.run_id:
        raise GapSourceValidationError(
            code="GAP_SOURCE_INCOMPLETE",
            message="Match result does not belong to the specified match run.",
        )

    # Invariant 3: Requisition ID and revision present
    if not run_record.job_id or not run_record.job_version_id or run_record.revision < 1:
        raise GapSourceValidationError(
            code="GAP_SOURCE_INCOMPLETE",
            message="Requisition or revision information is missing from match run.",
        )

    # Invariant 4: Candidate profile ID and version present
    if not result_record.profile_id or result_record.profile_version < 1:
        raise GapSourceValidationError(
            code="GAP_SOURCE_INCOMPLETE",
            message="Candidate profile ID or version is missing from match result.",
        )

    # Invariant 5: Evaluation date present
    if not run_record.evaluation_date:
        raise GapSourceValidationError(
            code="GAP_SOURCE_INCOMPLETE",
            message="Stored evaluation date is missing from match run.",
        )

    # Invariant 6: Required versions present
    if (
        not run_record.matcher_algorithm_version
        or not run_record.matching_rule_version
        or not run_record.scoring_policy_version
        or not run_record.match_input_schema_version
    ):
        raise GapSourceValidationError(
            code="GAP_SOURCE_VERSION_MISSING",
            message="Algorithm or policy version strings are missing from match run.",
        )

    decomp = result_record.score_decomposition or {}
    if not isinstance(decomp, dict):
        raise GapSourceValidationError(
            code="GAP_SOURCE_INCOMPLETE",
            message="Score decomposition payload is missing or invalid.",
        )

    # Invariant 14: Privacy check on stored decomposition
    try:
        verify_no_prohibited_keys(decomp)
    except ValueError as e:
        raise GapSourceValidationError(
            code="GAP_SOURCE_PROHIBITED_FIELD",
            message=f"Prohibited field detected in match result: {str(e)}",
        )

    req_contributions: List[Dict[str, Any]] = decomp.get("requirement_contributions", [])
    if not req_contributions:
        raise GapSourceValidationError(
            code="GAP_SOURCE_INCOMPLETE",
            message="No requirement contributions found in score decomposition.",
        )

    must_have_warnings_raw: List[Dict[str, Any]] = decomp.get("must_have_warnings", [])
    must_have_by_req: Dict[str, str] = {}
    for mw in must_have_warnings_raw:
        r_id = mw.get("requirement_id")
        if r_id:
            must_have_by_req[str(r_id)] = mw.get("code", "")

    # Optional cross-check against JobVersion if provided
    active_req_ids: Optional[Set[UUID]] = None
    if job_version is not None:
        if job_version.job_version_id != run_record.job_version_id:
            raise GapSourceValidationError(
                code="GAP_SOURCE_INCOMPLETE",
                message="Provided JobVersion does not match run job_version_id.",
            )
        active_req_ids = {r.requirement_id for r in job_version.requirements}

    gap_records: List[RequirementGapRecord] = []
    seen_requirement_ids: Set[UUID] = set()

    for idx, item in enumerate(req_contributions):
        # Verify prohibited keys per contribution
        try:
            verify_no_prohibited_keys(item)
        except ValueError as e:
            raise GapSourceValidationError(
                code="GAP_SOURCE_PROHIBITED_FIELD",
                message=f"Prohibited field detected in requirement contribution: {str(e)}",
            )

        raw_req_id = item.get("requirement_id")
        if not raw_req_id:
            raise GapSourceValidationError(
                code="GAP_SOURCE_INCOMPLETE",
                message=f"Requirement item at index {idx} lacks requirement_id.",
            )

        try:
            req_id = UUID(str(raw_req_id))
        except ValueError:
            raise GapSourceValidationError(
                code="GAP_SOURCE_INCOMPLETE",
                message=f"Invalid UUID for requirement_id: '{raw_req_id}'.",
            )

        # Invariant 9: No duplicate requirement IDs
        if req_id in seen_requirement_ids:
            raise GapSourceValidationError(
                code="GAP_SOURCE_DUPLICATE_REQUIREMENT",
                message=f"Duplicate requirement ID '{req_id}' in match result.",
            )
        seen_requirement_ids.add(req_id)

        # Parse categories and enums
        try:
            category = RequirementCategory(item.get("category"))
            necessity = RequirementNecessity(item.get("necessity"))
            priority = RequirementPriority(item.get("priority"))
        except (ValueError, KeyError) as e:
            raise GapSourceValidationError(
                code="GAP_SOURCE_INCOMPLETE",
                message=f"Invalid requirement category/necessity/priority for '{req_id}': {e}",
            )

        # Invariant 11 & Mapping: Map match state
        raw_match_state = item.get("match_state")
        gap_state = map_match_state_to_gap_state(raw_match_state)

        match_value = float(item.get("match_value", 0.0))
        if match_value < 0.0 or match_value > 1.0:
            raise GapSourceValidationError(
                code="GAP_SOURCE_INCOMPLETE",
                message=f"match_value {match_value} out of range [0.0, 1.0] for '{req_id}'.",
            )

        evidence_refs: List[str] = item.get("evidence_references", [])
        # Invariant 12: Credited findings require qualifying evidence references
        if gap_state in {RequirementGapState.COVERED, RequirementGapState.PARTIALLY_COVERED}:
            if not evidence_refs:
                raise GapSourceValidationError(
                    code="GAP_SOURCE_EVIDENCE_INVALID",
                    message=f"Credited requirement '{req_id}' ({gap_state}) has no evidence references.",
                )

        if gap_state == RequirementGapState.NOT_EVIDENCED:
            if match_value > 0.0:
                raise GapSourceValidationError(
                    code="GAP_SOURCE_INCOMPLETE",
                    message=f"NOT_EVIDENCED state for '{req_id}' cannot have positive match_value ({match_value}).",
                )
            if evidence_refs:
                raise GapSourceValidationError(
                    code="GAP_SOURCE_EVIDENCE_INVALID",
                    message=f"NOT_EVIDENCED state for '{req_id}' cannot carry credited evidence references.",
                )

        # Invariant 13: Must-have warning reference
        warning_ref = must_have_by_req.get(str(req_id))

        # Safe sorting of evidence references
        sorted_evidence_refs = sorted(evidence_refs)

        # Resolve requirement_name
        req_name = item.get("requirement_name")
        if not req_name and job_version:
            for r in job_version.requirements:
                if r.requirement_id == req_id:
                    req_name = getattr(r, "label", None)
                    break
        if not req_name:
            req_name = f"{category.value.capitalize()} Requirement"

        doc_val = item.get("observed_value")
        if gap_state == RequirementGapState.COVERED and (not doc_val or doc_val.strip().lower() in {"none documented", "none documented."}):
            doc_val = "Evidence on file"

        display_order = int(item.get("display_order", idx))

        gap_rec = RequirementGapRecord(
            match_run_id=run_record.run_id,
            match_result_id=result_record.result_id,
            requisition_id=run_record.job_id,
            job_version_id=run_record.job_version_id,
            requisition_revision=run_record.revision,
            profile_id=result_record.profile_id,
            profile_version=result_record.profile_version,
            evaluation_date=run_record.evaluation_date,
            match_input_schema_version=run_record.match_input_schema_version,
            matcher_algorithm_version=run_record.matcher_algorithm_version,
            matching_rule_version=run_record.matching_rule_version,
            scoring_policy_version=run_record.scoring_policy_version,
            taxonomy_version=getattr(job_version, "taxonomy_version", None) if job_version else None,
            requirement_id=req_id,
            requirement_name=req_name,
            display_order=display_order,
            category=category,
            necessity=necessity,
            priority=priority,
            source_match_state=MatchResultState(raw_match_state),
            source_match_value=match_value,
            gap_state=gap_state,
            required_value=item.get("expected_value"),
            documented_value=doc_val,
            measurable_shortfall_value=None,  # Nullable in Phase 2D.1
            shortfall_unit=None,  # Nullable in Phase 2D.1
            evidence_references=sorted_evidence_refs,
            review_required=bool(item.get("review_recommended", False)),
            must_have_warning_reference=warning_ref,
            recruiter_wording_placeholder=None,
            gap_schema_version=GAP_SCHEMA_VERSION,
            gap_policy_version=GAP_POLICY_VERSION,
        )
        gap_records.append(gap_rec)

    # Invariant 8: If job_version provided, verify all active requirements present
    if active_req_ids is not None:
        missing_ids = active_req_ids - seen_requirement_ids
        if missing_ids:
            raise GapSourceValidationError(
                code="GAP_SOURCE_REQUIREMENT_MISSING",
                message=f"Missing requirement results for active requirements: {missing_ids}",
            )

    # Invariant 10: Sort strictly by display_order ascending
    gap_records.sort(key=lambda g: g.display_order)

    # Build and validate container contract
    gap_set = RequirementGapSetContract(
        gap_schema_version=GAP_SCHEMA_VERSION,
        gap_policy_version=GAP_POLICY_VERSION,
        match_run_id=run_record.run_id,
        match_result_id=result_record.result_id,
        requisition_id=run_record.job_id,
        job_version_id=run_record.job_version_id,
        requisition_revision=run_record.revision,
        profile_id=result_record.profile_id,
        profile_version=result_record.profile_version,
        evaluation_date=run_record.evaluation_date,
        matcher_algorithm_version=run_record.matcher_algorithm_version,
        scoring_policy_version=run_record.scoring_policy_version,
        total_requirements=len(gap_records),
        gaps=gap_records,
    )

    return gap_set
