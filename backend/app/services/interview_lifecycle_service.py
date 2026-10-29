import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import UserRecord, UserRole
from app.models.interview import (
    CandidateInterviewPlanRecord,
    InterviewQuestionRecord,
    InterviewRubricRecord,
    JobInterviewTemplateRecord,
    SupplementalQuestionProvenanceRecord,
)
from app.models.job import JobLifecycleStatus, JobRequisition
from app.schemas.interview_api import (
    ApprovalSummaryResponse,
    CandidatePlanEditRequest,
    CandidateSupplementalQuestionEditItem,
    InterviewApprovalRequest,
    InterviewTemplateEditRequest,
    QuestionDiffItem,
    QuestionEditItem,
    SuccessorDraftCreateRequest,
    ValidationSummaryResponse,
    VersionDiffResponse,
    VersionHistoryResponse,
    VersionSummaryItem,
)
from app.schemas.interview_blueprint import BLUEPRINT_SCHEMA_VERSION, InterviewQuestion, InterviewRubric
from app.schemas.interview_snapshot import (
    DETERMINISTIC_BUILDER_VERSION,
    SCORING_SEPARATION_POLICY,
    compute_job_blueprint_snapshot_hash,
)
from app.schemas.phase3_audit import Phase3AuditEvent
from app.schemas.phase3_enums import (
    AuditRetentionClass,
    AuthoringOrigin,
    InterviewLayer,
    PlanLifecycle,
    QuestionType,
    ValidationState,
)
from app.schemas.safety_policy import (
    SAFETY_POLICY_HASH,
    SAFETY_POLICY_ID,
    SAFETY_POLICY_SCHEMA_VERSION,
    SAFETY_POLICY_VERSION,
    check_prohibited_topics,
)
from app.services.candidate_plan_generator_service import (
    CandidatePlanPolicyValidator,
    CandidateSupplementalQuestion,
    GeneratedDeterministicCandidatePlan,
    canonicalize_candidate_interview_plan,
    compute_candidate_interview_plan_content_hash,
)
from app.services.interview_catalogue import (
    QUESTION_CATALOGUE_VERSION,
    RUBRIC_CATALOGUE_VERSION,
    SUPPLEMENTAL_QUESTION_CATALOGUE_VERSION,
)
from app.services.interview_generator_service import (
    InterviewPolicyValidator,
    calculate_blueprint_coverage,
    canonicalize_deterministic_blueprint,
    compute_deterministic_blueprint_content_hash,
    evaluate_requirement_interview_suitability,
)
from app.services.interview_snapshot_service import (
    InterviewJobNotReadyError,
    InterviewJobRevisionStaleError,
    InterviewRequisitionNotFoundError,
    InterviewSnapshotError,
    InterviewUnauthorizedError,
    load_authoritative_candidate_plan_snapshot,
    load_authoritative_job_blueprint_snapshot,
)

logger = logging.getLogger("ai_hiring.services.interview_lifecycle")


class LifecycleConflictError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class LifecycleValidationError(Exception):
    def __init__(self, code: str, message: str, blocking_errors: List[str]):
        super().__init__(message)
        self.code = code
        self.message = message
        self.blocking_errors = blocking_errors
        self.status_code = 400


def _check_recruiter_authorization(actor: UserRecord, job: JobRequisition) -> None:
    """Verifies that actor has recruiter access to the job."""
    if actor.role == UserRole.ADMIN:
        return
    if actor.role != UserRole.RECRUITER:
        raise InterviewUnauthorizedError("Only recruiters and admins may manage interviews.")
    if job.created_by_user_id != actor.user_id:
        raise InterviewUnauthorizedError("Recruiter is not assigned to this job requisition.")


# =========================================================================
# JobInterviewTemplate: Safe Edit
# =========================================================================

def edit_job_interview_template_draft(
    actor: UserRecord,
    job_id: uuid.UUID,
    template_id: uuid.UUID,
    request: InterviewTemplateEditRequest,
    db: Session,
) -> JobInterviewTemplateRecord:
    """Safely updates non-structural fields of a DRAFT JobInterviewTemplate."""
    template = (
        db.query(JobInterviewTemplateRecord)
        .options(
            selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
        )
        .filter(
            JobInterviewTemplateRecord.template_id == template_id,
            JobInterviewTemplateRecord.job_id == job_id,
        )
        .first()
    )
    if not template:
        raise InterviewSnapshotError(
            code="TEMPLATE_NOT_FOUND",
            message="Job interview template not found.",
            status_code=404,
        )

    _check_recruiter_authorization(actor, template.requisition)

    # 1. State check
    if template.lifecycle_status != PlanLifecycle.DRAFT:
        raise LifecycleConflictError(
            code="IMMUTABLE_NON_DRAFT_STATE",
            message=f"Cannot edit template in {template.lifecycle_status.value} state. Approved records are immutable.",
        )

    # 2. Concurrency check
    if template.record_version != request.expected_record_version:
        raise LifecycleConflictError(
            code="STALE_RECORD_VERSION",
            message=f"Conflict: Expected record version {request.expected_record_version}, but database is at {template.record_version}.",
        )
    if template.content_hash != request.expected_content_hash:
        raise LifecycleConflictError(
            code="STALE_CONTENT_HASH",
            message="Conflict: Template content has been modified concurrently.",
        )

    # 3. Load authoritative snapshot to verify structural integrity
    snapshot, _ = load_authoritative_job_blueprint_snapshot(
        actor=actor,
        job_id=job_id,
        expected_revision=template.revision,
        db=db,
    )

    # 4. Map questions by ID
    existing_q_map = {q.question_id: q for q in template.questions}
    edit_q_ids = {item.question_id for item in request.questions}

    if set(existing_q_map.keys()) != edit_q_ids:
        raise LifecycleConflictError(
            code="STRUCTURAL_QUESTION_SET_MISMATCH",
            message="Cannot add or delete questions via safe edit. Question identifiers must match existing draft.",
        )

    # 5. Apply edits and check for safety violations
    display_orders = []
    for item in request.questions:
        q_record = existing_q_map[item.question_id]
        
        # Check text safety
        violation = check_prohibited_topics(item.question_text)
        if violation:
            raise LifecycleValidationError(
                code="PROHIBITED_TOPIC_DETECTED",
                message=f"Question text violates safety policy on prohibited topic '{violation[0]}'.",
                blocking_errors=[f"Prohibited topic '{violation[0]}' (matched '{violation[1]}') in question {item.question_id}"],
            )
        for probe in item.permitted_probes:
            violation = check_prohibited_topics(probe)
            if violation:
                raise LifecycleValidationError(
                    code="PROHIBITED_TOPIC_DETECTED",
                    message=f"Permitted probe violates safety policy on prohibited topic '{violation[0]}'.",
                    blocking_errors=[f"Prohibited topic '{violation[0]}' (matched '{violation[1]}') in probe"],
                )

        if len(item.permitted_probes) > 2:
            raise LifecycleValidationError(
                code="EXCESSIVE_PROBES",
                message="Cannot specify more than 2 permitted probes per question.",
                blocking_errors=["Maximum 2 probes permitted per question."],
            )

        # Check if text was modified
        if q_record.question_text.strip() != item.question_text.strip():
            q_record.question_text = item.question_text.strip()
            q_record.authoring_origin = AuthoringOrigin.RECRUITER_EDITED
            q_record.question_version += 1

        q_record.display_order = item.display_order
        q_record.estimated_time_minutes = item.estimated_time_minutes
        q_record.permitted_probes = [p.strip() for p in item.permitted_probes]
        q_record.validation_state = ValidationState.PENDING
        display_orders.append(item.display_order)

    # Check unique display order
    if len(display_orders) != len(set(display_orders)):
        raise LifecycleValidationError(
            code="DUPLICATE_DISPLAY_ORDER",
            message="Each question must have a unique display order.",
            blocking_errors=["Duplicate display orders detected across questions."],
        )

    # 6. Recalculate content hash
    # Build interview question schema objects
    common_questions = [q for q in template.questions if q.layer == InterviewLayer.COMMON_CORE]
    schema_questions: List[InterviewQuestion] = []
    for q_record in sorted(common_questions, key=lambda q: q.display_order):
        rubric_schema = None
        if q_record.rubric:
            rubric_schema = InterviewRubric(
                rubric_id=q_record.rubric.rubric_id,
                rubric_version=q_record.rubric.rubric_version,
                competency_definition=q_record.rubric.competency_definition,
                question_intent=q_record.rubric.question_intent,
                observable_indicators=list(q_record.rubric.observable_indicators),
                rating_anchors={int(k): str(v) for k, v in q_record.rubric.rating_anchors.items()},
                sufficient_evidence_guidance=q_record.rubric.sufficient_evidence_guidance,
                insufficient_evidence_guidance=q_record.rubric.insufficient_evidence_guidance,
                permitted_probes=list(q_record.rubric.permitted_probes),
                prohibited_inferences=list(q_record.rubric.prohibited_inferences),
                validation_state=q_record.rubric.validation_state,
                policy_reference=snapshot.policy_reference,
                schema_version=BLUEPRINT_SCHEMA_VERSION,
            )

        schema_questions.append(
            InterviewQuestion(
                question_id=q_record.question_id,
                blueprint_id=template.template_id,
                layer=q_record.layer,
                question_type=q_record.question_type,
                competency_id=q_record.competency_id,
                requirement_references=[uuid.UUID(r) for r in q_record.requirement_ids],
                source_match_result_id=None,
                source_gap_report_id=None,
                deterministic_template_id=q_record.deterministic_template_id,
                deterministic_template_version=q_record.deterministic_template_version,
                question_text=q_record.question_text,
                question_intent=q_record.question_intent,
                authoring_origin=q_record.authoring_origin,
                display_order=q_record.display_order,
                estimated_time_minutes=q_record.estimated_time_minutes,
                permitted_probes=list(q_record.permitted_probes),
                validation_state=q_record.validation_state,
                included_in_common_score=q_record.included_in_common_score,
                rubric=rubric_schema,
                policy_reference=snapshot.policy_reference,
                schema_version=BLUEPRINT_SCHEMA_VERSION,
            )
        )

    # Compute coverage classifications
    classifications = [evaluate_requirement_interview_suitability(r) for r in snapshot.approved_requirements]
    coverage = calculate_blueprint_coverage(
        snapshot=snapshot,
        classifications=classifications,
        selected_competencies=[],
        uncovered_eligible=[],
        unsupported_competencies=[],
        questions=schema_questions,
    )

    new_content_hash = compute_deterministic_blueprint_content_hash(
        snapshot=snapshot,
        questions=schema_questions,
        coverage=coverage,
    )

    template.content_hash = new_content_hash
    template.record_version += 1
    template.lifecycle_status = PlanLifecycle.DRAFT
    template.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(template)

    logger.info(
        "Job interview template draft edited successfully. template_id=%s, version=%s, record_version=%s",
        template.template_id,
        template.template_version,
        template.record_version,
    )
    return template


# =========================================================================
# JobInterviewTemplate: Validation
# =========================================================================

def validate_job_interview_template_draft(
    actor: UserRecord,
    job_id: uuid.UUID,
    template_id: uuid.UUID,
    db: Session,
) -> ValidationSummaryResponse:
    """Explicitly validates a DRAFT template against all safety & blueprint rules."""
    template = (
        db.query(JobInterviewTemplateRecord)
        .options(
            selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
        )
        .filter(
            JobInterviewTemplateRecord.template_id == template_id,
            JobInterviewTemplateRecord.job_id == job_id,
        )
        .first()
    )
    if not template:
        raise InterviewSnapshotError(
            code="TEMPLATE_NOT_FOUND",
            message="Job interview template not found.",
            status_code=404,
        )

    _check_recruiter_authorization(actor, template.requisition)

    if template.lifecycle_status not in (PlanLifecycle.DRAFT, PlanLifecycle.VALIDATED):
        raise LifecycleConflictError(
            code="INVALID_LIFECYCLE_STATE",
            message=f"Cannot validate template in {template.lifecycle_status.value} state.",
        )

    snapshot, _ = load_authoritative_job_blueprint_snapshot(
        actor=actor,
        job_id=job_id,
        expected_revision=template.revision,
        db=db,
    )

    common_questions = [q for q in template.questions if q.layer == InterviewLayer.COMMON_CORE]
    schema_questions: List[InterviewQuestion] = []
    for q_record in sorted(common_questions, key=lambda q: q.display_order):
        rubric_schema = None
        if q_record.rubric:
            rubric_schema = InterviewRubric(
                rubric_id=q_record.rubric.rubric_id,
                rubric_version=q_record.rubric.rubric_version,
                competency_definition=q_record.rubric.competency_definition,
                question_intent=q_record.rubric.question_intent,
                observable_indicators=list(q_record.rubric.observable_indicators),
                rating_anchors={int(k): str(v) for k, v in q_record.rubric.rating_anchors.items()},
                sufficient_evidence_guidance=q_record.rubric.sufficient_evidence_guidance,
                insufficient_evidence_guidance=q_record.rubric.insufficient_evidence_guidance,
                permitted_probes=list(q_record.rubric.permitted_probes),
                prohibited_inferences=list(q_record.rubric.prohibited_inferences),
                validation_state=q_record.rubric.validation_state,
                policy_reference=snapshot.policy_reference,
                schema_version=BLUEPRINT_SCHEMA_VERSION,
            )

        schema_questions.append(
            InterviewQuestion(
                question_id=q_record.question_id,
                blueprint_id=template.template_id,
                layer=q_record.layer,
                question_type=q_record.question_type,
                competency_id=q_record.competency_id,
                requirement_references=[uuid.UUID(r) for r in q_record.requirement_ids],
                source_match_result_id=None,
                source_gap_report_id=None,
                deterministic_template_id=q_record.deterministic_template_id,
                deterministic_template_version=q_record.deterministic_template_version,
                question_text=q_record.question_text,
                question_intent=q_record.question_intent,
                authoring_origin=q_record.authoring_origin,
                display_order=q_record.display_order,
                estimated_time_minutes=q_record.estimated_time_minutes,
                permitted_probes=list(q_record.permitted_probes),
                validation_state=q_record.validation_state,
                included_in_common_score=q_record.included_in_common_score,
                rubric=rubric_schema,
                policy_reference=snapshot.policy_reference,
                schema_version=BLUEPRINT_SCHEMA_VERSION,
            )
        )

    classifications = [evaluate_requirement_interview_suitability(r) for r in snapshot.approved_requirements]
    coverage = calculate_blueprint_coverage(
        snapshot=snapshot,
        classifications=classifications,
        selected_competencies=[],
        uncovered_eligible=[],
        unsupported_competencies=[],
        questions=schema_questions,
    )

    validator = InterviewPolicyValidator()
    report = validator.validate_blueprint(
        questions=schema_questions,
        snapshot=snapshot,
        coverage=coverage,
    )

    validated_hash = None
    if report.is_valid:
        validated_hash = template.content_hash
        template.lifecycle_status = PlanLifecycle.VALIDATED
        for q in template.questions:
            q.validation_state = ValidationState.PASSED
        db.commit()
    else:
        template.lifecycle_status = PlanLifecycle.DRAFT
        for q in template.questions:
            q.validation_state = ValidationState.BLOCKED
        db.commit()

    return ValidationSummaryResponse(
        record_id=template.template_id,
        record_version=template.template_version,
        lifecycle_status=template.lifecycle_status,
        is_valid=report.is_valid,
        content_hash=template.content_hash or "",
        validated_content_hash=validated_hash,
        blocking_errors=report.blocking_errors,
        warnings=report.warnings,
        checked_rules_count=report.checked_rules_count,
        validator_version=DETERMINISTIC_BUILDER_VERSION,
        policy_version=SAFETY_POLICY_VERSION,
        validation_timestamp=datetime.now(timezone.utc),
    )


# =========================================================================
# JobInterviewTemplate: Approval
# =========================================================================

def approve_job_interview_template(
    actor: UserRecord,
    job_id: uuid.UUID,
    template_id: uuid.UUID,
    request: InterviewApprovalRequest,
    db: Session,
) -> ApprovalSummaryResponse:
    """Explicitly transitions a VALIDATED template to APPROVED and immutable."""
    template = (
        db.query(JobInterviewTemplateRecord)
        .options(selectinload(JobInterviewTemplateRecord.requisition))
        .filter(
            JobInterviewTemplateRecord.template_id == template_id,
            JobInterviewTemplateRecord.job_id == job_id,
        )
        .first()
    )
    if not template:
        raise InterviewSnapshotError(
            code="TEMPLATE_NOT_FOUND",
            message="Job interview template not found.",
            status_code=404,
        )

    _check_recruiter_authorization(actor, template.requisition)

    if template.lifecycle_status == PlanLifecycle.APPROVED:
        # Idempotent response for already approved record with same hash
        if template.content_hash == request.expected_content_hash:
            return ApprovalSummaryResponse(
                record_id=template.template_id,
                record_version=template.template_version,
                lifecycle_status=template.lifecycle_status,
                approved_by_user_id=template.created_by_user_id,
                approved_at=template.updated_at,
                content_hash=template.content_hash or "",
                is_immutable=True,
                parent_record_id=template.parent_template_id,
                superseded_parent_id=template.parent_template_id,
            )
        raise LifecycleConflictError(
            code="ALREADY_APPROVED_DIFFERENT_HASH",
            message="Template is already approved with a different content hash.",
        )

    if template.lifecycle_status != PlanLifecycle.VALIDATED:
        raise LifecycleConflictError(
            code="TEMPLATE_NOT_VALIDATED",
            message=f"Cannot approve template in {template.lifecycle_status.value} state. Must be VALIDATED first.",
        )

    if template.content_hash != request.expected_content_hash:
        raise LifecycleConflictError(
            code="CONTENT_HASH_MISMATCH",
            message="Expected content hash does not match current validated template hash.",
        )

    superseded_parent_id = None
    if template.parent_template_id:
        parent = (
            db.query(JobInterviewTemplateRecord)
            .filter(JobInterviewTemplateRecord.template_id == template.parent_template_id)
            .first()
        )
        if parent and parent.lifecycle_status == PlanLifecycle.APPROVED:
            parent.lifecycle_status = PlanLifecycle.SUPERSEDED
            parent.updated_at = datetime.now(timezone.utc)
            superseded_parent_id = parent.template_id

    template.lifecycle_status = PlanLifecycle.APPROVED
    template.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(template)

    logger.info(
        "Job interview template approved. template_id=%s, version=%s, approver=%s",
        template.template_id,
        template.template_version,
        actor.user_id,
    )

    return ApprovalSummaryResponse(
        record_id=template.template_id,
        record_version=template.template_version,
        lifecycle_status=template.lifecycle_status,
        approved_by_user_id=actor.user_id,
        approved_at=template.updated_at,
        content_hash=template.content_hash or "",
        is_immutable=True,
        parent_record_id=template.parent_template_id,
        superseded_parent_id=superseded_parent_id,
    )


# =========================================================================
# JobInterviewTemplate: Successor Version Creation
# =========================================================================

def create_job_interview_template_successor(
    actor: UserRecord,
    job_id: uuid.UUID,
    template_id: uuid.UUID,
    request: SuccessorDraftCreateRequest,
    db: Session,
) -> JobInterviewTemplateRecord:
    """Creates a successor DRAFT from an APPROVED template version."""
    parent = (
        db.query(JobInterviewTemplateRecord)
        .options(
            selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
        )
        .filter(
            JobInterviewTemplateRecord.template_id == template_id,
            JobInterviewTemplateRecord.job_id == job_id,
        )
        .first()
    )
    if not parent:
        raise InterviewSnapshotError(
            code="TEMPLATE_NOT_FOUND",
            message="Job interview template not found.",
            status_code=404,
        )

    _check_recruiter_authorization(actor, parent.requisition)

    if parent.lifecycle_status != PlanLifecycle.APPROVED:
        raise LifecycleConflictError(
            code="PARENT_NOT_APPROVED",
            message=f"Cannot create successor from template in {parent.lifecycle_status.value} state. Parent must be APPROVED.",
        )

    # Determine highest template_version for this job
    max_version = (
        db.query(func.max(JobInterviewTemplateRecord.template_version))
        .filter(JobInterviewTemplateRecord.job_id == job_id)
        .scalar()
        or parent.template_version
    )
    next_version = max_version + 1

    successor = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=parent.organization_id,
        job_id=parent.job_id,
        job_version_id=parent.job_version_id,
        revision=parent.revision,
        template_version=next_version,
        parent_template_id=parent.template_id,
        lifecycle_status=PlanLifecycle.DRAFT,
        guardrail_policy_version=parent.guardrail_policy_version,
        guardrail_policy_hash=parent.guardrail_policy_hash,
        deterministic_builder_version=parent.deterministic_builder_version,
        question_catalogue_version=parent.question_catalogue_version,
        rubric_catalogue_version=parent.rubric_catalogue_version,
        input_snapshot_hash=parent.input_snapshot_hash,
        content_hash=parent.content_hash,
        created_by_user_id=actor.user_id,
        record_version=1,
    )
    db.add(successor)
    db.flush()

    # Clone questions
    for pq in parent.questions:
        sq = InterviewQuestionRecord(
            question_id=uuid.uuid4(),
            template_id=successor.template_id,
            question_version=1,
            layer=pq.layer,
            question_type=pq.question_type,
            authoring_origin=pq.authoring_origin,
            display_order=pq.display_order,
            competency_id=pq.competency_id,
            requirement_ids=list(pq.requirement_ids),
            deterministic_template_id=pq.deterministic_template_id,
            deterministic_template_version=pq.deterministic_template_version,
            question_text=pq.question_text,
            question_intent=pq.question_intent,
            estimated_time_minutes=pq.estimated_time_minutes,
            included_in_common_score=pq.included_in_common_score,
            validation_state=ValidationState.PENDING,
            permitted_probes=list(pq.permitted_probes),
            safe_metadata={"copied_from_parent_question_id": str(pq.question_id)},
        )
        db.add(sq)
        db.flush()

        if pq.rubric:
            pr = pq.rubric
            sr = InterviewRubricRecord(
                rubric_id=uuid.uuid4(),
                question_id=sq.question_id,
                rubric_version=1,
                competency_definition=pr.competency_definition,
                question_intent=pr.question_intent,
                observable_indicators=list(pr.observable_indicators),
                rating_anchors=dict(pr.rating_anchors),
                sufficient_evidence_guidance=pr.sufficient_evidence_guidance,
                insufficient_evidence_guidance=pr.insufficient_evidence_guidance,
                incomplete_answer_guidance=pr.incomplete_answer_guidance,
                permitted_probes=list(pr.permitted_probes),
                prohibited_inferences=list(pr.prohibited_inferences),
                subject_matter_review_status=pr.subject_matter_review_status,
                validation_state=ValidationState.PASSED,
            )
            db.add(sr)

    db.commit()
    db.refresh(successor)

    logger.info(
        "Job interview template successor draft created. new_template_id=%s, version=%s, parent_id=%s",
        successor.template_id,
        successor.template_version,
        parent.template_id,
    )
    return successor


# =========================================================================
# CandidateInterviewPlan: Safe Edit
# =========================================================================

def edit_candidate_interview_plan_draft(
    actor: UserRecord,
    job_id: uuid.UUID,
    template_id: uuid.UUID,
    plan_id: uuid.UUID,
    request: CandidatePlanEditRequest,
    db: Session,
) -> CandidateInterviewPlanRecord:
    """Safely updates supplemental question wording and time on a DRAFT candidate plan."""
    plan = (
        db.query(CandidateInterviewPlanRecord)
        .options(
            selectinload(CandidateInterviewPlanRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(CandidateInterviewPlanRecord.provenances).selectinload(SupplementalQuestionProvenanceRecord.question),
        )
        .filter(
            CandidateInterviewPlanRecord.plan_id == plan_id,
            CandidateInterviewPlanRecord.template_id == template_id,
        )
        .first()
    )
    if not plan:
        raise InterviewSnapshotError(
            code="PLAN_NOT_FOUND",
            message="Candidate interview plan not found.",
            status_code=404,
        )

    _check_recruiter_authorization(actor, plan.template.requisition)

    if plan.lifecycle_status != PlanLifecycle.DRAFT:
        raise LifecycleConflictError(
            code="IMMUTABLE_NON_DRAFT_STATE",
            message=f"Cannot edit candidate plan in {plan.lifecycle_status.value} state. Approved records are immutable.",
        )

    if plan.record_version != request.expected_record_version:
        raise LifecycleConflictError(
            code="STALE_RECORD_VERSION",
            message=f"Conflict: Expected record version {request.expected_record_version}, but database is at {plan.record_version}.",
        )
    if plan.content_hash != request.expected_content_hash:
        raise LifecycleConflictError(
            code="STALE_CONTENT_HASH",
            message="Conflict: Candidate plan content has been modified concurrently.",
        )

    # Map supplemental questions via provenance
    prov_q_map = {prov.question.question_id: prov for prov in plan.provenances if prov.question}
    edit_q_ids = {item.question_id for item in request.supplemental_questions}

    if set(prov_q_map.keys()) != edit_q_ids:
        raise LifecycleConflictError(
            code="STRUCTURAL_QUESTION_SET_MISMATCH",
            message="Cannot add or delete supplemental questions via safe edit.",
        )

    display_orders = []
    for item in request.supplemental_questions:
        prov = prov_q_map[item.question_id]
        q_record = prov.question

        violation = check_prohibited_topics(item.question_text)
        if violation:
            raise LifecycleValidationError(
                code="PROHIBITED_TOPIC_DETECTED",
                message=f"Question text violates safety policy on prohibited topic '{violation[0]}'.",
                blocking_errors=[f"Prohibited topic '{violation[0]}' in question {item.question_id}"],
            )
        if item.clarification_purpose:
            violation = check_prohibited_topics(item.clarification_purpose)
            if violation:
                raise LifecycleValidationError(
                    code="PROHIBITED_TOPIC_DETECTED",
                    message=f"Clarification purpose violates safety policy on prohibited topic '{violation[0]}'.",
                    blocking_errors=[f"Prohibited topic '{violation[0]}' in clarification purpose"],
                )

        if q_record.question_text.strip() != item.question_text.strip():
            q_record.question_text = item.question_text.strip()
            q_record.authoring_origin = AuthoringOrigin.RECRUITER_EDITED
            q_record.question_version += 1

        q_record.display_order = item.display_order
        q_record.estimated_time_minutes = item.estimated_time_minutes
        q_record.validation_state = ValidationState.PENDING
        display_orders.append(item.display_order)

    if len(display_orders) != len(set(display_orders)):
        raise LifecycleValidationError(
            code="DUPLICATE_DISPLAY_ORDER",
            message="Each supplemental question must have a unique display order.",
            blocking_errors=["Duplicate display orders detected across supplemental questions."],
        )

    # Reconstruct generated candidate plan schema to compute canonical content hash
    supplemental_schema_questions: List[CandidateSupplementalQuestion] = []
    for prov in plan.provenances:
        q = prov.question
        if not q:
            continue
        supplemental_schema_questions.append(
            CandidateSupplementalQuestion(
                question_id=q.question_id,
                layer=InterviewLayer.SUPPLEMENTAL_EVIDENCE,
                question_type=QuestionType.EVIDENCE_CLARIFICATION,
                authoring_origin=q.authoring_origin,
                display_order=q.display_order,
                requirement_references=[uuid.UUID(r) for r in q.requirement_ids],
                deterministic_template_id=q.deterministic_template_id or "UNKNOWN",
                deterministic_template_version=q.deterministic_template_version or "1",
                question_text=q.question_text,
                question_intent=q.question_intent,
                estimated_time_minutes=q.estimated_time_minutes,
                included_in_common_score=False,
                validation_state=q.validation_state,
                clarification_purpose=q.safe_metadata.get("clarification_purpose", "Evidence clarification"),
                facts_to_confirm=q.safe_metadata.get("facts_to_confirm", []),
                prohibited_inferences=q.safe_metadata.get("prohibited_inferences", []),
                source_requirement_id=prov.source_requirement_id,
                source_match_result_id=prov.source_match_result_id,
                source_match_state=prov.source_match_state,
                allowlisted_evidence_ids=list(prov.allowlisted_evidence_ids),
            )
        )

    # Common core question count and duration from template
    common_q_count = len(plan.template.questions) if plan.template else 0
    common_duration = sum(q.estimated_time_minutes for q in plan.template.questions) if plan.template else 0

    from app.services.candidate_plan_generator_service import CandidatePlanCoverageSummary
    coverage_summary = CandidatePlanCoverageSummary(
        selected_target_count=len(supplemental_schema_questions),
        target_selection_reasons={},
        match_states_clarified=list({q.source_match_state for q in supplemental_schema_questions}),
        total_supplemental_duration_minutes=sum(q.estimated_time_minutes for q in supplemental_schema_questions),
    )

    from app.schemas.interview_api import BlueprintValidationReportResponse
    generated_plan = GeneratedDeterministicCandidatePlan(
        plan_id=plan.plan_id,
        template_id=plan.template_id,
        template_version=plan.template_version,
        template_content_hash=plan.template.content_hash or "",
        job_id=job_id,
        job_revision=plan.template.revision,
        candidate_id=plan.candidate_id,
        profile_id=plan.profile_id,
        profile_version=plan.profile_version,
        match_result_id=plan.match_result_id,
        match_result_fingerprint=plan.match_result_fingerprint,
        plan_version=plan.plan_version,
        lifecycle_status=PlanLifecycle.DRAFT,
        scoring_separation_policy=SCORING_SEPARATION_POLICY,
        input_snapshot_hash=plan.input_snapshot_hash,
        content_hash="",
        common_core_questions_count=common_q_count,
        common_core_duration_minutes=common_duration,
        supplemental_questions=supplemental_schema_questions,
        coverage_summary=coverage_summary,
        validation_report=BlueprintValidationReportResponse(
            is_valid=True,
            blocking_errors=[],
            warnings=[],
            checked_rules_count=1,
        ),
        supplemental_catalogue_version=SUPPLEMENTAL_QUESTION_CATALOGUE_VERSION,
        policy_version=SAFETY_POLICY_VERSION,
    )

    new_content_hash = compute_candidate_interview_plan_content_hash(generated_plan)

    plan.content_hash = new_content_hash
    plan.record_version += 1
    plan.lifecycle_status = PlanLifecycle.DRAFT
    plan.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(plan)

    logger.info(
        "Candidate interview plan draft edited successfully. plan_id=%s, version=%s, record_version=%s",
        plan.plan_id,
        plan.plan_version,
        plan.record_version,
    )
    return plan


# =========================================================================
# CandidateInterviewPlan: Validation
# =========================================================================

def validate_candidate_interview_plan_draft(
    actor: UserRecord,
    job_id: uuid.UUID,
    template_id: uuid.UUID,
    plan_id: uuid.UUID,
    db: Session,
) -> ValidationSummaryResponse:
    """Explicitly validates a DRAFT candidate plan against all safety & scoring separation rules."""
    plan = (
        db.query(CandidateInterviewPlanRecord)
        .options(
            selectinload(CandidateInterviewPlanRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(CandidateInterviewPlanRecord.provenances).selectinload(SupplementalQuestionProvenanceRecord.question),
        )
        .filter(
            CandidateInterviewPlanRecord.plan_id == plan_id,
            CandidateInterviewPlanRecord.template_id == template_id,
        )
        .first()
    )
    if not plan:
        raise InterviewSnapshotError(
            code="PLAN_NOT_FOUND",
            message="Candidate interview plan not found.",
            status_code=404,
        )

    _check_recruiter_authorization(actor, plan.template.requisition)

    if plan.lifecycle_status not in (PlanLifecycle.DRAFT, PlanLifecycle.VALIDATED):
        raise LifecycleConflictError(
            code="INVALID_LIFECYCLE_STATE",
            message=f"Cannot validate candidate plan in {plan.lifecycle_status.value} state.",
        )

    snapshot, _ = load_authoritative_candidate_plan_snapshot(
        actor=actor,
        job_id=job_id,
        expected_revision=plan.template.revision,
        candidate_id=plan.candidate_id,
        profile_version=plan.profile_version,
        match_result_id=plan.match_result_id,
        db=db,
    )

    supplemental_schema_questions: List[CandidateSupplementalQuestion] = []
    for prov in plan.provenances:
        q = prov.question
        if not q:
            continue
        supplemental_schema_questions.append(
            CandidateSupplementalQuestion(
                question_id=q.question_id,
                layer=q.layer,
                question_type=q.question_type,
                authoring_origin=q.authoring_origin,
                display_order=q.display_order,
                requirement_references=[uuid.UUID(r) for r in q.requirement_ids],
                deterministic_template_id=q.deterministic_template_id or "UNKNOWN",
                deterministic_template_version=q.deterministic_template_version or "1",
                question_text=q.question_text,
                question_intent=q.question_intent,
                estimated_time_minutes=q.estimated_time_minutes,
                included_in_common_score=q.included_in_common_score,
                validation_state=q.validation_state,
                clarification_purpose=q.safe_metadata.get("clarification_purpose", "Evidence clarification"),
                facts_to_confirm=q.safe_metadata.get("facts_to_confirm", []),
                prohibited_inferences=q.safe_metadata.get("prohibited_inferences", []),
                source_requirement_id=prov.source_requirement_id,
                source_match_result_id=prov.source_match_result_id,
                source_match_state=prov.source_match_state,
                allowlisted_evidence_ids=list(prov.allowlisted_evidence_ids),
            )
        )

    report = CandidatePlanPolicyValidator.validate(
        snapshot=snapshot,
        template_record=plan.template,
        questions=supplemental_schema_questions,
        targets=[],
    )

    validated_hash = None
    if report.is_valid:
        validated_hash = plan.content_hash
        plan.lifecycle_status = PlanLifecycle.VALIDATED
        for prov in plan.provenances:
            if prov.question:
                prov.question.validation_state = ValidationState.PASSED
        db.commit()
    else:
        plan.lifecycle_status = PlanLifecycle.DRAFT
        for prov in plan.provenances:
            if prov.question:
                prov.question.validation_state = ValidationState.BLOCKED
        db.commit()

    return ValidationSummaryResponse(
        record_id=plan.plan_id,
        record_version=plan.plan_version,
        lifecycle_status=plan.lifecycle_status,
        is_valid=report.is_valid,
        content_hash=plan.content_hash or "",
        validated_content_hash=validated_hash,
        blocking_errors=report.blocking_errors,
        warnings=report.warnings,
        checked_rules_count=report.checked_rules_count,
        validator_version=DETERMINISTIC_BUILDER_VERSION,
        policy_version=SAFETY_POLICY_VERSION,
        validation_timestamp=datetime.now(timezone.utc),
    )


# =========================================================================
# CandidateInterviewPlan: Approval
# =========================================================================

def approve_candidate_interview_plan(
    actor: UserRecord,
    job_id: uuid.UUID,
    template_id: uuid.UUID,
    plan_id: uuid.UUID,
    request: InterviewApprovalRequest,
    db: Session,
) -> ApprovalSummaryResponse:
    """Explicitly transitions a VALIDATED candidate plan to APPROVED (requires template to be APPROVED)."""
    plan = (
        db.query(CandidateInterviewPlanRecord)
        .options(selectinload(CandidateInterviewPlanRecord.template).selectinload(JobInterviewTemplateRecord.requisition))
        .filter(
            CandidateInterviewPlanRecord.plan_id == plan_id,
            CandidateInterviewPlanRecord.template_id == template_id,
        )
        .first()
    )
    if not plan:
        raise InterviewSnapshotError(
            code="PLAN_NOT_FOUND",
            message="Candidate interview plan not found.",
            status_code=404,
        )

    _check_recruiter_authorization(actor, plan.template.requisition)

    if plan.lifecycle_status == PlanLifecycle.APPROVED:
        if plan.content_hash == request.expected_content_hash:
            return ApprovalSummaryResponse(
                record_id=plan.plan_id,
                record_version=plan.plan_version,
                lifecycle_status=plan.lifecycle_status,
                approved_by_user_id=plan.created_by_user_id,
                approved_at=plan.updated_at,
                content_hash=plan.content_hash or "",
                is_immutable=True,
                parent_record_id=plan.parent_plan_id,
                superseded_parent_id=plan.parent_plan_id,
            )
        raise LifecycleConflictError(
            code="ALREADY_APPROVED_DIFFERENT_HASH",
            message="Candidate plan is already approved with a different content hash.",
        )

    if plan.lifecycle_status != PlanLifecycle.VALIDATED:
        raise LifecycleConflictError(
            code="PLAN_NOT_VALIDATED",
            message=f"Cannot approve candidate plan in {plan.lifecycle_status.value} state. Must be VALIDATED first.",
        )

    if plan.content_hash != request.expected_content_hash:
        raise LifecycleConflictError(
            code="CONTENT_HASH_MISMATCH",
            message="Expected content hash does not match current validated candidate plan hash.",
        )

    # CRITICAL INVARIANT: Bound template must be APPROVED
    if plan.template.lifecycle_status != PlanLifecycle.APPROVED:
        raise LifecycleConflictError(
            code="TEMPLATE_NOT_APPROVED",
            message=f"Candidate plan cannot be approved because referenced template version {plan.template.template_version} is in {plan.template.lifecycle_status.value} state. Template must be APPROVED first.",
        )

    superseded_parent_id = None
    if plan.parent_plan_id:
        parent = (
            db.query(CandidateInterviewPlanRecord)
            .filter(CandidateInterviewPlanRecord.plan_id == plan.parent_plan_id)
            .first()
        )
        if parent and parent.lifecycle_status == PlanLifecycle.APPROVED:
            parent.lifecycle_status = PlanLifecycle.SUPERSEDED
            parent.updated_at = datetime.now(timezone.utc)
            superseded_parent_id = parent.plan_id

    plan.lifecycle_status = PlanLifecycle.APPROVED
    plan.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(plan)

    logger.info(
        "Candidate interview plan approved. plan_id=%s, version=%s, approver=%s",
        plan.plan_id,
        plan.plan_version,
        actor.user_id,
    )

    return ApprovalSummaryResponse(
        record_id=plan.plan_id,
        record_version=plan.plan_version,
        lifecycle_status=plan.lifecycle_status,
        approved_by_user_id=actor.user_id,
        approved_at=plan.updated_at,
        content_hash=plan.content_hash or "",
        is_immutable=True,
        parent_record_id=plan.parent_plan_id,
        superseded_parent_id=superseded_parent_id,
    )


# =========================================================================
# CandidateInterviewPlan: Successor Version Creation
# =========================================================================

def create_candidate_interview_plan_successor(
    actor: UserRecord,
    job_id: uuid.UUID,
    template_id: uuid.UUID,
    plan_id: uuid.UUID,
    request: SuccessorDraftCreateRequest,
    db: Session,
) -> CandidateInterviewPlanRecord:
    """Creates a successor DRAFT from an APPROVED candidate plan version."""
    parent = (
        db.query(CandidateInterviewPlanRecord)
        .options(
            selectinload(CandidateInterviewPlanRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(CandidateInterviewPlanRecord.provenances).selectinload(SupplementalQuestionProvenanceRecord.question),
        )
        .filter(
            CandidateInterviewPlanRecord.plan_id == plan_id,
            CandidateInterviewPlanRecord.template_id == template_id,
        )
        .first()
    )
    if not parent:
        raise InterviewSnapshotError(
            code="PLAN_NOT_FOUND",
            message="Candidate interview plan not found.",
            status_code=404,
        )

    _check_recruiter_authorization(actor, parent.template.requisition)

    if parent.lifecycle_status != PlanLifecycle.APPROVED:
        raise LifecycleConflictError(
            code="PARENT_NOT_APPROVED",
            message=f"Cannot create successor from candidate plan in {parent.lifecycle_status.value} state. Parent must be APPROVED.",
        )

    max_version = (
        db.query(func.max(CandidateInterviewPlanRecord.plan_version))
        .filter(
            CandidateInterviewPlanRecord.candidate_id == parent.candidate_id,
            CandidateInterviewPlanRecord.template_id == template_id,
        )
        .scalar()
        or parent.plan_version
    )
    next_version = max_version + 1

    successor = CandidateInterviewPlanRecord(
        plan_id=uuid.uuid4(),
        organization_id=parent.organization_id,
        template_id=parent.template_id,
        template_version=parent.template_version,
        candidate_id=parent.candidate_id,
        profile_id=parent.profile_id,
        profile_version=parent.profile_version,
        match_result_id=parent.match_result_id,
        match_result_fingerprint=parent.match_result_fingerprint,
        plan_version=next_version,
        parent_plan_id=parent.plan_id,
        lifecycle_status=PlanLifecycle.DRAFT,
        guardrail_policy_version=parent.guardrail_policy_version,
        scoring_separation_policy=parent.scoring_separation_policy,
        input_snapshot_hash=parent.input_snapshot_hash,
        content_hash=parent.content_hash,
        created_by_user_id=actor.user_id,
        record_version=1,
    )
    db.add(successor)
    db.flush()

    # Clone supplemental questions and provenance
    for pp in parent.provenances:
        pq = pp.question
        if not pq:
            continue
        sq = InterviewQuestionRecord(
            question_id=uuid.uuid4(),
            template_id=parent.template_id,
            question_version=1,
            layer=pq.layer,
            question_type=pq.question_type,
            authoring_origin=pq.authoring_origin,
            display_order=pq.display_order,
            competency_id=pq.competency_id,
            requirement_ids=list(pq.requirement_ids),
            deterministic_template_id=pq.deterministic_template_id,
            deterministic_template_version=pq.deterministic_template_version,
            question_text=pq.question_text,
            question_intent=pq.question_intent,
            estimated_time_minutes=pq.estimated_time_minutes,
            included_in_common_score=False,
            validation_state=ValidationState.PENDING,
            permitted_probes=list(pq.permitted_probes),
            safe_metadata=dict(pq.safe_metadata),
        )
        db.add(sq)
        db.flush()

        sp = SupplementalQuestionProvenanceRecord(
            provenance_id=uuid.uuid4(),
            question_id=sq.question_id,
            plan_id=successor.plan_id,
            source_requirement_id=pp.source_requirement_id,
            source_match_result_id=pp.source_match_result_id,
            source_gap_report_id=pp.source_gap_report_id,
            source_match_state=pp.source_match_state,
            allowlisted_evidence_ids=list(pp.allowlisted_evidence_ids),
            excluded_from_common_score=True,
            provenance_metadata={"copied_from_parent_plan_id": str(parent.plan_id)},
        )
        db.add(sp)

    db.commit()
    db.refresh(successor)

    logger.info(
        "Candidate interview plan successor draft created. new_plan_id=%s, version=%s, parent_id=%s",
        successor.plan_id,
        successor.plan_version,
        parent.plan_id,
    )
    return successor


# =========================================================================
# Version History & Diffs
# =========================================================================

def get_template_version_history(
    actor: UserRecord,
    job_id: uuid.UUID,
    db: Session,
) -> VersionHistoryResponse:
    """Returns the ordered version history for job interview templates."""
    job = db.query(JobRequisition).filter(JobRequisition.job_id == job_id).first()
    if not job:
        raise InterviewRequisitionNotFoundError("REQUISITION_NOT_FOUND", "Job requisition not found.")
    _check_recruiter_authorization(actor, job)

    templates = (
        db.query(JobInterviewTemplateRecord)
        .options(selectinload(JobInterviewTemplateRecord.questions))
        .filter(JobInterviewTemplateRecord.job_id == job_id)
        .order_by(JobInterviewTemplateRecord.template_version.desc())
        .all()
    )

    current_ver = templates[0].template_version if templates else 1
    items: List[VersionSummaryItem] = []
    for t in templates:
        items.append(
            VersionSummaryItem(
                version_id=t.template_id,
                version_number=t.template_version,
                lifecycle_status=t.lifecycle_status,
                content_hash=t.content_hash or "",
                created_by_user_id=t.created_by_user_id,
                created_at=t.created_at,
                approved_by_user_id=t.created_by_user_id if t.lifecycle_status == PlanLifecycle.APPROVED else None,
                approved_at=t.updated_at if t.lifecycle_status == PlanLifecycle.APPROVED else None,
                parent_id=t.parent_template_id,
                policy_version=t.guardrail_policy_version,
                questions_count=len(t.questions),
                total_duration_minutes=sum(q.estimated_time_minutes for q in t.questions),
                edit_reason=None,
            )
        )

    return VersionHistoryResponse(
        resource_type="job_interview_template",
        resource_id=job_id,
        current_active_version=current_ver,
        versions=items,
    )


def compute_template_version_diff(
    actor: UserRecord,
    job_id: uuid.UUID,
    version_from: int,
    version_to: int,
    db: Session,
) -> VersionDiffResponse:
    """Computes field-level difference between two template versions."""
    job = db.query(JobRequisition).filter(JobRequisition.job_id == job_id).first()
    if not job:
        raise InterviewRequisitionNotFoundError("REQUISITION_NOT_FOUND", "Job requisition not found.")
    _check_recruiter_authorization(actor, job)

    t1 = (
        db.query(JobInterviewTemplateRecord)
        .options(selectinload(JobInterviewTemplateRecord.questions))
        .filter(
            JobInterviewTemplateRecord.job_id == job_id,
            JobInterviewTemplateRecord.template_version == version_from,
        )
        .first()
    )
    t2 = (
        db.query(JobInterviewTemplateRecord)
        .options(selectinload(JobInterviewTemplateRecord.questions))
        .filter(
            JobInterviewTemplateRecord.job_id == job_id,
            JobInterviewTemplateRecord.template_version == version_to,
        )
        .first()
    )
    if not t1 or not t2:
        raise LifecycleConflictError("VERSION_NOT_FOUND", "One or both specified template versions not found.", status_code=404)

    # Compare questions by deterministic_template_id or competency_id
    q1_map = {q.deterministic_template_id or str(q.question_id): q for q in t1.questions}
    q2_map = {q.deterministic_template_id or str(q.question_id): q for q in t2.questions}

    all_keys = set(q1_map.keys()).union(set(q2_map.keys()))
    diff_items: List[QuestionDiffItem] = []
    changed_count = 0

    for k in sorted(all_keys):
        q1 = q1_map.get(k)
        q2 = q2_map.get(k)

        if q1 and not q2:
            diff_items.append(
                QuestionDiffItem(
                    question_id=str(q1.question_id),
                    display_order_v1=q1.display_order,
                    display_order_v2=None,
                    text_changed=True,
                    text_v1=q1.question_text,
                    text_v2=None,
                    time_changed=True,
                    time_v1=q1.estimated_time_minutes,
                    time_v2=None,
                    probes_changed=bool(q1.permitted_probes),
                    probes_v1=list(q1.permitted_probes),
                    probes_v2=[],
                    change_type="REMOVED",
                )
            )
            changed_count += 1
        elif not q1 and q2:
            diff_items.append(
                QuestionDiffItem(
                    question_id=str(q2.question_id),
                    display_order_v1=None,
                    display_order_v2=q2.display_order,
                    text_changed=True,
                    text_v1=None,
                    text_v2=q2.question_text,
                    time_changed=True,
                    time_v1=None,
                    time_v2=q2.estimated_time_minutes,
                    probes_changed=bool(q2.permitted_probes),
                    probes_v1=[],
                    probes_v2=list(q2.permitted_probes),
                    change_type="ADDED",
                )
            )
            changed_count += 1
        elif q1 and q2:
            text_changed = q1.question_text.strip() != q2.question_text.strip()
            time_changed = q1.estimated_time_minutes != q2.estimated_time_minutes
            probes_changed = set(q1.permitted_probes) != set(q2.permitted_probes)
            order_changed = q1.display_order != q2.display_order

            is_modified = text_changed or time_changed or probes_changed or order_changed
            if is_modified:
                changed_count += 1

            diff_items.append(
                QuestionDiffItem(
                    question_id=str(q2.question_id),
                    display_order_v1=q1.display_order,
                    display_order_v2=q2.display_order,
                    text_changed=text_changed,
                    text_v1=q1.question_text if text_changed else None,
                    text_v2=q2.question_text if text_changed else None,
                    time_changed=time_changed,
                    time_v1=q1.estimated_time_minutes if time_changed else None,
                    time_v2=q2.estimated_time_minutes if time_changed else None,
                    probes_changed=probes_changed,
                    probes_v1=list(q1.permitted_probes),
                    probes_v2=list(q2.permitted_probes),
                    change_type="MODIFIED" if is_modified else "UNCHANGED",
                )
            )

    return VersionDiffResponse(
        resource_type="job_interview_template",
        resource_id=job_id,
        version_from=version_from,
        version_to=version_to,
        lifecycle_status_from=t1.lifecycle_status,
        lifecycle_status_to=t2.lifecycle_status,
        content_hash_from=t1.content_hash or "",
        content_hash_to=t2.content_hash or "",
        total_duration_minutes_from=sum(q.estimated_time_minutes for q in t1.questions),
        total_duration_minutes_to=sum(q.estimated_time_minutes for q in t2.questions),
        changed_questions_count=changed_count,
        question_diffs=diff_items,
    )


def get_candidate_plan_version_history(
    actor: UserRecord,
    job_id: uuid.UUID,
    template_id: uuid.UUID,
    candidate_id: uuid.UUID,
    db: Session,
) -> VersionHistoryResponse:
    """Returns the ordered version history for candidate interview plans."""
    job = db.query(JobRequisition).filter(JobRequisition.job_id == job_id).first()
    if not job:
        raise InterviewRequisitionNotFoundError("REQUISITION_NOT_FOUND", "Job requisition not found.")
    _check_recruiter_authorization(actor, job)

    plans = (
        db.query(CandidateInterviewPlanRecord)
        .options(selectinload(CandidateInterviewPlanRecord.provenances).selectinload(SupplementalQuestionProvenanceRecord.question))
        .filter(
            CandidateInterviewPlanRecord.candidate_id == candidate_id,
            CandidateInterviewPlanRecord.template_id == template_id,
        )
        .order_by(CandidateInterviewPlanRecord.plan_version.desc())
        .all()
    )

    current_ver = plans[0].plan_version if plans else 1
    items: List[VersionSummaryItem] = []
    for p in plans:
        supp_questions = [prov.question for prov in p.provenances if prov.question]
        items.append(
            VersionSummaryItem(
                version_id=p.plan_id,
                version_number=p.plan_version,
                lifecycle_status=p.lifecycle_status,
                content_hash=p.content_hash or "",
                created_by_user_id=p.created_by_user_id,
                created_at=p.created_at,
                approved_by_user_id=p.created_by_user_id if p.lifecycle_status == PlanLifecycle.APPROVED else None,
                approved_at=p.updated_at if p.lifecycle_status == PlanLifecycle.APPROVED else None,
                parent_id=p.parent_plan_id,
                policy_version=p.guardrail_policy_version,
                questions_count=len(supp_questions),
                total_duration_minutes=sum(q.estimated_time_minutes for q in supp_questions),
                edit_reason=None,
            )
        )

    return VersionHistoryResponse(
        resource_type="candidate_interview_plan",
        resource_id=candidate_id,
        current_active_version=current_ver,
        versions=items,
    )


def compute_candidate_plan_version_diff(
    actor: UserRecord,
    job_id: uuid.UUID,
    template_id: uuid.UUID,
    candidate_id: uuid.UUID,
    version_from: int,
    version_to: int,
    db: Session,
) -> VersionDiffResponse:
    """Computes field-level difference between two candidate plan versions."""
    job = db.query(JobRequisition).filter(JobRequisition.job_id == job_id).first()
    if not job:
        raise InterviewRequisitionNotFoundError("REQUISITION_NOT_FOUND", "Job requisition not found.")
    _check_recruiter_authorization(actor, job)

    p1 = (
        db.query(CandidateInterviewPlanRecord)
        .options(selectinload(CandidateInterviewPlanRecord.provenances).selectinload(SupplementalQuestionProvenanceRecord.question))
        .filter(
            CandidateInterviewPlanRecord.candidate_id == candidate_id,
            CandidateInterviewPlanRecord.template_id == template_id,
            CandidateInterviewPlanRecord.plan_version == version_from,
        )
        .first()
    )
    p2 = (
        db.query(CandidateInterviewPlanRecord)
        .options(selectinload(CandidateInterviewPlanRecord.provenances).selectinload(SupplementalQuestionProvenanceRecord.question))
        .filter(
            CandidateInterviewPlanRecord.candidate_id == candidate_id,
            CandidateInterviewPlanRecord.template_id == template_id,
            CandidateInterviewPlanRecord.plan_version == version_to,
        )
        .first()
    )
    if not p1 or not p2:
        raise LifecycleConflictError("VERSION_NOT_FOUND", "One or both specified candidate plan versions not found.", status_code=404)

    q1_map = {str(prov.source_requirement_id): prov.question for prov in p1.provenances if prov.question}
    q2_map = {str(prov.source_requirement_id): prov.question for prov in p2.provenances if prov.question}

    all_keys = set(q1_map.keys()).union(set(q2_map.keys()))
    diff_items: List[QuestionDiffItem] = []
    changed_count = 0

    for k in sorted(all_keys):
        q1 = q1_map.get(k)
        q2 = q2_map.get(k)

        if q1 and not q2:
            diff_items.append(
                QuestionDiffItem(
                    question_id=str(q1.question_id),
                    display_order_v1=q1.display_order,
                    display_order_v2=None,
                    text_changed=True,
                    text_v1=q1.question_text,
                    text_v2=None,
                    time_changed=True,
                    time_v1=q1.estimated_time_minutes,
                    time_v2=None,
                    probes_changed=False,
                    probes_v1=[],
                    probes_v2=[],
                    change_type="REMOVED",
                )
            )
            changed_count += 1
        elif not q1 and q2:
            diff_items.append(
                QuestionDiffItem(
                    question_id=str(q2.question_id),
                    display_order_v1=None,
                    display_order_v2=q2.display_order,
                    text_changed=True,
                    text_v1=None,
                    text_v2=q2.question_text,
                    time_changed=True,
                    time_v1=None,
                    time_v2=q2.estimated_time_minutes,
                    probes_changed=False,
                    probes_v1=[],
                    probes_v2=[],
                    change_type="ADDED",
                )
            )
            changed_count += 1
        elif q1 and q2:
            text_changed = q1.question_text.strip() != q2.question_text.strip()
            time_changed = q1.estimated_time_minutes != q2.estimated_time_minutes
            order_changed = q1.display_order != q2.display_order

            is_modified = text_changed or time_changed or order_changed
            if is_modified:
                changed_count += 1

            diff_items.append(
                QuestionDiffItem(
                    question_id=str(q2.question_id),
                    display_order_v1=q1.display_order,
                    display_order_v2=q2.display_order,
                    text_changed=text_changed,
                    text_v1=q1.question_text if text_changed else None,
                    text_v2=q2.question_text if text_changed else None,
                    time_changed=time_changed,
                    time_v1=q1.estimated_time_minutes if time_changed else None,
                    time_v2=q2.estimated_time_minutes if time_changed else None,
                    probes_changed=False,
                    probes_v1=[],
                    probes_v2=[],
                    change_type="MODIFIED" if is_modified else "UNCHANGED",
                )
            )

    supp1 = [prov.question for prov in p1.provenances if prov.question]
    supp2 = [prov.question for prov in p2.provenances if prov.question]

    return VersionDiffResponse(
        resource_type="candidate_interview_plan",
        resource_id=candidate_id,
        version_from=version_from,
        version_to=version_to,
        lifecycle_status_from=p1.lifecycle_status,
        lifecycle_status_to=p2.lifecycle_status,
        content_hash_from=p1.content_hash or "",
        content_hash_to=p2.content_hash or "",
        total_duration_minutes_from=sum(q.estimated_time_minutes for q in supp1),
        total_duration_minutes_to=sum(q.estimated_time_minutes for q in supp2),
        changed_questions_count=changed_count,
        question_diffs=diff_items,
    )

