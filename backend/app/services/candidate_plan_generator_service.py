import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import UserRecord, UserRole
from app.models.interview import (
    CandidateInterviewPlanRecord,
    InterviewQuestionRecord,
    JobInterviewTemplateRecord,
    SupplementalQuestionProvenanceRecord,
)
from app.schemas.interview_api import (
    BlueprintValidationReportResponse,
)
from app.schemas.interview_snapshot import (
    DETERMINISTIC_BUILDER_VERSION,
    SCORING_SEPARATION_POLICY,
    CandidatePlanInputSnapshot,
    CandidateRequirementResultSnapshotItem,
    JobRequirementSnapshotItem,
)
from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    InterviewLayer,
    PlanLifecycle,
    QuestionType,
    ValidationState,
)
from app.schemas.requirement_match import MatchResultState
from app.schemas.safety_policy import (
    SAFETY_POLICY_VERSION,
    check_prohibited_topics,
)
from app.services.interview_catalogue import (
    SUPPLEMENTAL_QUESTION_CATALOGUE_VERSION,
    SupplementalQuestionTemplateEntry,
    get_catalogue_supplemental_template,
)
from app.services.interview_snapshot_service import (
    InterviewCandidateNotFoundError,
    InterviewJobNotReadyError,
    InterviewJobRevisionStaleError,
    InterviewMatchResultMismatchError,
    InterviewProfileVersionMismatchError,
    InterviewRequisitionNotFoundError,
    InterviewSnapshotError,
    InterviewSourceRelationshipInvalidError,
    InterviewUnauthorizedError,
    load_authoritative_candidate_plan_snapshot,
)

logger = logging.getLogger("ai_hiring.services.candidate_plan_generator")


class TargetSelectionResult(FrozenSchema):
    """Result of deterministic selection for a candidate clarification target."""
    requirement_id: UUID
    requirement_label: str
    match_state: MatchResultState
    match_value: float
    allowlisted_evidence_ids: List[str]
    selection_reason: str
    priority_rank: int


class CandidateSupplementalQuestion(FrozenSchema):
    """Immutable model representing a generated deterministic supplemental question."""
    question_id: UUID
    layer: InterviewLayer = InterviewLayer.SUPPLEMENTAL_EVIDENCE
    question_type: QuestionType = QuestionType.EVIDENCE_CLARIFICATION
    authoring_origin: AuthoringOrigin = AuthoringOrigin.DETERMINISTIC
    display_order: int = Field(ge=0)
    requirement_references: List[UUID] = Field(min_length=1)
    deterministic_template_id: str
    deterministic_template_version: str
    question_text: str = Field(min_length=10, max_length=2000)
    question_intent: str = Field(min_length=5, max_length=1000)
    estimated_time_minutes: int = Field(default=4, ge=1, le=15)
    included_in_common_score: bool = False
    validation_state: ValidationState = ValidationState.PASSED
    clarification_purpose: str = Field(min_length=5, max_length=1000)
    facts_to_confirm: List[str] = Field(default_factory=list)
    prohibited_inferences: List[str] = Field(default_factory=list)
    source_requirement_id: UUID
    source_match_result_id: UUID
    source_match_state: str
    allowlisted_evidence_ids: List[str] = Field(default_factory=list)


class CandidatePlanCoverageSummary(FrozenSchema):
    """Summary of candidate supplemental targets and coverage."""
    selected_target_count: int
    target_selection_reasons: Dict[str, str]
    match_states_clarified: List[str]
    total_supplemental_duration_minutes: int


class GeneratedDeterministicCandidatePlan(FrozenSchema):
    """Container for the complete generated candidate supplemental interview plan."""
    plan_id: UUID
    template_id: UUID
    template_version: int
    template_content_hash: str
    job_id: UUID
    job_revision: int
    candidate_id: UUID
    profile_id: UUID
    profile_version: int
    match_result_id: UUID
    match_result_fingerprint: str
    plan_version: int = 1
    lifecycle_status: PlanLifecycle = PlanLifecycle.DRAFT
    scoring_separation_policy: str = SCORING_SEPARATION_POLICY
    input_snapshot_hash: str
    content_hash: str
    common_core_questions_count: int
    common_core_duration_minutes: int
    supplemental_questions: List[CandidateSupplementalQuestion]
    coverage_summary: CandidatePlanCoverageSummary
    validation_report: BlueprintValidationReportResponse
    supplemental_catalogue_version: str = SUPPLEMENTAL_QUESTION_CATALOGUE_VERSION
    policy_version: str = SAFETY_POLICY_VERSION


# =========================================================================
# Step 3: Target Selection Logic
# =========================================================================

MATCH_STATE_PRIORITY_ORDER = {
    MatchResultState.CONFLICTING_EVIDENCE: 1,
    MatchResultState.INSUFFICIENT_EVIDENCE: 2,
    MatchResultState.NOT_EVIDENCED: 3,
    MatchResultState.PARTIALLY_MATCHED: 4,
    MatchResultState.MATCHED: 5,
}

NECESSITY_ORDER = {
    "MUST_HAVE": 0,
    "NICE_TO_HAVE": 1,
    "PREFERRED": 1,
    "OPTIONAL": 2,
}

PRIORITY_ORDER = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
}


def select_deterministic_candidate_targets(
    snapshot: CandidatePlanInputSnapshot,
    max_targets: int = 4,
) -> List[TargetSelectionResult]:
    """Selects up to `max_targets` (0-4) candidate clarification targets deterministically.

    Order of priority:
    1. CONFLICTING_EVIDENCE
    2. INSUFFICIENT_EVIDENCE
    3. NOT_EVIDENCED
    4. PARTIALLY_MATCHED
    5. MATCHED

    Within the same match state:
    - Must-have requirements before preferred
    - Priority descending (CRITICAL > HIGH > MEDIUM > LOW)
    - Custom weight descending
    - Requirement label alphabetical
    - Stable requirement ID
    """
    req_map: Dict[UUID, JobRequirementSnapshotItem] = {
        r.requirement_id: r for r in snapshot.job_snapshot.approved_requirements
    }

    eligible_results: List[Tuple[int, int, int, float, str, str, CandidateRequirementResultSnapshotItem, JobRequirementSnapshotItem]] = []

    for rr in snapshot.requirement_results:
        # Exclude NOT_APPLICABLE
        if rr.match_state == MatchResultState.NOT_APPLICABLE:
            continue

        state_prio = MATCH_STATE_PRIORITY_ORDER.get(rr.match_state, 99)
        req_item = req_map.get(rr.requirement_id)
        if not req_item:
            continue

        nec_val = req_item.necessity.value if hasattr(req_item.necessity, "value") else str(req_item.necessity)
        nec_prio = NECESSITY_ORDER.get(nec_val, 1)

        prio_val = req_item.priority.value if hasattr(req_item.priority, "value") else str(req_item.priority)
        prio_rank = PRIORITY_ORDER.get(prio_val, 2)

        weight = float(req_item.weight) if req_item.weight is not None else 0.0
        neg_weight = -weight

        label_key = req_item.label.lower().strip()
        req_id_key = str(req_item.requirement_id)

        eligible_results.append((
            state_prio,
            nec_prio,
            prio_rank,
            neg_weight,
            label_key,
            req_id_key,
            rr,
            req_item,
        ))

    # Sort deterministically
    eligible_results.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4], item[5]))

    # Truncate to max_targets (0 to 4)
    selected_tuples = eligible_results[:max_targets]

    targets: List[TargetSelectionResult] = []
    for rank_idx, (st_prio, nec_prio, pr_rank, neg_wt, lbl, r_id_k, rr, req_item) in enumerate(selected_tuples, start=1):
        nec_str = "must-have" if nec_prio == 0 else "preferred"
        state_str = rr.match_state.value if hasattr(rr.match_state, "value") else str(rr.match_state)
        reason = f"Selected #{rank_idx} to clarify {state_str} on {nec_str} requirement '{req_item.label}'."
        targets.append(
            TargetSelectionResult(
                requirement_id=rr.requirement_id,
                requirement_label=req_item.label,
                match_state=rr.match_state,
                match_value=float(rr.match_value),
                allowlisted_evidence_ids=list(rr.allowlisted_evidence_ids),
                selection_reason=reason,
                priority_rank=rank_idx,
            )
        )

    return targets


# =========================================================================
# Step 5: Question Generation
# =========================================================================

def generate_candidate_supplemental_questions(
    snapshot: CandidatePlanInputSnapshot,
    targets: List[TargetSelectionResult],
) -> List[CandidateSupplementalQuestion]:
    """Generates deterministic, non-scored evidence-clarification questions for the selected targets."""
    questions: List[CandidateSupplementalQuestion] = []

    for idx, target in enumerate(targets, start=1):
        template = get_catalogue_supplemental_template(target.match_state)
        if not template:
            # If no catalogue template exists for this state, do not invent arbitrary questions
            continue

        rendered_text = template.text_template.replace("{label}", target.requirement_label.strip())

        # Compute stable deterministic UUID
        hash_seed = f"cand_supp:{snapshot.candidate_id}:{snapshot.match_result_id}:{target.requirement_id}:{template.template_id}"
        q_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, hash_seed)

        questions.append(
            CandidateSupplementalQuestion(
                question_id=q_uuid,
                layer=InterviewLayer.SUPPLEMENTAL_EVIDENCE,
                question_type=QuestionType.EVIDENCE_CLARIFICATION,
                authoring_origin=AuthoringOrigin.DETERMINISTIC,
                display_order=idx,
                requirement_references=[target.requirement_id],
                deterministic_template_id=template.template_id,
                deterministic_template_version=template.version,
                question_text=rendered_text,
                question_intent=template.question_intent,
                estimated_time_minutes=template.estimated_duration_minutes,
                included_in_common_score=False,
                validation_state=ValidationState.PASSED,
                clarification_purpose=template.clarification_purpose,
                facts_to_confirm=list(template.facts_to_confirm),
                prohibited_inferences=list(template.prohibited_inferences),
                source_requirement_id=target.requirement_id,
                source_match_result_id=snapshot.match_result_id,
                source_match_state=target.match_state.value if hasattr(target.match_state, "value") else str(target.match_state),
                allowlisted_evidence_ids=list(target.allowlisted_evidence_ids),
            )
        )

    return questions


# =========================================================================
# Step 6: Policy Validation
# =========================================================================

class CandidatePlanPolicyValidator:
    """Validates candidate supplemental interview plans against neutrality, scoring separation, and provenance invariants."""

    @classmethod
    def validate(
        cls,
        snapshot: CandidatePlanInputSnapshot,
        template_record: JobInterviewTemplateRecord,
        questions: List[CandidateSupplementalQuestion],
        targets: List[TargetSelectionResult],
    ) -> BlueprintValidationReportResponse:
        blocking_errors: List[str] = []
        warnings: List[str] = []
        rules_checked = 0

        # Rule 1: Question count limits (0 to 4)
        rules_checked += 1
        if len(questions) > 4:
            blocking_errors.append(f"Supplemental question count {len(questions)} exceeds maximum limit of 4.")

        # Rule 2: Scoring separation invariant (ALL must be excluded from common score)
        rules_checked += 1
        for q in questions:
            if q.included_in_common_score:
                blocking_errors.append(f"Supplemental question '{q.question_id}' MUST NOT be included in common score.")
            if q.layer != InterviewLayer.SUPPLEMENTAL_EVIDENCE:
                blocking_errors.append(f"Supplemental question '{q.question_id}' has invalid layer '{q.layer}'; expected SUPPLEMENTAL_EVIDENCE.")
            if q.question_type != QuestionType.EVIDENCE_CLARIFICATION:
                blocking_errors.append(f"Supplemental question '{q.question_id}' has invalid question_type '{q.question_type}'; expected EVIDENCE_CLARIFICATION.")
            if q.authoring_origin != AuthoringOrigin.DETERMINISTIC:
                blocking_errors.append(f"Supplemental question '{q.question_id}' has invalid authoring_origin '{q.authoring_origin}'; expected DETERMINISTIC.")

        # Rule 3: Exact template and snapshot revision binding
        rules_checked += 1
        if template_record.revision != snapshot.job_snapshot.revision:
            blocking_errors.append(
                f"Template job revision {template_record.revision} does not match candidate snapshot job revision {snapshot.job_snapshot.revision}."
            )
        if template_record.organization_id != snapshot.job_snapshot.organization_id:
            blocking_errors.append("Template organization does not match candidate snapshot organization.")

        # Rule 4: Profile and Match Result binding
        rules_checked += 1
        if snapshot.profile_version != template_record.revision and snapshot.profile_version < 1:
            blocking_errors.append("Invalid profile version in candidate snapshot.")

        # Rule 5: NOT_APPLICABLE must not produce questions
        rules_checked += 1
        for q in questions:
            if q.source_match_state == "NOT_APPLICABLE":
                blocking_errors.append("NOT_APPLICABLE match state cannot produce supplemental clarification questions.")

        # Rule 6: Duplicate target check
        rules_checked += 1
        req_ids_used = [q.source_requirement_id for q in questions]
        if len(req_ids_used) != len(set(req_ids_used)):
            blocking_errors.append("Duplicate source requirement targeted in supplemental questions.")

        # Rule 7: Authoritative requirement reference check
        rules_checked += 1
        approved_req_ids = {r.requirement_id for r in snapshot.job_snapshot.approved_requirements}
        for q in questions:
            if q.source_requirement_id not in approved_req_ids:
                blocking_errors.append(f"Source requirement '{q.source_requirement_id}' not found in approved requirements snapshot.")

        # Rule 8: Safety & Neutrality topic check (zero prohibited topics or accusatory language)
        rules_checked += 1
        for q in questions:
            for text_val in [q.question_text, q.question_intent, q.clarification_purpose] + q.facts_to_confirm + q.prohibited_inferences:
                violation = check_prohibited_topics(text_val)
                if violation:
                    blocking_errors.append(f"Prohibited topic '{violation[0]}' (matched '{violation[1]}') in supplemental question '{q.question_id}'.")

        # Warnings: zero supplemental questions is a normal valid state
        if not questions:
            warnings.append("No additional evidence-clarification questions were required for this plan.")

        return BlueprintValidationReportResponse(
            is_valid=len(blocking_errors) == 0,
            blocking_errors=blocking_errors,
            warnings=warnings,
            checked_rules_count=rules_checked,
        )


# =========================================================================
# Step 7: Canonicalization and Content Hashing
# =========================================================================

def canonicalize_candidate_interview_plan(
    plan: GeneratedDeterministicCandidatePlan,
) -> Dict[str, Any]:
    """Produces a deterministic canonical dictionary representation of a candidate interview plan."""
    questions_normalized = []
    for q in sorted(plan.supplemental_questions, key=lambda x: (x.display_order, str(x.question_id))):
        questions_normalized.append({
            "allowlisted_evidence_ids": sorted(q.allowlisted_evidence_ids),
            "authoring_origin": q.authoring_origin.value if hasattr(q.authoring_origin, "value") else str(q.authoring_origin),
            "clarification_purpose": q.clarification_purpose.strip(),
            "deterministic_template_id": q.deterministic_template_id,
            "deterministic_template_version": q.deterministic_template_version,
            "display_order": q.display_order,
            "estimated_time_minutes": q.estimated_time_minutes,
            "facts_to_confirm": sorted(q.facts_to_confirm),
            "included_in_common_score": q.included_in_common_score,
            "layer": q.layer.value if hasattr(q.layer, "value") else str(q.layer),
            "prohibited_inferences": sorted(q.prohibited_inferences),
            "question_id": str(q.question_id),
            "question_intent": q.question_intent.strip(),
            "question_text": q.question_text.strip(),
            "question_type": q.question_type.value if hasattr(q.question_type, "value") else str(q.question_type),
            "requirement_references": sorted([str(r) for r in q.requirement_references]),
            "source_match_result_id": str(q.source_match_result_id),
            "source_match_state": q.source_match_state,
            "source_requirement_id": str(q.source_requirement_id),
            "validation_state": q.validation_state.value if hasattr(q.validation_state, "value") else str(q.validation_state),
        })

    return {
        "candidate_id": str(plan.candidate_id),
        "common_core_duration_minutes": plan.common_core_duration_minutes,
        "common_core_questions_count": plan.common_core_questions_count,
        "coverage_summary": {
            "match_states_clarified": sorted(plan.coverage_summary.match_states_clarified),
            "selected_target_count": plan.coverage_summary.selected_target_count,
            "target_selection_reasons": {k: v for k, v in sorted(plan.coverage_summary.target_selection_reasons.items())},
            "total_supplemental_duration_minutes": plan.coverage_summary.total_supplemental_duration_minutes,
        },
        "input_snapshot_hash": plan.input_snapshot_hash,
        "job_id": str(plan.job_id),
        "job_revision": plan.job_revision,
        "lifecycle_status": plan.lifecycle_status.value if hasattr(plan.lifecycle_status, "value") else str(plan.lifecycle_status),
        "match_result_fingerprint": plan.match_result_fingerprint,
        "match_result_id": str(plan.match_result_id),
        "plan_version": plan.plan_version,
        "policy_version": plan.policy_version,
        "profile_id": str(plan.profile_id),
        "profile_version": plan.profile_version,
        "scoring_separation_policy": plan.scoring_separation_policy,
        "supplemental_catalogue_version": plan.supplemental_catalogue_version,
        "supplemental_questions": questions_normalized,
        "template_content_hash": plan.template_content_hash,
        "template_id": str(plan.template_id),
        "template_version": plan.template_version,
    }


def compute_candidate_interview_plan_content_hash(
    plan: GeneratedDeterministicCandidatePlan,
) -> str:
    """Computes deterministic SHA-256 hash of a candidate interview plan."""
    canonical_dict = canonicalize_candidate_interview_plan(plan)
    serialized = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().lower()


# =========================================================================
# High-Level Services: Preview & Draft Creation
# =========================================================================

def preview_deterministic_candidate_interview_plan(
    actor: UserRecord,
    template_id: UUID,
    candidate_id: UUID,
    expected_revision: int,
    profile_version: int,
    match_result_id: UUID,
    db: Session,
    organization_id: Optional[UUID] = None,
) -> GeneratedDeterministicCandidatePlan:
    """Ephemeral server-side preview of candidate supplemental question plan without persistence."""
    # 1. Load JobInterviewTemplateRecord
    stmt_tpl = (
        select(JobInterviewTemplateRecord)
        .options(selectinload(JobInterviewTemplateRecord.questions))
        .where(JobInterviewTemplateRecord.template_id == template_id)
    )
    template_record = db.execute(stmt_tpl).scalar_one_or_none()
    if not template_record:
        raise InterviewRequisitionNotFoundError(f"Job interview template '{template_id}' not found.")

    # 2. Load authoritative candidate snapshot
    snapshot, input_snapshot_hash = load_authoritative_candidate_plan_snapshot(
        actor=actor,
        job_id=template_record.job_id,
        expected_revision=expected_revision,
        candidate_id=candidate_id,
        profile_version=profile_version,
        match_result_id=match_result_id,
        db=db,
        organization_id=organization_id,
    )

    # 3. Select targets (0 to 4)
    targets = select_deterministic_candidate_targets(snapshot, max_targets=4)

    # 4. Generate supplemental questions
    supp_questions = generate_candidate_supplemental_questions(snapshot, targets)

    # 5. Validate neutrality and scoring separation
    validation_report = CandidatePlanPolicyValidator.validate(
        snapshot=snapshot,
        template_record=template_record,
        questions=supp_questions,
        targets=targets,
    )

    # Common core metrics
    common_questions = [q for q in template_record.questions if q.layer == InterviewLayer.COMMON_CORE]
    common_duration = sum(q.estimated_time_minutes for q in common_questions)
    supp_duration = sum(q.estimated_time_minutes for q in supp_questions)
    match_states_clarified = list({t.match_state.value if hasattr(t.match_state, "value") else str(t.match_state) for t in targets})

    coverage_summary = CandidatePlanCoverageSummary(
        selected_target_count=len(targets),
        target_selection_reasons={str(t.requirement_id): t.selection_reason for t in targets},
        match_states_clarified=sorted(match_states_clarified),
        total_supplemental_duration_minutes=supp_duration,
    )

    plan = GeneratedDeterministicCandidatePlan(
        plan_id=uuid.uuid4(),
        template_id=template_record.template_id,
        template_version=template_record.template_version,
        template_content_hash=template_record.content_hash or "",
        job_id=template_record.job_id,
        job_revision=template_record.revision,
        candidate_id=snapshot.candidate_id,
        profile_id=snapshot.profile_id,
        profile_version=snapshot.profile_version,
        match_result_id=snapshot.match_result_id,
        match_result_fingerprint=snapshot.match_result_fingerprint,
        plan_version=1,
        lifecycle_status=PlanLifecycle.DRAFT,
        scoring_separation_policy=SCORING_SEPARATION_POLICY,
        input_snapshot_hash=input_snapshot_hash,
        content_hash="",
        common_core_questions_count=len(common_questions),
        common_core_duration_minutes=common_duration,
        supplemental_questions=supp_questions,
        coverage_summary=coverage_summary,
        validation_report=validation_report,
        supplemental_catalogue_version=SUPPLEMENTAL_QUESTION_CATALOGUE_VERSION,
        policy_version=SAFETY_POLICY_VERSION,
    )

    # Compute content hash
    content_hash = compute_candidate_interview_plan_content_hash(plan)

    return plan.model_copy(update={"content_hash": content_hash})


def create_deterministic_candidate_interview_plan_draft(
    actor: UserRecord,
    template_id: UUID,
    candidate_id: UUID,
    expected_revision: int,
    profile_version: int,
    match_result_id: UUID,
    db: Session,
    organization_id: Optional[UUID] = None,
) -> Tuple[CandidateInterviewPlanRecord, GeneratedDeterministicCandidatePlan]:
    """Authoritatively regenerates and persists a DRAFT CandidateInterviewPlanRecord with exact provenance binding."""
    # 1. Preview plan logic
    plan = preview_deterministic_candidate_interview_plan(
        actor=actor,
        template_id=template_id,
        candidate_id=candidate_id,
        expected_revision=expected_revision,
        profile_version=profile_version,
        match_result_id=match_result_id,
        db=db,
        organization_id=organization_id,
    )

    # 2. Check for existing candidate plan draft (idempotency)
    stmt_existing = (
        select(CandidateInterviewPlanRecord)
        .options(
            selectinload(CandidateInterviewPlanRecord.provenances).selectinload(SupplementalQuestionProvenanceRecord.question),
        )
        .where(
            CandidateInterviewPlanRecord.candidate_id == candidate_id,
            CandidateInterviewPlanRecord.template_id == template_id,
            CandidateInterviewPlanRecord.plan_version == 1,
        )
    )
    existing_plan = db.execute(stmt_existing).scalar_one_or_none()
    if existing_plan is not None:
        if existing_plan.input_snapshot_hash == plan.input_snapshot_hash:
            logger.info("Returning existing candidate interview plan draft '%s' (idempotent match).", existing_plan.plan_id)
            return existing_plan, plan

    # 3. Create persistent CandidateInterviewPlanRecord
    effective_org_id = organization_id or getattr(actor, "org_id", getattr(actor, "organization_id", None)) or actor.user_id

    # Determine max display order on template_id to prevent collision with common questions or other plans
    stmt_max_order = select(func.coalesce(func.max(InterviewQuestionRecord.display_order), 0)).where(
        InterviewQuestionRecord.template_id == template_id
    )
    current_max_order = db.execute(stmt_max_order).scalar_one()

    plan_record = CandidateInterviewPlanRecord(
        plan_id=plan.plan_id,
        organization_id=effective_org_id,
        template_id=template_id,
        template_version=plan.template_version,
        candidate_id=candidate_id,
        profile_id=plan.profile_id,
        profile_version=plan.profile_version,
        match_result_id=match_result_id,
        match_result_fingerprint=plan.match_result_fingerprint,
        plan_version=1,
        lifecycle_status=PlanLifecycle.DRAFT,
        guardrail_policy_version=SAFETY_POLICY_VERSION,
        scoring_separation_policy=SCORING_SEPARATION_POLICY,
        input_snapshot_hash=plan.input_snapshot_hash,
        content_hash=plan.content_hash,
        created_by_user_id=actor.user_id,
        record_version=1,
    )
    db.add(plan_record)
    db.flush()

    # 4. Create supplemental InterviewQuestionRecord and SupplementalQuestionProvenanceRecord
    for idx, sq in enumerate(plan.supplemental_questions, start=1):
        q_order = current_max_order + idx
        q_record = InterviewQuestionRecord(
            question_id=sq.question_id,
            template_id=template_id,
            question_version=1,
            layer=sq.layer,
            question_type=sq.question_type,
            authoring_origin=sq.authoring_origin,
            display_order=q_order,
            competency_id=None,
            requirement_ids=[str(r) for r in sq.requirement_references],
            deterministic_template_id=sq.deterministic_template_id,
            deterministic_template_version=sq.deterministic_template_version,
            question_text=sq.question_text,
            question_intent=sq.question_intent,
            estimated_time_minutes=sq.estimated_time_minutes,
            included_in_common_score=False,
            validation_state=sq.validation_state,
            permitted_probes=[],
            safe_metadata={
                "clarification_purpose": sq.clarification_purpose,
                "facts_to_confirm": sq.facts_to_confirm,
                "prohibited_inferences": sq.prohibited_inferences,
            },
        )
        db.add(q_record)
        db.flush()

        prov_record = SupplementalQuestionProvenanceRecord(
            provenance_id=uuid.uuid4(),
            question_id=q_record.question_id,
            plan_id=plan_record.plan_id,
            source_requirement_id=sq.source_requirement_id,
            source_match_result_id=sq.source_match_result_id,
            source_match_state=sq.source_match_state,
            allowlisted_evidence_ids=sq.allowlisted_evidence_ids,
            excluded_from_common_score=True,
        )
        db.add(prov_record)

    db.commit()
    db.refresh(plan_record)

    return plan_record, plan
