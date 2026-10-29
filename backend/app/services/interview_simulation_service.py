import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import UserRecord, UserRole
from app.models.candidate import CandidateProfileRecord
from app.models.interview import (
    CandidateInterviewPlanRecord,
    JobInterviewTemplateRecord,
    SimulationAnswerRecord,
    SimulationAttemptRecord,
    SupplementalQuestionProvenanceRecord,
)
from app.models.job import JobRequisition, JobVersion
from app.schemas.interview_api import (
    SimulationAnswerItemResponse,
    SimulationAnswerSaveRequest,
    SimulationAnswerSaveResponse,
    SimulationAttemptResponse,
    SimulationQuestionSnapshotResponse,
    SimulationSubmitRequest,
    SimulationSubmitResponse,
)
from app.schemas.phase3_audit import Phase3AuditEvent
from app.schemas.phase3_enums import AuditRetentionClass, InterviewLayer, PlanLifecycle
from app.schemas.safety_policy import (
    SAFETY_POLICY_HASH,
    SAFETY_POLICY_VERSION,
)
from app.services.interview_snapshot_service import (
    InterviewCandidateNotFoundError,
    InterviewRequisitionNotFoundError,
    InterviewSnapshotError,
    InterviewUnauthorizedError,
)

logger = logging.getLogger("ai_hiring.services.simulation")


class SimulationServiceError(InterviewSnapshotError):
    """Base error for simulation operations."""
    pass


def _verify_simulation_access(actor: UserRecord, job: JobRequisition) -> None:
    """Verifies that actor has authorized recruiter role for this requisition."""
    if actor.role == UserRole.ADMIN:
        return
    if actor.role != UserRole.RECRUITER:
        raise InterviewUnauthorizedError(
            message="Only recruiters and administrators may access simulations.",
            code="FORBIDDEN",
        )
    if job.created_by_user_id != actor.user_id:
        raise InterviewUnauthorizedError(
            message="Recruiter is not authorized to access simulations for this job requisition.",
            code="UNAUTHORIZED_JOB_ACCESS",
        )


def _serialize_answer_item(ans: SimulationAnswerRecord) -> SimulationAnswerItemResponse:
    return SimulationAnswerItemResponse(
        answer_id=ans.answer_id,
        question_id=ans.question_id,
        question_version=ans.question_version,
        display_order=ans.display_order,
        layer=ans.layer,
        answer_text=ans.answer_text,
        answer_version=ans.answer_version,
        is_submitted=ans.is_submitted,
        content_hash=ans.content_hash,
        updated_at=ans.updated_at.isoformat() if ans.updated_at else datetime.now(timezone.utc).isoformat(),
    )


def _serialize_attempt_response(
    attempt: SimulationAttemptRecord,
    candidate_name: Optional[str] = None,
    job_title: Optional[str] = None,
) -> SimulationAttemptResponse:
    questions_list = [
        SimulationQuestionSnapshotResponse(
            question_id=uuid.UUID(q["question_id"]) if isinstance(q["question_id"], str) else q["question_id"],
            question_version=q.get("question_version", 1),
            display_order=q.get("display_order", 0),
            layer=q.get("layer", "COMMON_CORE"),
            question_type=q.get("question_type", "TECHNICAL"),
            competency_name=q.get("competency_name", ""),
            question_text=q.get("question_text", ""),
            estimated_time_minutes=q.get("estimated_time_minutes", 5),
            permitted_probes=q.get("permitted_probes", []),
            rubric=q.get("rubric"),
        )
        for q in attempt.question_snapshots
    ]

    answers_dict = {
        str(ans.question_id): _serialize_answer_item(ans)
        for ans in attempt.answers
    }

    assessments_dict = {}
    if hasattr(attempt, "assessments") and attempt.assessments:
        for ass in attempt.assessments:
            assessments_dict[str(ass.question_id)] = {
                "question_id": str(ass.question_id),
                "assessment_status": ass.assessment_status,
                "provisional_level": ass.provisional_level,
                "insufficient_evidence_reason": ass.insufficient_evidence_reason,
                "criterion_observations": ass.criterion_observations or [],
                "cited_segment_ids": ass.cited_segment_ids or [],
                "uncertainty_codes": ass.uncertainty_codes or [],
                "validation_report": ass.validation_report or {},
                "model_invocation_metadata": ass.model_invocation_metadata or {},
            }

    return SimulationAttemptResponse(
        attempt_id=attempt.attempt_id,
        organization_id=attempt.organization_id,
        recruiter_user_id=attempt.recruiter_user_id,
        candidate_id=attempt.candidate_id,
        candidate_name=candidate_name,
        job_id=attempt.job_id,
        job_title=job_title,
        job_version_id=attempt.job_version_id,
        job_revision=attempt.job_revision,
        candidate_profile_version=attempt.candidate_profile_version,
        match_result_id=attempt.match_result_id,
        plan_id=attempt.plan_id,
        template_id=attempt.template_id,
        plan_version=attempt.plan_version,
        record_version=attempt.record_version,
        authoritative_content_hash=attempt.authoritative_content_hash,
        lifecycle_status=attempt.lifecycle_status,
        guardrail_policy_version=attempt.guardrail_policy_version,
        total_questions_count=attempt.total_questions_count,
        answered_questions_count=attempt.answered_questions_count,
        started_at=attempt.started_at.isoformat() if attempt.started_at else datetime.now(timezone.utc).isoformat(),
        submitted_at=attempt.submitted_at.isoformat() if attempt.submitted_at else None,
        questions=questions_list,
        answers=answers_dict,
        assessments=assessments_dict,
    )



def _resolve_candidate_name(db: Session, candidate_id: Optional[UUID], profile: Optional[CandidateProfileRecord]) -> Optional[str]:
    """Helper to safely extract candidate full name across model variations."""
    if profile:
        if hasattr(profile, "identity") and profile.identity and getattr(profile.identity, "full_name", None):
            return profile.identity.full_name
        if hasattr(profile, "full_name") and getattr(profile, "full_name", None):
            return profile.full_name
    if candidate_id:
        from app.models.candidate import CandidateIdentityRecord
        identity = db.execute(select(CandidateIdentityRecord).where(CandidateIdentityRecord.identity_id == candidate_id)).scalar_one_or_none()
        if identity and identity.full_name:
            return identity.full_name
    return None


def _resolve_job_title(job: JobRequisition) -> Optional[str]:
    """Helper to safely extract job title from requisition versions."""
    if hasattr(job, "title") and getattr(job, "title", None):
        return job.title
    if hasattr(job, "versions") and job.versions:
        sorted_vers = sorted(job.versions, key=lambda v: v.version_number, reverse=True)
        if sorted_vers and sorted_vers[0].title:
            return sorted_vers[0].title
    return None


def start_or_resume_simulation_attempt(
    actor: UserRecord,
    job_id: UUID,
    template_id: UUID,
    plan_id: UUID,
    db: Session,
) -> SimulationAttemptResponse:
    """Starts or resumes an authorized recruiter-controlled simulation attempt.
    
    Enforces:
    - Requisition and candidate plan exist in same organization.
    - Candidate Interview Plan MUST be in APPROVED lifecycle state.
    - No unresolved/manual-required question remains.
    - Snapshot exact ordered questions and rubrics.
    """
    # 1. Load job
    job = db.execute(
        select(JobRequisition)
        .options(selectinload(JobRequisition.versions))
        .where(JobRequisition.job_id == job_id)
    ).scalar_one_or_none()
    if not job:
        raise InterviewRequisitionNotFoundError(job_id=job_id)
    _verify_simulation_access(actor, job)

    # 2. Load Candidate Interview Plan with all relationships
    stmt_plan = (
        select(CandidateInterviewPlanRecord)
        .options(
            selectinload(CandidateInterviewPlanRecord.template),
            selectinload(CandidateInterviewPlanRecord.profile).selectinload(CandidateProfileRecord.identity),
            selectinload(CandidateInterviewPlanRecord.provenances).selectinload(SupplementalQuestionProvenanceRecord.question),
        )
        .where(
            CandidateInterviewPlanRecord.plan_id == plan_id,
            CandidateInterviewPlanRecord.template_id == template_id,
        )
    )
    plan = db.execute(stmt_plan).scalar_one_or_none()
    if not plan or plan.template.job_id != job_id:
        raise SimulationServiceError(
            code="PLAN_NOT_FOUND",
            message="Candidate interview plan not found for this requisition.",
            status_code=404,
        )

    # 3. Verify APPROVED lifecycle state
    if plan.lifecycle_status != PlanLifecycle.APPROVED:
        raise SimulationServiceError(
            code="PLAN_NOT_APPROVED",
            message=f"Cannot start simulation for plan in {plan.lifecycle_status.value} state. Only APPROVED plans can be simulated.",
            status_code=409,
        )

    job_title = _resolve_job_title(job)

    # 4. Check for existing active or submitted attempt
    stmt_existing = (
        select(SimulationAttemptRecord)
        .options(
            selectinload(SimulationAttemptRecord.answers),
            selectinload(SimulationAttemptRecord.assessments),
        )
        .where(
            SimulationAttemptRecord.plan_id == plan_id,
            SimulationAttemptRecord.recruiter_user_id == actor.user_id,
        )
        .order_by(SimulationAttemptRecord.created_at.desc())
    )

    existing_attempt = db.execute(stmt_existing).scalars().first()
    if existing_attempt:
        cand_name = _resolve_candidate_name(db, plan.candidate_id, plan.profile)
        return _serialize_attempt_response(existing_attempt, candidate_name=cand_name, job_title=job_title)

    # 5. Build question snapshots from plan and common core template
    # Load template questions
    stmt_tmpl = (
        select(JobInterviewTemplateRecord)
        .options(selectinload(JobInterviewTemplateRecord.questions).selectinload(JobInterviewTemplateRecord.questions.property.mapper.class_.rubric))
        .where(JobInterviewTemplateRecord.template_id == template_id)
    )
    template_rec = db.execute(stmt_tmpl).scalar_one_or_none()
    if not template_rec:
        raise SimulationServiceError(
            code="TEMPLATE_NOT_FOUND",
            message="Parent job interview template not found.",
            status_code=404,
        )

    # Gather all questions (common core + supplemental)
    all_q_records = []
    # Common core from template
    for q in sorted(template_rec.questions, key=lambda x: x.display_order):
        if q.layer == InterviewLayer.COMMON_CORE:
            all_q_records.append(q)

    # Supplemental from plan provenances
    if plan.provenances:
        for prov in sorted(plan.provenances, key=lambda p: p.question.display_order if p.question else 0):
            if prov.question:
                all_q_records.append(prov.question)

    # Check that no question is unresolved
    question_snapshots: List[Dict[str, Any]] = []
    for idx, q_rec in enumerate(all_q_records):
        meta = dict(q_rec.safe_metadata or {})
        if (
            meta.get("unresolved_manual_required")
            or not q_rec.question_text
            or q_rec.question_text.strip() == "[Manual question required]"
        ):
            raise SimulationServiceError(
                code="UNRESOLVED_QUESTION_IN_PLAN",
                message=f"Question at order {q_rec.display_order} requires manual entry before simulation can start.",
                status_code=422,
            )

        rubric_dict = None
        if hasattr(q_rec, "rubric") and q_rec.rubric:
            rubric_dict = {
                "rubric_id": str(q_rec.rubric.rubric_id),
                "rubric_version": q_rec.rubric.rubric_version,
                "competency_definition": q_rec.rubric.competency_definition,
                "question_intent": q_rec.rubric.question_intent,
                "observable_indicators": list(q_rec.rubric.observable_indicators or []),
                "rating_anchors": q_rec.rubric.rating_anchors,
                "sufficient_evidence_guidance": q_rec.rubric.sufficient_evidence_guidance,
                "insufficient_evidence_guidance": q_rec.rubric.insufficient_evidence_guidance,
                "permitted_probes": list(q_rec.rubric.permitted_probes or []),
            }

        question_snapshots.append({
            "question_id": str(q_rec.question_id),
            "question_version": q_rec.question_version,
            "display_order": idx + 1,
            "layer": q_rec.layer.value if hasattr(q_rec.layer, "value") else str(q_rec.layer),
            "question_type": q_rec.question_type.value if hasattr(q_rec.question_type, "value") else str(q_rec.question_type),
            "competency_name": q_rec.competency_id or "Core Competency",
            "question_text": q_rec.question_text,
            "estimated_time_minutes": q_rec.estimated_time_minutes,
            "permitted_probes": list(q_rec.permitted_probes or []),
            "rubric": rubric_dict,
        })

    # 6. Create SimulationAttemptRecord
    attempt = SimulationAttemptRecord(
        attempt_id=uuid.uuid4(),
        organization_id=plan.organization_id,
        recruiter_user_id=actor.user_id,
        candidate_id=plan.candidate_id,
        plan_id=plan.plan_id,
        template_id=template_rec.template_id,
        job_id=job.job_id,
        job_version_id=template_rec.job_version_id,
        job_revision=template_rec.revision,
        candidate_profile_version=plan.profile.profile_version if plan.profile else plan.profile_version,
        match_result_id=plan.match_result_id,
        plan_version=plan.plan_version,
        authoritative_content_hash=template_rec.content_hash or plan.input_snapshot_hash,
        lifecycle_status="IN_PROGRESS",
        guardrail_policy_version=SAFETY_POLICY_VERSION,
        guardrail_policy_hash=SAFETY_POLICY_HASH,
        question_snapshots=question_snapshots,
        total_questions_count=len(question_snapshots),
        answered_questions_count=0,
        started_at=datetime.now(timezone.utc),
        record_version=1,
    )
    db.add(attempt)
    db.flush()

    # 7. Initialize answer draft records for each question
    for q_snap in question_snapshots:
        ans_rec = SimulationAnswerRecord(
            answer_id=uuid.uuid4(),
            attempt_id=attempt.attempt_id,
            question_id=uuid.UUID(q_snap["question_id"]),
            question_version=q_snap["question_version"],
            display_order=q_snap["display_order"],
            layer=q_snap["layer"],
            answer_text="",
            answer_version=1,
            is_submitted=False,
            content_hash=hashlib.sha256("".encode("utf-8")).hexdigest(),
            safe_metadata={},
        )
        db.add(ans_rec)

    db.commit()
    db.refresh(attempt)

    # 8. Audit event
    Phase3AuditEvent(
        event_id=uuid.uuid4(),
        actor_user_id=actor.user_id,
        organization_id=attempt.organization_id,
        event_type="SIMULATION_ATTEMPT_STARTED",
        resource_type="simulation_attempt",
        resource_id=str(attempt.attempt_id),
        resource_version=attempt.record_version,
        policy_version=SAFETY_POLICY_VERSION,
        policy_hash=SAFETY_POLICY_HASH,
        retention_class=AuditRetentionClass.OPERATIONAL_TRACE,
        result_code="SUCCESS",
        safe_metadata={
            "plan_id": str(plan.plan_id),
            "template_id": str(template_rec.template_id),
            "candidate_id": str(plan.candidate_id),
            "total_questions": len(question_snapshots),
        },
    )
    db.commit()

    cand_name = _resolve_candidate_name(db, plan.candidate_id, plan.profile)
    return _serialize_attempt_response(attempt, candidate_name=cand_name, job_title=job_title)


def get_simulation_attempt(
    actor: UserRecord,
    job_id: UUID,
    template_id: UUID,
    plan_id: UUID,
    attempt_id: Optional[UUID],
    db: Session,
) -> SimulationAttemptResponse:
    """Retrieves an existing simulation attempt with answers."""
    job = db.execute(
        select(JobRequisition)
        .options(selectinload(JobRequisition.versions))
        .where(JobRequisition.job_id == job_id)
    ).scalar_one_or_none()
    if not job:
        raise InterviewRequisitionNotFoundError(job_id=job_id)
    _verify_simulation_access(actor, job)

    stmt = (
        select(SimulationAttemptRecord)
        .options(
            selectinload(SimulationAttemptRecord.answers),
            selectinload(SimulationAttemptRecord.assessments),
        )
        .where(
            SimulationAttemptRecord.plan_id == plan_id,
            SimulationAttemptRecord.job_id == job_id,
        )
    )

    if attempt_id:
        stmt = stmt.where(SimulationAttemptRecord.attempt_id == attempt_id)
    stmt = stmt.order_by(SimulationAttemptRecord.created_at.desc())

    attempt = db.execute(stmt).scalars().first()
    if not attempt:
        raise SimulationServiceError(
            code="SIMULATION_NOT_FOUND",
            message="No simulation attempt found for this candidate plan.",
            status_code=404,
        )

    cand = db.execute(
        select(CandidateProfileRecord)
        .options(selectinload(CandidateProfileRecord.identity))
        .where(CandidateProfileRecord.profile_id == attempt.candidate_id)
    ).scalar_one_or_none()
    cand_name = _resolve_candidate_name(db, attempt.candidate_id, cand)
    job_title = _resolve_job_title(job)

    return _serialize_attempt_response(attempt, candidate_name=cand_name, job_title=job_title)


def save_draft_simulation_answer(
    actor: UserRecord,
    job_id: UUID,
    template_id: UUID,
    plan_id: UUID,
    attempt_id: UUID,
    question_id: UUID,
    request: SimulationAnswerSaveRequest,
    db: Session,
) -> SimulationAnswerSaveResponse:
    """Saves one draft simulated answer using optimistic concurrency.
    
    Invariants:
    - Attempt must be IN_PROGRESS (SUBMITTED attempts are immutable).
    - NEVER invokes AI or logs answer text to general application logs.
    - Concurrency check against answer_version.
    """
    job = db.execute(select(JobRequisition).where(JobRequisition.job_id == job_id)).scalar_one_or_none()
    if not job:
        raise InterviewRequisitionNotFoundError(job_id=job_id)
    _verify_simulation_access(actor, job)

    stmt_att = (
        select(SimulationAttemptRecord)
        .options(selectinload(SimulationAttemptRecord.answers))
        .where(
            SimulationAttemptRecord.attempt_id == attempt_id,
            SimulationAttemptRecord.plan_id == plan_id,
            SimulationAttemptRecord.job_id == job_id,
        )
    )
    attempt = db.execute(stmt_att).scalar_one_or_none()
    if not attempt:
        raise SimulationServiceError(
            code="SIMULATION_NOT_FOUND",
            message="Simulation attempt not found.",
            status_code=404,
        )

    if attempt.lifecycle_status != "IN_PROGRESS":
        raise SimulationServiceError(
            code="ATTEMPT_IMMUTABLE",
            message="Cannot save answer on a submitted simulation attempt.",
            status_code=409,
        )

    # Find answer record
    answer = next((a for a in attempt.answers if a.question_id == question_id), None)
    if not answer:
        raise SimulationServiceError(
            code="QUESTION_NOT_IN_SIMULATION",
            message="Question not found in this simulation attempt.",
            status_code=404,
        )

    # Concurrency check
    if request.expected_answer_version != answer.answer_version:
        raise SimulationServiceError(
            code="STALE_ANSWER_VERSION",
            message=f"Answer version conflict: expected {request.expected_answer_version}, but current version is {answer.answer_version}.",
            status_code=409,
        )

    # Update answer
    answer.answer_text = request.answer_text
    answer.content_hash = hashlib.sha256(request.answer_text.encode("utf-8")).hexdigest()
    answer.answer_version += 1
    answer.updated_at = datetime.now(timezone.utc)

    # Recalculate answered questions count
    answered_count = sum(1 for a in attempt.answers if len(a.answer_text.strip()) > 0)
    attempt.answered_questions_count = answered_count
    attempt.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(answer)
    db.refresh(attempt)

    return SimulationAnswerSaveResponse(
        answer=_serialize_answer_item(answer),
        answered_questions_count=attempt.answered_questions_count,
        total_questions_count=attempt.total_questions_count,
        attempt_status=attempt.lifecycle_status,
    )


def submit_simulation_attempt(
    actor: UserRecord,
    job_id: UUID,
    template_id: UUID,
    plan_id: UUID,
    attempt_id: UUID,
    request: SimulationSubmitRequest,
    db: Session,
) -> SimulationSubmitResponse:
    """Finalizes and submits a simulation attempt, making all answers immutable."""
    job = db.execute(select(JobRequisition).where(JobRequisition.job_id == job_id)).scalar_one_or_none()
    if not job:
        raise InterviewRequisitionNotFoundError(job_id=job_id)
    _verify_simulation_access(actor, job)

    stmt_att = (
        select(SimulationAttemptRecord)
        .options(selectinload(SimulationAttemptRecord.answers))
        .where(
            SimulationAttemptRecord.attempt_id == attempt_id,
            SimulationAttemptRecord.plan_id == plan_id,
            SimulationAttemptRecord.job_id == job_id,
        )
    )
    attempt = db.execute(stmt_att).scalar_one_or_none()
    if not attempt:
        raise SimulationServiceError(
            code="SIMULATION_NOT_FOUND",
            message="Simulation attempt not found.",
            status_code=404,
        )

    if attempt.lifecycle_status == "SUBMITTED":
        # Idempotent response
        return SimulationSubmitResponse(
            attempt_id=attempt.attempt_id,
            lifecycle_status="SUBMITTED",
            submitted_at=attempt.submitted_at.isoformat() if attempt.submitted_at else datetime.now(timezone.utc).isoformat(),
            total_questions_count=attempt.total_questions_count,
            answered_questions_count=attempt.answered_questions_count,
            authoritative_content_hash=attempt.authoritative_content_hash,
            message="Simulation attempt is already submitted and sealed.",
        )

    # Concurrency check if expected_record_version provided
    if request.expected_record_version and request.expected_record_version != attempt.record_version:
        raise SimulationServiceError(
            code="STALE_ATTEMPT_VERSION",
            message=f"Attempt version mismatch: expected {request.expected_record_version}, but current version is {attempt.record_version}.",
            status_code=409,
        )

    # Mark all answers submitted
    for ans in attempt.answers:
        ans.is_submitted = True
        ans.updated_at = datetime.now(timezone.utc)

    attempt.lifecycle_status = "SUBMITTED"
    attempt.submitted_at = datetime.now(timezone.utc)
    attempt.record_version += 1
    attempt.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(attempt)

    # Audit event
    Phase3AuditEvent(
        event_id=uuid.uuid4(),
        actor_user_id=actor.user_id,
        organization_id=attempt.organization_id,
        event_type="SIMULATION_ATTEMPT_SUBMITTED",
        resource_type="simulation_attempt",
        resource_id=str(attempt.attempt_id),
        resource_version=attempt.record_version,
        policy_version=SAFETY_POLICY_VERSION,
        policy_hash=SAFETY_POLICY_HASH,
        retention_class=AuditRetentionClass.OPERATIONAL_TRACE,
        result_code="SUCCESS",
        safe_metadata={
            "attempt_id": str(attempt.attempt_id),
            "plan_id": str(attempt.plan_id),
            "answered_count": attempt.answered_questions_count,
            "total_questions": attempt.total_questions_count,
        },
    )
    db.commit()

    return SimulationSubmitResponse(
        attempt_id=attempt.attempt_id,
        lifecycle_status="SUBMITTED",
        submitted_at=attempt.submitted_at.isoformat(),
        total_questions_count=attempt.total_questions_count,
        answered_questions_count=attempt.answered_questions_count,
        authoritative_content_hash=attempt.authoritative_content_hash,
        message="Simulation attempt submitted and locked successfully.",
    )
