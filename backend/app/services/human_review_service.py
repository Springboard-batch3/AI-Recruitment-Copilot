import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import UserRecord, UserRole
from app.models.interview import (
    AssessmentReviewRecord,
    InterviewAssessmentRecord,
    InterviewQuestionRecord,
    JobInterviewTemplateRecord,
    SimulationAnswerRecord,
    SimulationAnswerSegmentRecord,
    SimulationAttemptRecord,
    SimulationQuestionAssessmentRecord,
    SimulationReviewAuditEventRecord,
)
from app.models.job import JobRequisition
from app.schemas.human_review import (
    AcceptReviewRequest,
    AssessmentReviewItemResponse,
    DeterministicAggregateResponse,
    EditReviewRequest,
    FinalizationEligibilityResponse,
    FinalizeAssessmentRequest,
    InterviewFinalizeResponse,
    MarkInsufficientRequest,
    OverrideReviewRequest,
    RejectReviewRequest,
    ReviewWorkspaceQuestionItem,
    ReviewWorkspaceResponse,
)
from app.schemas.phase3_audit import Phase3AuditEvent
from app.schemas.phase3_enums import AuditRetentionClass, InterviewLayer

logger = logging.getLogger(__name__)


class HumanReviewError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _verify_recruiter_access(actor: UserRecord, job: JobRequisition) -> None:
    if actor.role == UserRole.RECRUITER and job.created_by_user_id != actor.user_id:
        raise HumanReviewError("ACCESS_DENIED", "Recruiter does not own this job requisition.", status_code=403)


def record_review_command(
    db: Session,
    actor: UserRecord,
    job_id: UUID,
    template_id: UUID,
    plan_id: UUID,
    attempt_id: UUID,
    question_id: UUID,
    command: str,
    payload: Any,
) -> AssessmentReviewItemResponse:
    """Executes a command-specific human review operation on a question assessment."""
    # 1. Authoritative Job Check
    job = db.execute(select(JobRequisition).where(JobRequisition.job_id == job_id)).scalar_one_or_none()
    if not job:
        raise HumanReviewError("JOB_NOT_FOUND", "Job requisition not found.", status_code=404)
    _verify_recruiter_access(actor, job)

    # 2. Authoritative Attempt Check
    attempt = db.execute(
        select(SimulationAttemptRecord)
        .where(
            SimulationAttemptRecord.attempt_id == attempt_id,
            SimulationAttemptRecord.plan_id == plan_id,
        )
    ).scalar_one_or_none()
    if not attempt:
        raise HumanReviewError("SIMULATION_NOT_FOUND", "Simulation attempt not found.", status_code=404)

    # Immutable Finalization Check (Rule 10)
    if attempt.lifecycle_status == "FINALIZED":
        raise HumanReviewError("ASSESSMENT_ALREADY_FINALIZED", "Finalized assessment records cannot be modified in place.", status_code=409)

    existing_finalized = db.execute(
        select(InterviewAssessmentRecord).where(InterviewAssessmentRecord.attempt_id == attempt_id)
    ).scalar_one_or_none()
    if existing_finalized and existing_finalized.is_finalized:
        raise HumanReviewError("ASSESSMENT_ALREADY_FINALIZED", "Finalized assessment records cannot be modified in place.", status_code=409)

    # 3. Authoritative Question Assessment Check
    assessment = db.execute(
        select(SimulationQuestionAssessmentRecord)
        .where(
            SimulationQuestionAssessmentRecord.attempt_id == attempt_id,
            SimulationQuestionAssessmentRecord.question_id == question_id,
        )
    ).scalar_one_or_none()
    if not assessment:
        raise HumanReviewError(
            "PROVISIONAL_ASSESSMENT_NOT_FOUND",
            "Provisional assessment must exist before a human review can be recorded.",
            status_code=404,
        )

    # 4. Load segments allowlist for this question's answer
    segments = db.execute(
        select(SimulationAnswerSegmentRecord)
        .where(
            SimulationAnswerSegmentRecord.attempt_id == attempt_id,
            SimulationAnswerSegmentRecord.question_id == question_id,
        )
    ).scalars().all()
    valid_segment_ids = {str(s.segment_id) for s in segments}

    # 5. Optimistic concurrency check
    latest_review = db.execute(
        select(AssessmentReviewRecord)
        .where(AssessmentReviewRecord.assessment_id == assessment.assessment_id)
        .order_by(AssessmentReviewRecord.review_version.desc())
    ).scalars().first()

    expected_ver = getattr(payload, "expected_review_version", None)
    if expected_ver is not None:
        if latest_review and latest_review.review_version != expected_ver:
            raise HumanReviewError(
                "STALE_REVIEW_VERSION",
                f"Review version conflict. Expected {expected_ver} but current is {latest_review.review_version}.",
                status_code=409,
            )
        elif not latest_review and expected_ver != 1:
            raise HumanReviewError(
                "STALE_REVIEW_VERSION",
                f"Review version conflict. Expected {expected_ver} for initial review.",
                status_code=409,
            )

    next_version = (latest_review.review_version + 1) if latest_review else 1

    # 6. Command Dispatch
    decision: str
    reviewed_obs: Optional[str] = None
    reviewed_level: Optional[int] = None
    retained_cites: List[str] = []
    reason_text: Optional[str] = None

    if command == "accept":
        decision = "ACCEPTED"
        reviewed_level = assessment.provisional_level
        # Extract observations as plain text
        obs_texts = [
            str(o.get("observation") or o.get("observation_text") or "").strip()
            for o in (assessment.criterion_observations or [])
            if (o.get("observation") or o.get("observation_text"))
        ]
        reviewed_obs = "\n\n".join(obs_texts) if obs_texts else "Accepted provisional evaluation."
        retained_cites = list(assessment.cited_segment_ids or [])
        reason_text = None

    elif command == "edit":
        decision = "EDITED"
        req = payload if isinstance(payload, EditReviewRequest) else EditReviewRequest(**payload)
        
        # Rule 5: Edit requires at least one valid citation
        if not req.cited_segment_ids:
            raise HumanReviewError("CITATIONS_REQUIRED", "At least one valid answer segment citation is required for edited observations.", status_code=422)
        
        # Verify all cited segments belong to exact submitted answer
        for cid in req.cited_segment_ids:
            if cid not in valid_segment_ids:
                raise HumanReviewError(
                    "FORGED_OR_UNKNOWN_CITATION",
                    f"Cited segment '{cid}' is unknown or does not belong to the submitted answer for this question.",
                    status_code=422,
                )
        
        reviewed_obs = req.reviewed_observation.strip()
        retained_cites = list(req.cited_segment_ids)
        reviewed_level = latest_review.reviewed_rubric_level if (latest_review and latest_review.reviewed_rubric_level is not None) else assessment.provisional_level
        reason_text = req.reason.strip() if req.reason else None

    elif command == "override":
        decision = "OVERRIDDEN"
        req = payload if isinstance(payload, OverrideReviewRequest) else OverrideReviewRequest(**payload)
        if not req.reason or not req.reason.strip():
            raise HumanReviewError("REASON_REQUIRED", "Non-empty rationale reason is required to override rubric level.", status_code=422)
        
        reviewed_level = req.reviewed_rubric_level
        reason_text = req.reason.strip()
        reviewed_obs = latest_review.reviewed_observation if (latest_review and latest_review.reviewed_observation) else "Provisional score overridden by recruiter."
        retained_cites = latest_review.retained_citations if latest_review else list(assessment.cited_segment_ids or [])

    elif command == "reject":
        decision = "REJECTED"
        req = payload if isinstance(payload, RejectReviewRequest) else RejectReviewRequest(**payload)
        if not req.reason or not req.reason.strip():
            raise HumanReviewError("REASON_REQUIRED", "Non-empty justification reason is required to reject provisional assessment.", status_code=422)
        
        reviewed_level = None  # Neutral exclusion from scoring
        reviewed_obs = None    # Removed from human result
        retained_cites = []
        reason_text = req.reason.strip()

    elif command == "mark_insufficient":
        decision = "INSUFFICIENT"
        req = payload if isinstance(payload, MarkInsufficientRequest) else MarkInsufficientRequest(**(payload or {}))
        reviewed_level = None  # Neutral, not zero (Rule 8)
        reviewed_obs = "Insufficient evidence to assign a rubric score."
        retained_cites = []
        reason_text = req.reason.strip() if (req and req.reason) else "Insufficient answer evidence."

    else:
        raise HumanReviewError("INVALID_REVIEW_COMMAND", f"Unsupported review command: '{command}'.", status_code=400)

    # 7. Persist AssessmentReviewRecord
    new_review = AssessmentReviewRecord(
        review_id=uuid.uuid4(),
        assessment_id=assessment.assessment_id,
        attempt_id=attempt_id,
        question_id=question_id,
        reviewer_user_id=actor.user_id,
        review_version=next_version,
        decision=decision,
        reviewed_observation=reviewed_obs,
        reviewed_rubric_level=reviewed_level,
        retained_citations=retained_cites,
        reason=reason_text,
    )
    db.add(new_review)

    # 8. Record audit trail
    audit_rec = SimulationReviewAuditEventRecord(
        audit_id=uuid.uuid4(),
        attempt_id=attempt_id,
        question_id=question_id,
        actor_user_id=actor.user_id,
        event_type=f"REVIEW_{decision}",
        previous_state={
            "review_version": latest_review.review_version,
            "decision": latest_review.decision,
            "level": latest_review.reviewed_rubric_level,
        } if latest_review else None,
        new_state={
            "review_version": next_version,
            "decision": decision,
            "level": reviewed_level,
        },
        reason=reason_text,
        safe_metadata={
            "question_id": str(question_id),
            "decision": decision,
            "citations_count": len(retained_cites),
        },
    )
    db.add(audit_rec)

    # Emit Phase3AuditEvent
    Phase3AuditEvent(
        event_id=uuid.uuid4(),
        actor_user_id=actor.user_id,
        organization_id=getattr(actor, "org_id", getattr(actor, "organization_id", None)),
        event_type=f"HUMAN_REVIEW_{decision}",
        resource_type="simulation_question_assessment",
        resource_id=str(assessment.assessment_id),
        resource_version=next_version,
        retention_class=AuditRetentionClass.OPERATIONAL_TRACE,
        safe_metadata={
            "attempt_id": str(attempt_id),
            "question_id": str(question_id),
            "decision": decision,
        },
    )

    # 9. Update Attempt lifecycle status
    if attempt.lifecycle_status != "FINALIZED":
        attempt.lifecycle_status = "HUMAN_REVIEWED"

    db.commit()
    db.refresh(new_review)

    return AssessmentReviewItemResponse(
        review_id=new_review.review_id,
        assessment_id=new_review.assessment_id,
        attempt_id=new_review.attempt_id,
        question_id=new_review.question_id,
        reviewer_user_id=new_review.reviewer_user_id,
        review_version=new_review.review_version,
        decision=new_review.decision,
        reviewed_observation=new_review.reviewed_observation,
        reviewed_rubric_level=new_review.reviewed_rubric_level,
        retained_citations=new_review.retained_citations or [],
        reason=new_review.reason,
        created_at=new_review.created_at,
        updated_at=new_review.updated_at,
    )


def calculate_deterministic_aggregate(
    template_questions: List[Any],
    reviews_by_question: Dict[UUID, AssessmentReviewRecord],
) -> DeterministicAggregateResponse:
    """Calculates core aggregate score deterministically from reviewed common core questions."""
    included_items: List[Dict[str, Any]] = []
    excluded_items: List[Dict[str, Any]] = []
    total_core = 0
    numerator = 0.0
    denominator = 0.0

    for q in template_questions:
        q_id = q.question_id
        is_core = (
            getattr(q, "layer", None) == InterviewLayer.COMMON_CORE
            or getattr(q, "layer", None) == "COMMON_CORE"
        )
        if not is_core:
            excluded_items.append({
                "question_id": str(q_id),
                "competency": getattr(q, "competency_name", None) or getattr(q, "competency_id", None),
                "reason": "SUPPLEMENTAL_EVIDENCE_EXCLUDED",
            })
            continue

        total_core += 1
        review = reviews_by_question.get(q_id)
        if not review:
            excluded_items.append({
                "question_id": str(q_id),
                "competency": getattr(q, "competency_name", None) or getattr(q, "competency_id", None),
                "reason": "PENDING_REVIEW",
            })
            continue

        if review.decision in ("ACCEPTED", "EDITED", "OVERRIDDEN") and review.reviewed_rubric_level is not None:
            level = float(review.reviewed_rubric_level)
            weight = 1.0  # Approved baseline equal weighting
            numerator += level * weight
            denominator += weight
            included_items.append({
                "question_id": str(q_id),
                "competency": getattr(q, "competency_name", None) or getattr(q, "competency_id", None),
                "decision": review.decision,
                "reviewed_rubric_level": review.reviewed_rubric_level,
                "weight": weight,
            })
        elif review.decision == "INSUFFICIENT":
            excluded_items.append({
                "question_id": str(q_id),
                "competency": getattr(q, "competency_name", None) or getattr(q, "competency_id", None),
                "reason": "INSUFFICIENT_EVIDENCE_NEUTRAL",
            })
        elif review.decision == "REJECTED":
            excluded_items.append({
                "question_id": str(q_id),
                "competency": getattr(q, "competency_name", None) or getattr(q, "competency_id", None),
                "reason": "REJECTED_OBSERVATION_NEUTRAL",
            })

    aggregate_score = round(numerator / denominator, 2) if denominator > 0 else None

    # Deterministic policy hash verification
    hash_payload = f"1.0.0:{round(numerator, 4)}:{round(denominator, 4)}:{len(included_items)}:{len(excluded_items)}"
    policy_hash = hashlib.sha256(hash_payload.encode("utf-8")).hexdigest()

    return DeterministicAggregateResponse(
        policy_version="1.0.0",
        policy_hash=policy_hash,
        aggregate_score=aggregate_score,
        numerator=round(numerator, 4),
        denominator=round(denominator, 4),
        total_core_questions=total_core,
        scored_core_questions=len(included_items),
        included_items=included_items,
        excluded_items=excluded_items,
    )


def check_finalization_eligibility(
    template_questions: List[Any],
    reviews_by_question: Dict[UUID, AssessmentReviewRecord],
    attempt: SimulationAttemptRecord,
) -> FinalizationEligibilityResponse:
    """Computes finalization eligibility and reasons."""
    blocking_reasons: List[str] = []
    unreviewed: List[str] = []
    total_core = 0
    reviewed_core = 0

    if attempt.lifecycle_status == "FINALIZED":
        blocking_reasons.append("Interview assessment is already finalized and immutable.")

    for q in template_questions:
        q_id = q.question_id
        is_core = (
            getattr(q, "layer", None) == InterviewLayer.COMMON_CORE
            or getattr(q, "layer", None) == "COMMON_CORE"
        )
        if not is_core:
            continue
        total_core += 1
        review = reviews_by_question.get(q_id)
        if not review:
            unreviewed.append(str(q_id))
            blocking_reasons.append(f"Question {q_id} has not been reviewed by recruiter.")
        else:
            reviewed_core += 1
            if review.decision == "OVERRIDDEN" and not (review.reason and review.reason.strip()):
                blocking_reasons.append(f"Question {q_id} override is missing mandatory rationale reason.")
            if review.decision == "REJECTED" and not (review.reason and review.reason.strip()):
                blocking_reasons.append(f"Question {q_id} rejection is missing mandatory justification reason.")
            if review.decision == "EDITED" and not review.retained_citations:
                blocking_reasons.append(f"Question {q_id} edit is missing required evidence citations.")

    aggregate = calculate_deterministic_aggregate(template_questions, reviews_by_question)
    is_eligible = (len(blocking_reasons) == 0 and total_core > 0 and reviewed_core == total_core)

    return FinalizationEligibilityResponse(
        is_eligible=is_eligible,
        blocking_reasons=blocking_reasons,
        total_core_questions=total_core,
        reviewed_core_questions=reviewed_core,
        unreviewed_question_ids=unreviewed,
        aggregate_preview=aggregate,
    )


def finalize_simulation_assessment(
    db: Session,
    actor: UserRecord,
    job_id: UUID,
    template_id: UUID,
    plan_id: UUID,
    attempt_id: UUID,
    payload: FinalizeAssessmentRequest,
) -> InterviewFinalizeResponse:
    """Locks the interview simulation assessment immutably."""
    # 1. Verify Job and Access
    job = db.execute(select(JobRequisition).where(JobRequisition.job_id == job_id)).scalar_one_or_none()
    if not job:
        raise HumanReviewError("JOB_NOT_FOUND", "Job requisition not found.", status_code=404)
    _verify_recruiter_access(actor, job)

    # 2. Verify Template
    template = db.execute(
        select(JobInterviewTemplateRecord)
        .options(selectinload(JobInterviewTemplateRecord.questions))
        .where(JobInterviewTemplateRecord.template_id == template_id)
    ).scalar_one_or_none()
    if not template:
        raise HumanReviewError("TEMPLATE_NOT_FOUND", "Template not found.", status_code=404)

    # 3. Verify Attempt
    attempt = db.execute(
        select(SimulationAttemptRecord)
        .where(
            SimulationAttemptRecord.attempt_id == attempt_id,
            SimulationAttemptRecord.plan_id == plan_id,
        )
    ).scalar_one_or_none()
    if not attempt:
        raise HumanReviewError("SIMULATION_NOT_FOUND", "Simulation attempt not found.", status_code=404)

    if attempt.lifecycle_status == "FINALIZED":
        raise HumanReviewError("ASSESSMENT_ALREADY_FINALIZED", "Assessment is already finalized and cannot be modified.", status_code=409)

    if payload.expected_attempt_version is not None and attempt.record_version != payload.expected_attempt_version:
        raise HumanReviewError(
            "STALE_ATTEMPT_VERSION",
            f"Attempt version conflict. Expected {payload.expected_attempt_version} but current is {attempt.record_version}.",
            status_code=409,
        )

    # 4. Load all reviews for this attempt
    all_reviews = db.execute(
        select(AssessmentReviewRecord)
        .where(AssessmentReviewRecord.attempt_id == attempt_id)
        .order_by(AssessmentReviewRecord.review_version.asc())
    ).scalars().all()

    reviews_by_question: Dict[UUID, AssessmentReviewRecord] = {}
    for r in all_reviews:
        reviews_by_question[r.question_id] = r

    # 5. Check eligibility
    eligibility = check_finalization_eligibility(template.questions, reviews_by_question, attempt)
    if not eligibility.is_eligible:
        raise HumanReviewError(
            "FINALIZATION_INELIGIBLE",
            f"Finalization blocked: {'; '.join(eligibility.blocking_reasons)}",
            status_code=422,
        )

    # 6. Calculate deterministic aggregate
    aggregate = calculate_deterministic_aggregate(template.questions, reviews_by_question)

    # 7. Create immutable InterviewAssessmentRecord
    interview_assessment = InterviewAssessmentRecord(
        interview_assessment_id=uuid.uuid4(),
        attempt_id=attempt_id,
        organization_id=attempt.organization_id,
        finalized_by_user_id=actor.user_id,
        lifecycle_status="FINALIZED",
        aggregate_score=aggregate.aggregate_score,
        aggregate_summary=aggregate.model_dump(),
        policy_version=aggregate.policy_version,
        policy_hash=aggregate.policy_hash,
        is_finalized=True,
        finalized_at=datetime.now(timezone.utc),
    )
    db.add(interview_assessment)

    # 8. Transition Attempt lifecycle status
    attempt.lifecycle_status = "FINALIZED"
    attempt.record_version += 1

    # 9. Audit event
    audit_rec = SimulationReviewAuditEventRecord(
        audit_id=uuid.uuid4(),
        attempt_id=attempt_id,
        actor_user_id=actor.user_id,
        event_type="ASSESSMENT_FINALIZED",
        reason=payload.reason,
        safe_metadata={
            "aggregate_score": aggregate.aggregate_score,
            "policy_version": aggregate.policy_version,
            "total_core": aggregate.total_core_questions,
            "scored_core": aggregate.scored_core_questions,
        },
    )
    db.add(audit_rec)

    Phase3AuditEvent(
        event_id=uuid.uuid4(),
        actor_user_id=actor.user_id,
        organization_id=attempt.organization_id,
        event_type="ASSESSMENT_FINALIZED",
        resource_type="simulation_interview_assessment",
        resource_id=str(interview_assessment.interview_assessment_id),
        resource_version=attempt.record_version,
        retention_class=AuditRetentionClass.OPERATIONAL_TRACE,
        safe_metadata={
            "attempt_id": str(attempt_id),
            "aggregate_score": aggregate.aggregate_score,
        },
    )

    db.commit()
    db.refresh(interview_assessment)

    return InterviewFinalizeResponse(
        interview_assessment_id=interview_assessment.interview_assessment_id,
        attempt_id=attempt_id,
        lifecycle_status="FINALIZED",
        is_finalized=True,
        finalized_at=interview_assessment.finalized_at,
        finalized_by_user_id=actor.user_id,
        aggregate=aggregate,
        safe_message="Interview assessment finalized successfully. Record is immutable.",
    )


def get_review_workspace_data(
    db: Session,
    actor: UserRecord,
    job_id: UUID,
    template_id: UUID,
    plan_id: UUID,
    attempt_id: UUID,
) -> ReviewWorkspaceResponse:
    """Loads all data needed for the recruiter review workspace."""
    # 1. Job Requisition
    job = db.execute(select(JobRequisition).where(JobRequisition.job_id == job_id)).scalar_one_or_none()
    if not job:
        raise HumanReviewError("JOB_NOT_FOUND", "Job requisition not found.", status_code=404)
    _verify_recruiter_access(actor, job)

    # 2. Template
    template = db.execute(
        select(JobInterviewTemplateRecord)
        .options(
            selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric)
        )
        .where(JobInterviewTemplateRecord.template_id == template_id)
    ).scalar_one_or_none()
    if not template:
        raise HumanReviewError("TEMPLATE_NOT_FOUND", "Template not found.", status_code=404)

    # 3. Attempt
    attempt = db.execute(
        select(SimulationAttemptRecord)
        .options(
            selectinload(SimulationAttemptRecord.answers).selectinload(SimulationAnswerRecord.segments),
            selectinload(SimulationAttemptRecord.assessments),
            selectinload(SimulationAttemptRecord.reviews),
            selectinload(SimulationAttemptRecord.interview_assessment),
        )
        .where(
            SimulationAttemptRecord.attempt_id == attempt_id,
            SimulationAttemptRecord.plan_id == plan_id,
        )
    ).scalar_one_or_none()
    if not attempt:
        raise HumanReviewError("SIMULATION_NOT_FOUND", "Simulation attempt not found.", status_code=404)

    # Map answers by question_id
    answers_by_q = {a.question_id: a for a in (attempt.answers or [])}
    assessments_by_q = {a.question_id: a for a in (attempt.assessments or [])}

    # Map latest review by question_id
    reviews_by_q: Dict[UUID, AssessmentReviewRecord] = {}
    for r in sorted(attempt.reviews or [], key=lambda x: x.review_version):
        reviews_by_q[r.question_id] = r

    # Build question items
    sorted_questions = sorted(template.questions or [], key=lambda x: x.display_order if x.display_order is not None else 0)
    items: List[ReviewWorkspaceQuestionItem] = []

    for q in sorted_questions:
        ans = answers_by_q.get(q.question_id)
        ass = assessments_by_q.get(q.question_id)
        rev = reviews_by_q.get(q.question_id)

        segments_data = [
            {
                "segment_id": str(s.segment_id),
                "sequence_order": s.sequence_order,
                "start_char": s.start_char,
                "end_char": s.end_char,
                "segment_text": s.segment_text,
            }
            for s in (ans.segments if ans else [])
        ]

        rubric_data = None
        if q.rubric:
            rubric_data = {
                "rubric_id": str(q.rubric.rubric_id),
                "rubric_version": q.rubric.rubric_version,
                "competency_definition": q.rubric.competency_definition,
                "rating_anchors": {str(k): str(v) for k, v in (q.rubric.rating_anchors or {}).items()},
                "observable_indicators": list(q.rubric.observable_indicators or []),
                "permitted_probes": list(q.rubric.permitted_probes or []),
            }

        provisional_data = None
        if ass:
            provisional_data = {
                "assessment_id": str(ass.assessment_id),
                "assessment_status": ass.assessment_status,
                "provisional_level": ass.provisional_level,
                "insufficient_evidence_reason": ass.insufficient_evidence_reason,
                "criterion_observations": ass.criterion_observations or [],
                "cited_segment_ids": ass.cited_segment_ids or [],
                "uncertainty_codes": ass.uncertainty_codes or [],
                "model_name": (ass.model_invocation_metadata or {}).get("model_name", "local-mlx-qwen3-4b"),
                "provider_class": (ass.model_invocation_metadata or {}).get("provider_class", "LOCAL"),
            }

        review_dto = None
        if rev:
            review_dto = AssessmentReviewItemResponse(
                review_id=rev.review_id,
                assessment_id=rev.assessment_id,
                attempt_id=rev.attempt_id,
                question_id=rev.question_id,
                reviewer_user_id=rev.reviewer_user_id,
                review_version=rev.review_version,
                decision=rev.decision,
                reviewed_observation=rev.reviewed_observation,
                reviewed_rubric_level=rev.reviewed_rubric_level,
                retained_citations=rev.retained_citations or [],
                reason=rev.reason,
                created_at=rev.created_at,
                updated_at=rev.updated_at,
            )

        items.append(
            ReviewWorkspaceQuestionItem(
                question_id=q.question_id,
                question_version=q.question_version,
                display_order=q.display_order,
                layer=q.layer.value if hasattr(q.layer, "value") else str(q.layer),
                question_type=q.question_type.value if hasattr(q.question_type, "value") else str(q.question_type),
                competency_name=q.competency_id or "Core Competency",
                question_text=q.question_text,
                submitted_answer=ans.answer_text if ans else None,
                answer_segments=segments_data,
                rubric=rubric_data,
                provisional=provisional_data,
                human_review=review_dto,
            )
        )

    eligibility = check_finalization_eligibility(sorted_questions, reviews_by_q, attempt)

    finalized_dto = None
    if attempt.interview_assessment and attempt.interview_assessment.is_finalized:
        ia = attempt.interview_assessment
        finalized_dto = InterviewFinalizeResponse(
            interview_assessment_id=ia.interview_assessment_id,
            attempt_id=attempt.attempt_id,
            lifecycle_status=ia.lifecycle_status,
            is_finalized=True,
            finalized_at=ia.finalized_at,
            finalized_by_user_id=ia.finalized_by_user_id,
            aggregate=DeterministicAggregateResponse(**(ia.aggregate_summary or {})),
            safe_message="Finalized interview assessment loaded.",
        )

    return ReviewWorkspaceResponse(
        attempt_id=attempt.attempt_id,
        lifecycle_status=attempt.lifecycle_status,
        candidate_id=attempt.candidate_id,
        candidate_name=None,
        job_id=job.job_id,
        job_title=job.title,
        questions=items,
        eligibility=eligibility,
        finalized_assessment=finalized_dto,
    )
