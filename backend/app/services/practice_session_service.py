"""Domain service orchestrating Candidate Practice session state machine, snapshotting, and executions."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import UserRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateBindingStatus,
    CandidatePracticeAnswerReviewRecord,
    CandidatePracticeAnswerSegmentRecord,
    CandidatePracticeMcqAnswerRecord,
    CandidatePracticeMessageRecord,
    CandidatePracticeReleaseRecord,
    CandidatePracticeReportRecord,
    CandidatePracticeSessionRecord,
)
from app.models.interview import AiInterviewQuestionSetRecord
from app.models.job import JobRequirement, JobRequisition, JobVersion
from app.schemas.mcq_contract import McqOptionDto
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    PracticeMessageKind,
    PracticeMessageRole,
    PracticeReleaseStatus,
    PracticeSection,
    PracticeSessionLifecycle,
    QuestionFormat,
)
from app.schemas.practice_review_contract import (
    AnswerStatus,
    AnswerStrengthItem,
    ConversationalReviewPayload,
    DimensionLevel,
    FollowUpItem,
    ImprovementAreaItem,
    McqReviewPayload,
    PracticeAnswerReviewDto,
    ReviewLifecycleStatus,
)
from app.schemas.practice_session_contract import (
    PracticeActiveQuestionDto,
    PracticeAdvanceQuestionRequest,
    PracticeConversationAnswerRequest,
    PracticeMcqAnswerRequest,
    PracticeMessageDto,
    PracticeQuestionRetryRequest,
    PracticeReviewRetryRequest,
    PracticeSessionAbandonResponse,
    PracticeSessionDetailResponse,
    PracticeSessionStartRequest,
)
from app.schemas.live_interview_contract import LEVEL_TO_NUMERIC
from app.services.live_interview_service import (
    construct_canonical_transcript,
    generate_final_interview_review_with_repair,
    generate_live_question_with_repair,
)
from app.services.practice_review_service import (
    create_insufficient_response_review,
    extract_answer_segments,
    generate_conversational_review_with_repair,
    is_deterministic_insufficient_response,
)

logger = logging.getLogger("ai_hiring.services.practice_session")


def _get_active_binding(candidate_user: UserRecord, db: Session) -> CandidateAccountBindingRecord:
    stmt = select(CandidateAccountBindingRecord).where(
        CandidateAccountBindingRecord.user_id == candidate_user.user_id,
        CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
    )
    binding = db.execute(stmt).scalar_one_or_none()
    if not binding:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "CANDIDATE_ACCESS_SUSPENDED", "message": "Active candidate binding not found."},
        )
    return binding


def _compute_snapshot_hash(snapshot: List[Dict[str, Any]]) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _map_review_to_dto(review: CandidatePracticeAnswerReviewRecord) -> PracticeAnswerReviewDto:
    strengths_items = [
        AnswerStrengthItem(
            dimension=st.get("dimension", "General"),
            observation=st.get("observation", ""),
            answer_segment_ids=st.get("answer_segment_ids", []),
        )
        for st in (review.strengths or [])
    ]
    improvements_items = [
        ImprovementAreaItem(
            dimension=imp.get("dimension", "General"),
            observation=imp.get("observation", ""),
            answer_segment_ids=imp.get("answer_segment_ids", []),
        )
        for imp in (review.improvement_areas or [])
    ]

    return PracticeAnswerReviewDto(
        review_id=review.review_id,
        session_id=review.session_id,
        question_id=review.question_id,
        answer_id=review.answer_id,
        schema_version=review.schema_version,
        lifecycle_status=review.lifecycle_status,
        answer_status=review.answer_status,
        acknowledgement=review.acknowledgement,
        strengths=strengths_items,
        improvement_areas=improvements_items,
        next_steps=review.next_steps or [],
        stronger_structure=review.stronger_structure,
        dimension_levels=review.dimension_levels or {},
        follow_up_needed=review.follow_up_needed,
        follow_up_question=review.follow_up_question,
        follow_up=FollowUpItem(needed=review.follow_up_needed, question=review.follow_up_question) if review.follow_up_needed else None,
        uncertainty_codes=review.uncertainty_codes or [],
        model_identifier=review.model_identifier,
        provider_class=review.provider_class,
        retry_count=review.retry_count or 0,
        created_at=review.created_at,
        completed_at=review.completed_at,
    )


def _map_session_to_dto(session: CandidatePracticeSessionRecord, db: Optional[Session] = None) -> PracticeSessionDetailResponse:
    now = datetime.now(timezone.utc)
    
    # Auto-expire if in progress but past expiry
    active_lifecycles = {
        PracticeSessionLifecycle.IN_PROGRESS.value,
        PracticeSessionLifecycle.QUESTION_GENERATING.value,
        PracticeSessionLifecycle.AWAITING_ANSWER.value,
        PracticeSessionLifecycle.ANSWER_SAVING.value,
        PracticeSessionLifecycle.FINAL_ANSWER_SAVING.value,
        PracticeSessionLifecycle.FINAL_REVIEW_GENERATING.value,
        PracticeSessionLifecycle.QUESTION_GENERATION_FAILED_RETRYABLE.value,
        PracticeSessionLifecycle.FINAL_REVIEW_FAILED_RETRYABLE.value,
    }
    if session.lifecycle_status in active_lifecycles and session.expires_at:
        exp = session.expires_at if session.expires_at.tzinfo else session.expires_at.replace(tzinfo=timezone.utc)
        if now > exp:
            session.lifecycle_status = PracticeSessionLifecycle.EXPIRED.value
            session.expired_at = now

    # Derive active question
    active_q: Optional[PracticeActiveQuestionDto] = None
    snapshot = session.questions_snapshot or []
    conv_questions = [q for q in snapshot if q.get("question_format") == QuestionFormat.CONVERSATION.value]
    mcq_questions = [q for q in snapshot if q.get("question_format") == QuestionFormat.MCQ_SINGLE_ANSWER.value]

    if session.lifecycle_status in (
        PracticeSessionLifecycle.IN_PROGRESS.value,
        PracticeSessionLifecycle.AWAITING_ANSWER.value,
        PracticeSessionLifecycle.QUESTION_GENERATING.value,
        PracticeSessionLifecycle.ANSWER_SAVING.value,
        PracticeSessionLifecycle.QUESTION_GENERATION_FAILED_RETRYABLE.value,
    ):
        if session.current_section == PracticeSection.CONVERSATION.value:
            if 0 <= session.current_conv_index < len(conv_questions):
                raw_q = conv_questions[session.current_conv_index]
                # Only expose question wording if not in initial ungenerated state
                active_q = PracticeActiveQuestionDto(
                    question_id=UUID(raw_q["question_id"]),
                    question_format=QuestionFormat.CONVERSATION.value,
                    wording=raw_q.get("wording", ""),
                    competency_name=raw_q.get("competency_name", "Core Competency"),
                    options=None,
                    difficulty=raw_q.get("difficulty"),
                    estimated_time_minutes=raw_q.get("estimated_time_minutes", 3),
                    current_position=session.current_conv_index + 1,
                    total_in_section=len(conv_questions),
                )
        elif session.current_section == PracticeSection.MCQ.value:
            if 0 <= session.current_mcq_index < len(mcq_questions):
                raw_q = mcq_questions[session.current_mcq_index]
                safe_options = [
                    McqOptionDto(option_id=opt["option_id"], text=opt["text"])
                    for opt in raw_q.get("options", [])
                ]
                active_q = PracticeActiveQuestionDto(
                    question_id=UUID(raw_q["question_id"]),
                    question_format=QuestionFormat.MCQ_SINGLE_ANSWER.value,
                    wording=raw_q["wording"],
                    competency_name=raw_q["competency_name"],
                    options=safe_options,
                    difficulty=raw_q.get("difficulty"),
                    estimated_time_minutes=raw_q.get("estimated_time_minutes", 2),
                    current_position=session.current_mcq_index + 1,
                    total_in_section=len(mcq_questions),
                )

    # Format safe message transcript
    messages_dto: List[PracticeMessageDto] = []
    if db:
        stmt_msgs = (
            select(CandidatePracticeMessageRecord)
            .where(CandidatePracticeMessageRecord.session_id == session.session_id)
            .order_by(CandidatePracticeMessageRecord.sequence_number.asc(), CandidatePracticeMessageRecord.created_at.asc())
        )
        msgs = db.execute(stmt_msgs).scalars().all()
    else:
        msgs = list(session.messages or [])
        msgs.sort(key=lambda m: (m.sequence_number or 0, m.created_at if m.created_at else now))

    for msg in msgs:
        messages_dto.append(
            PracticeMessageDto(
                message_id=msg.message_id,
                sequence_number=msg.sequence_number,
                role=msg.role,
                message_kind=msg.message_kind,
                source_question_id=msg.source_question_id,
                text=msg.text,
                created_at=msg.created_at,
            )
        )

    job_title = session.job_version.title if session.job_version else (session.job.title if session.job else "Practice Job")

    return PracticeSessionDetailResponse(
        session_id=session.session_id,
        release_id=session.release_id,
        job_id=session.job_id,
        job_title=job_title,
        job_revision=session.job_revision,
        lifecycle_status=session.lifecycle_status,
        current_section=session.current_section,
        current_conv_index=session.current_conv_index,
        total_conv_count=session.total_conv_count,
        current_mcq_index=session.current_mcq_index,
        total_mcq_count=session.total_mcq_count,
        total_response_count=session.total_response_count,
        active_question=active_q,
        messages=messages_dto,
        latest_review=None,  # No in-session per-answer reviews for live interview
        latest_mcq_review=None,
        fallback_active=session.fallback_active,
        fallback_notice=None,
        started_at=session.started_at,
        completed_at=session.completed_at,
        expires_at=session.expires_at,
    )


async def start_or_resume_practice_session(
    candidate_user: UserRecord,
    release_id: UUID,
    db: Session,
    custom_endpoint_url: Optional[str] = None,
) -> PracticeSessionDetailResponse:
    """Starts a new live AI interview session or resumes an existing active session."""
    binding = _get_active_binding(candidate_user, db)
    now = datetime.now(timezone.utc)

    # 1. Load release
    stmt_rel = (
        select(CandidatePracticeReleaseRecord)
        .options(
            selectinload(CandidatePracticeReleaseRecord.job),
            selectinload(CandidatePracticeReleaseRecord.job_version),
            selectinload(CandidatePracticeReleaseRecord.ai_set),
        )
        .where(
            CandidatePracticeReleaseRecord.release_id == release_id,
            CandidatePracticeReleaseRecord.candidate_id == binding.candidate_id,
            CandidatePracticeReleaseRecord.organization_id == binding.organization_id,
        )
    )
    release = db.execute(stmt_rel).scalar_one_or_none()
    if not release:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RELEASE_NOT_FOUND", "message": "Practice assignment not found for candidate."},
        )

    if release.status != PracticeReleaseStatus.RELEASED.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "RELEASE_REVOKED", "message": "This practice assignment has been revoked."},
        )

    # 2. Check for existing active session for this release
    active_statuses = [
        PracticeSessionLifecycle.IN_PROGRESS.value,
        PracticeSessionLifecycle.QUESTION_GENERATING.value,
        PracticeSessionLifecycle.AWAITING_ANSWER.value,
        PracticeSessionLifecycle.ANSWER_SAVING.value,
        PracticeSessionLifecycle.FINAL_ANSWER_SAVING.value,
        PracticeSessionLifecycle.FINAL_REVIEW_GENERATING.value,
        PracticeSessionLifecycle.QUESTION_GENERATION_FAILED_RETRYABLE.value,
        PracticeSessionLifecycle.FINAL_REVIEW_FAILED_RETRYABLE.value,
    ]
    stmt_existing = (
        select(CandidatePracticeSessionRecord)
        .options(
            selectinload(CandidatePracticeSessionRecord.messages),
            selectinload(CandidatePracticeSessionRecord.job),
            selectinload(CandidatePracticeSessionRecord.job_version),
        )
        .where(
            CandidatePracticeSessionRecord.release_id == release.release_id,
            CandidatePracticeSessionRecord.candidate_id == binding.candidate_id,
            CandidatePracticeSessionRecord.lifecycle_status.in_(active_statuses),
        )
        .order_by(desc(CandidatePracticeSessionRecord.started_at))
    )
    existing_session = db.execute(stmt_existing).scalars().first()
    if existing_session:
        exp = existing_session.expires_at if existing_session.expires_at.tzinfo else existing_session.expires_at.replace(tzinfo=timezone.utc)
        if now <= exp:
            # If session was left in QUESTION_GENERATING before Question 1 was generated (e.g. from prior db error), complete Question 1 generation now
            if existing_session.lifecycle_status == PracticeSessionLifecycle.QUESTION_GENERATING.value and existing_session.current_conv_index == 0:
                conv_plan = existing_session.questions_snapshot or []
                if conv_plan and not conv_plan[0].get("wording"):
                    target_comp_1 = conv_plan[0].get("competency_name") or "General"
                    q_type_1 = conv_plan[0].get("question_type", "TECHNICAL_SCENARIO")
                    q_1_id = UUID(conv_plan[0]["question_id"])
                    
                    # Fetch reqs
                    job_version = existing_session.job_version
                    job_requirements = []
                    if job_version:
                        stmt_reqs = select(JobRequirement).where(JobRequirement.job_version_id == job_version.job_version_id).order_by(JobRequirement.display_order.asc())
                        job_requirements = db.execute(stmt_reqs).scalars().all()
                    req_dicts = [
                        {"id": str(r.requirement_id), "requirement_id": str(r.requirement_id), "title": r.label, "competency_name": r.label, "summary": r.rationale or r.label}
                        for r in job_requirements
                    ]
                    if not req_dicts:
                        req_dicts = [{"id": "req-1", "title": target_comp_1, "competency_name": target_comp_1, "summary": "Technical competence"}]

                    gen_ok, q_payload, prov_class, model_id, err_msg = await generate_live_question_with_repair(
                        session=existing_session,
                        target_competency=target_comp_1,
                        desired_question_type=q_type_1,
                        question_index=1,
                        total_count=existing_session.total_conv_count,
                        requirements=req_dicts,
                        covered_competencies=[],
                        previous_questions=[],
                        previous_transcript=[],
                        db=db,
                        custom_endpoint_url=custom_endpoint_url,
                    )
                    if gen_ok and q_payload:
                        q_1_msg = CandidatePracticeMessageRecord(
                            message_id=uuid4(),
                            session_id=existing_session.session_id,
                            sequence_number=len(existing_session.messages) + 1,
                            role=PracticeMessageRole.INTERVIEWER.value,
                            message_kind=PracticeMessageKind.PRIMARY_QUESTION.value,
                            source_question_id=q_1_id,
                            text=q_payload.question,
                            model_invocation_ref=f"{prov_class}:{model_id}",
                            created_at=datetime.now(timezone.utc),
                        )
                        db.add(q_1_msg)
                        conv_plan[0]["wording"] = q_payload.question
                        existing_session.questions_snapshot = conv_plan
                        existing_session.lifecycle_status = PracticeSessionLifecycle.AWAITING_ANSWER.value
                        existing_session.model_call_count += 1
                    else:
                        existing_session.lifecycle_status = PracticeSessionLifecycle.QUESTION_GENERATION_FAILED_RETRYABLE.value
                    db.commit()
            return _map_session_to_dto(existing_session, db=db)
        else:
            existing_session.lifecycle_status = PracticeSessionLifecycle.EXPIRED.value
            existing_session.expired_at = now
            db.commit()

    # 3. Check 24h attempt rate limit (max 3 attempts per 24 hours per job)
    window_start = now - timedelta(hours=24)
    stmt_attempts = (
        select(func.count(CandidatePracticeSessionRecord.session_id))
        .where(
            CandidatePracticeSessionRecord.candidate_id == binding.candidate_id,
            CandidatePracticeSessionRecord.job_id == release.job_id,
            CandidatePracticeSessionRecord.started_at >= window_start,
        )
    )
    attempt_count = db.execute(stmt_attempts).scalar() or 0
    if attempt_count >= 3:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "ATTEMPT_LIMIT_REACHED",
                "message": "Maximum 3 practice attempts permitted within a 24-hour window for this job.",
            },
        )

    # 4. Extract coverage plan from approved job requirements & competencies
    job_version = release.job_version
    job_requirements: List[JobRequirement] = []
    if job_version:
        stmt_reqs = select(JobRequirement).where(JobRequirement.job_version_id == job_version.job_version_id).order_by(JobRequirement.display_order.asc())
        job_requirements = db.execute(stmt_reqs).scalars().all()

    req_dicts: List[Dict[str, Any]] = [
        {"id": str(r.requirement_id), "requirement_id": str(r.requirement_id), "title": r.label, "competency_name": r.label, "summary": r.rationale or r.label}
        for r in job_requirements
    ]

    # If no requirements, fallback to safe competency anchors
    if not req_dicts:
        req_dicts = [
            {"id": "req-1", "requirement_id": "req-1", "title": "Core Technical Skills", "competency_name": "Core Technical Skills", "summary": "Technical problem solving and core engineering competence"},
            {"id": "req-2", "requirement_id": "req-2", "title": "System Architecture", "competency_name": "System Architecture", "summary": "Design, scaling, reliability, and database validation"},
            {"id": "req-3", "requirement_id": "req-3", "title": "Collaboration & Delivery", "competency_name": "Collaboration & Delivery", "summary": "Team collaboration, communication, and project execution"},
        ]

    # Safe deterministic internal coverage plan for 6 questions (or number of requirements)
    total_conv_count = min(6, max(3, len(req_dicts)))
    coverage_plan: List[Dict[str, Any]] = []
    for i in range(total_conv_count):
        req_idx = i % len(req_dicts)
        target_req = req_dicts[req_idx]
        coverage_plan.append({
            "question_id": str(uuid4()),
            "display_order": i,
            "question_format": QuestionFormat.CONVERSATION.value,
            "wording": "",  # Model will author complete visible text
            "competency_name": target_req["competency_name"],
            "target_competency": target_req["competency_name"],
            "question_type": "TECHNICAL_SCENARIO" if i % 2 == 0 else "BEHAVIORAL",
            "source_requirement_ids": [target_req["id"]],
            "difficulty": "INTERMEDIATE",
            "estimated_time_minutes": 3,
        })

    snapshot_hash = _compute_snapshot_hash(coverage_plan)
    job_title = release.job_version.title if release.job_version else (release.job.title if release.job else "Role")

    new_session = CandidatePracticeSessionRecord(
        session_id=uuid4(),
        organization_id=binding.organization_id,
        binding_id=binding.binding_id,
        candidate_id=binding.candidate_id,
        release_id=release.release_id,
        application_id=release.application_id,
        job_id=release.job_id,
        job_version_id=release.job_version_id,
        job_revision=release.job_revision,
        ai_set_id=release.ai_set_id,
        set_version=release.set_version,
        snapshot_hash=snapshot_hash,
        questions_snapshot=coverage_plan,
        lifecycle_status=PracticeSessionLifecycle.QUESTION_GENERATING.value,
        current_section=PracticeSection.CONVERSATION.value,
        current_conv_index=0,
        current_conv_turn_count=0,
        current_mcq_index=0,
        total_conv_count=total_conv_count,
        total_mcq_count=0,
        total_response_count=0,
        model_call_count=0,
        concurrency_version=1,
        started_at=now,
        expires_at=now + timedelta(hours=24),
        retention_class="SIMULATION_CONTENT",
        fallback_active=False,
    )
    db.add(new_session)
    db.flush()

    # 5. Populate Initial Greeting
    greeting_msg = CandidatePracticeMessageRecord(
        message_id=uuid4(),
        session_id=new_session.session_id,
        sequence_number=1,
        role=PracticeMessageRole.INTERVIEWER.value,
        message_kind=PracticeMessageKind.GREETING.value,
        source_question_id=None,
        text=f"Welcome to your live practice interview for the {job_title} role. Your AI practice interviewer will generate questions tailored to this role. Please answer each question thoroughly in your own words when you are ready.",
        created_at=now,
    )
    db.add(greeting_msg)
    db.commit()

    # 6. Call Local LM Studio to generate Question 1
    target_comp_1 = coverage_plan[0]["competency_name"]
    q_type_1 = coverage_plan[0]["question_type"]
    q_1_id = UUID(coverage_plan[0]["question_id"])

    gen_ok, q_payload, prov_class, model_id, err_msg = await generate_live_question_with_repair(
        session=new_session,
        target_competency=target_comp_1,
        desired_question_type=q_type_1,
        question_index=1,
        total_count=total_conv_count,
        requirements=req_dicts,
        covered_competencies=[],
        previous_questions=[],
        previous_transcript=[],
        db=db,
        custom_endpoint_url=custom_endpoint_url,
    )

    if gen_ok and q_payload:
        # Persist Question 1
        q_1_msg = CandidatePracticeMessageRecord(
            message_id=uuid4(),
            session_id=new_session.session_id,
            sequence_number=2,
            role=PracticeMessageRole.INTERVIEWER.value,
            message_kind=PracticeMessageKind.PRIMARY_QUESTION.value,
            source_question_id=q_1_id,
            text=q_payload.question,
            model_invocation_ref=f"{prov_class}:{model_id}",
            created_at=datetime.now(timezone.utc),
        )
        db.add(q_1_msg)
        # Update snapshot with persisted wording
        coverage_plan[0]["wording"] = q_payload.question
        new_session.questions_snapshot = coverage_plan
        new_session.lifecycle_status = PracticeSessionLifecycle.AWAITING_ANSWER.value
        new_session.model_call_count += 1
    else:
        # Question generation failed retryable
        logger.warning(f"Live question 1 generation failed: {err_msg}")
        new_session.lifecycle_status = PracticeSessionLifecycle.QUESTION_GENERATION_FAILED_RETRYABLE.value

    db.commit()

    # Re-fetch session with relationships
    stmt_full = (
        select(CandidatePracticeSessionRecord)
        .options(
            selectinload(CandidatePracticeSessionRecord.messages),
            selectinload(CandidatePracticeSessionRecord.job),
            selectinload(CandidatePracticeSessionRecord.job_version),
        )
        .where(CandidatePracticeSessionRecord.session_id == new_session.session_id)
    )
    full_session = db.execute(stmt_full).scalar_one()
    return _map_session_to_dto(full_session, db=db)



def get_practice_session_state(
    candidate_user: UserRecord,
    session_id: UUID,
    db: Session,
) -> PracticeSessionDetailResponse:
    """Fetches candidate practice session state."""
    binding = _get_active_binding(candidate_user, db)

    stmt = (
        select(CandidatePracticeSessionRecord)
        .options(
            selectinload(CandidatePracticeSessionRecord.messages),
            selectinload(CandidatePracticeSessionRecord.job),
            selectinload(CandidatePracticeSessionRecord.job_version),
        )
        .where(
            CandidatePracticeSessionRecord.session_id == session_id,
            CandidatePracticeSessionRecord.candidate_id == binding.candidate_id,
        )
    )
    session = db.execute(stmt).scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Practice session not found."},
        )

    return _map_session_to_dto(session, db=db)


async def submit_conversational_answer(
    candidate_user: UserRecord,
    session_id: UUID,
    payload: PracticeConversationAnswerRequest,
    db: Session,
    custom_endpoint_url: Optional[str] = None,
) -> PracticeSessionDetailResponse:
    """Submits candidate answer, commits it immediately, and generates the next AI question or triggers final review."""
    binding = _get_active_binding(candidate_user, db)
    now = datetime.now(timezone.utc)

    # 1. Load session under lock
    stmt = (
        select(CandidatePracticeSessionRecord)
        .options(
            selectinload(CandidatePracticeSessionRecord.messages),
            selectinload(CandidatePracticeSessionRecord.job),
            selectinload(CandidatePracticeSessionRecord.job_version),
        )
        .where(
            CandidatePracticeSessionRecord.session_id == session_id,
            CandidatePracticeSessionRecord.candidate_id == binding.candidate_id,
        )
        .with_for_update()
    )
    session = db.execute(stmt).scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Practice session not found."},
        )

    # Must be awaiting answer
    allowed_answer_states = {
        PracticeSessionLifecycle.AWAITING_ANSWER.value,
        PracticeSessionLifecycle.IN_PROGRESS.value,
    }
    if session.lifecycle_status not in allowed_answer_states:
        # Check idempotency: if answer with this idempotency key already committed
        # or question already advanced, return current session state safely
        return _map_session_to_dto(session, db=db)

    # 2. Check Idempotency (Existing answer message with this question_id in current turn)
    snapshot = session.questions_snapshot or []
    conv_questions = [q for q in snapshot if q.get("question_format") == QuestionFormat.CONVERSATION.value]
    if session.current_conv_index >= len(conv_questions):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CONVERSATION_COMPLETE", "message": "All conversational questions are already completed."},
        )

    active_q = conv_questions[session.current_conv_index]
    if str(payload.question_id) != active_q["question_id"]:
        # Check if this question was already answered in a prior turn (idempotent duplicate submission)
        stmt_prior = select(CandidatePracticeMessageRecord).where(
            CandidatePracticeMessageRecord.session_id == session.session_id,
            CandidatePracticeMessageRecord.source_question_id == payload.question_id,
            CandidatePracticeMessageRecord.message_kind == PracticeMessageKind.ANSWER.value,
        )
        prior_ans = db.execute(stmt_prior).scalars().first()
        if prior_ans:
            return _map_session_to_dto(session, db=db)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "QUESTION_MISMATCH", "message": "Answer submitted does not match active question."},
        )

    answer_clean = payload.answer_text.strip()
    if not answer_clean:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "EMPTY_ANSWER", "message": "Answer text cannot be empty or whitespace only."},
        )

    # 3. Persist Candidate Answer and Commit Immediately
    stmt_max_seq = select(func.coalesce(func.max(CandidatePracticeMessageRecord.sequence_number), 0)).where(
        CandidatePracticeMessageRecord.session_id == session.session_id
    )
    max_seq = db.execute(stmt_max_seq).scalar() or 0
    seq_num = max_seq + 1

    cand_msg_id = uuid4()
    cand_msg = CandidatePracticeMessageRecord(
        message_id=cand_msg_id,
        session_id=session.session_id,
        sequence_number=seq_num,
        role=PracticeMessageRole.CANDIDATE.value,
        message_kind=PracticeMessageKind.ANSWER.value,
        source_question_id=payload.question_id,
        text=answer_clean,
        created_at=now,
    )
    db.add(cand_msg)
    session.total_response_count += 1
    session.concurrency_version += 1
    session.updated_at = now

    is_final_question = (session.current_conv_index + 1 >= len(conv_questions))

    if is_final_question:
        session.lifecycle_status = PracticeSessionLifecycle.FINAL_REVIEW_GENERATING.value
        db.commit()

        # Build transcript and generate final report
        transcript, source_hash = construct_canonical_transcript(session, db)
        gen_ok, final_review, prov_class, model_id, err_msg = await generate_final_interview_review_with_repair(
            session=session,
            transcript=transcript,
            db=db,
            custom_endpoint_url=custom_endpoint_url,
        )

        if gen_ok and final_review:
            # Check if a report for this session already exists
            stmt_existing = select(CandidatePracticeReportRecord).where(
                CandidatePracticeReportRecord.session_id == session.session_id,
                CandidatePracticeReportRecord.is_current.is_(True),
            )
            existing_rep = db.execute(stmt_existing).scalars().first()

            from app.services.practice_report_service import assemble_live_interview_report_data
            rep_data = assemble_live_interview_report_data(
                session=session,
                transcript=transcript,
                final_review=final_review,
                source_hash=source_hash,
                prov_class=prov_class,
                model_id=model_id,
            )

            if existing_rep:
                existing_rep.summary_text = rep_data["summary_text"]
                existing_rep.strongest_areas = rep_data["strongest_areas"]
                existing_rep.priority_improvements = rep_data["priority_improvements"]
                existing_rep.competency_commentary = rep_data["competency_commentary"]
                existing_rep.next_practice_plan = rep_data["next_practice_plan"]
                existing_rep.competency_aggregates = rep_data["competency_aggregates"]
                existing_rep.dimension_aggregates = rep_data["dimension_aggregates"]
                existing_rep.question_trend = rep_data["question_trend"]
                existing_rep.source_review_hash = rep_data["source_review_hash"]
                existing_rep.provider_class = prov_class
                existing_rep.model_identifier = model_id
                existing_rep.validation_status = "PASSED"
                existing_rep.lifecycle_status = "SUMMARY_READY"
                existing_rep.completion_counts = rep_data["completion_counts"]
                existing_rep.trend_classification = rep_data["trend_classification"]
                existing_rep.retry_count = (existing_rep.retry_count or 0) + 1
                existing_rep.finalized_at = datetime.now(timezone.utc)
            else:
                stmt_max_v = (
                    select(CandidatePracticeReportRecord.report_version)
                    .where(CandidatePracticeReportRecord.session_id == session.session_id)
                    .order_by(CandidatePracticeReportRecord.report_version.desc())
                )
                current_max_v = db.execute(stmt_max_v).scalars().first() or 0
                next_version = current_max_v + 1

                report_rec = CandidatePracticeReportRecord(
                    report_id=uuid4(),
                    session_id=session.session_id,
                    candidate_id=binding.candidate_id,
                    organization_id=session.organization_id,
                    report_version=next_version,
                    schema_version="live-interview-final-review.v1",
                    lifecycle_status="SUMMARY_READY",
                    source_review_hash=source_hash,
                    aggregation_policy_version="1.0.0",
                    summary_text=rep_data["summary_text"],
                    strongest_areas=rep_data["strongest_areas"],
                    priority_improvements=rep_data["priority_improvements"],
                    competency_commentary=rep_data["competency_commentary"],
                    response_patterns=[],
                    next_practice_plan=rep_data["next_practice_plan"],
                    competency_aggregates=rep_data["competency_aggregates"],
                    dimension_aggregates=rep_data["dimension_aggregates"],
                    question_trend=rep_data["question_trend"],
                    trend_classification=rep_data["trend_classification"],
                    mcq_breakdown={},
                    completion_counts=rep_data["completion_counts"],
                    disclaimer=final_review.disclaimer,
                    provider_class=prov_class,
                    model_identifier=model_id,
                    prompt_template_version="1.0.0",
                    guardrail_policy_version="1.0.0",
                    validation_status="PASSED",
                    retry_count=0,
                    is_current=True,
                    created_at=datetime.now(timezone.utc),
                    finalized_at=datetime.now(timezone.utc),
                )
                db.add(report_rec)

            session.lifecycle_status = PracticeSessionLifecycle.COMPLETED.value
            session.completed_at = datetime.now(timezone.utc)
        else:
            session.lifecycle_status = PracticeSessionLifecycle.FINAL_REVIEW_FAILED_RETRYABLE.value
        db.commit()
    else:
        # Move to next question generation
        session.current_conv_index += 1
        session.lifecycle_status = PracticeSessionLifecycle.QUESTION_GENERATING.value
        db.commit()

        # Build transcript and previous questions list for context
        next_idx = session.current_conv_index + 1
        target_plan_item = snapshot[session.current_conv_index]
        target_comp = target_plan_item.get("competency_name") or target_plan_item.get("target_competency") or "General"
        desired_type = target_plan_item.get("question_type", "TECHNICAL_SCENARIO")
        next_q_id = UUID(target_plan_item["question_id"])

        covered_comps = [q.get("competency_name") for q in snapshot[:session.current_conv_index] if q.get("competency_name")]
        prev_q_texts = [q.get("wording") for q in snapshot[:session.current_conv_index] if q.get("wording")]

        # Transcript history
        transcript_history = []
        stmt_prior_msgs = (
            select(CandidatePracticeMessageRecord)
            .where(CandidatePracticeMessageRecord.session_id == session.session_id)
            .order_by(CandidatePracticeMessageRecord.sequence_number.asc())
        )
        prior_msgs = db.execute(stmt_prior_msgs).scalars().all()
        curr_q = None
        for pm in prior_msgs:
            if pm.message_kind == PracticeMessageKind.PRIMARY_QUESTION.value:
                curr_q = pm.text
            elif pm.message_kind == PracticeMessageKind.ANSWER.value and curr_q:
                transcript_history.append({"question": curr_q, "answer": pm.text})
                curr_q = None

        # Load requirements
        job_version = session.job_version
        req_dicts = []
        if job_version:
            stmt_reqs = select(JobRequirement).where(JobRequirement.job_version_id == job_version.job_version_id).order_by(JobRequirement.display_order.asc())
            j_reqs = db.execute(stmt_reqs).scalars().all()
            req_dicts = [{"id": str(r.requirement_id), "requirement_id": str(r.requirement_id), "title": r.label, "competency_name": r.label, "summary": r.rationale or r.label} for r in j_reqs]

        if not req_dicts:
            req_dicts = [{"id": "req-1", "title": target_comp, "competency_name": target_comp, "summary": "Technical competence"}]

        gen_ok, q_payload, prov_class, model_id, err_msg = await generate_live_question_with_repair(
            session=session,
            target_competency=target_comp,
            desired_question_type=desired_type,
            question_index=next_idx,
            total_count=session.total_conv_count,
            requirements=req_dicts,
            covered_competencies=covered_comps,
            previous_questions=prev_q_texts,
            previous_transcript=transcript_history,
            db=db,
            custom_endpoint_url=custom_endpoint_url,
        )

        if gen_ok and q_payload:
            stmt_max_seq_next = select(func.coalesce(func.max(CandidatePracticeMessageRecord.sequence_number), 0)).where(
                CandidatePracticeMessageRecord.session_id == session.session_id
            )
            next_seq_num = (db.execute(stmt_max_seq_next).scalar() or 0) + 1

            q_msg = CandidatePracticeMessageRecord(
                message_id=uuid4(),
                session_id=session.session_id,
                sequence_number=next_seq_num,
                role=PracticeMessageRole.INTERVIEWER.value,
                message_kind=PracticeMessageKind.PRIMARY_QUESTION.value,
                source_question_id=next_q_id,
                text=q_payload.question,
                model_invocation_ref=f"{prov_class}:{model_id}",
                created_at=datetime.now(timezone.utc),
            )
            db.add(q_msg)
            target_plan_item["wording"] = q_payload.question
            session.questions_snapshot = snapshot
            session.lifecycle_status = PracticeSessionLifecycle.AWAITING_ANSWER.value
            session.model_call_count += 1
        else:
            logger.warning(f"Live question {next_idx} generation failed: {err_msg}")
            session.lifecycle_status = PracticeSessionLifecycle.QUESTION_GENERATION_FAILED_RETRYABLE.value

        db.commit()

    return _map_session_to_dto(session, db=db)


async def retry_question_generation(
    candidate_user: UserRecord,
    session_id: UUID,
    payload: PracticeQuestionRetryRequest,
    db: Session,
    custom_endpoint_url: Optional[str] = None,
) -> PracticeSessionDetailResponse:
    """Retries question generation for the current slot without fabricating deterministic wording or skipping."""
    binding = _get_active_binding(candidate_user, db)
    now = datetime.now(timezone.utc)

    stmt = (
        select(CandidatePracticeSessionRecord)
        .options(
            selectinload(CandidatePracticeSessionRecord.messages),
            selectinload(CandidatePracticeSessionRecord.job),
            selectinload(CandidatePracticeSessionRecord.job_version),
        )
        .where(
            CandidatePracticeSessionRecord.session_id == session_id,
            CandidatePracticeSessionRecord.candidate_id == binding.candidate_id,
        )
        .with_for_update()
    )
    session = db.execute(stmt).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "SESSION_NOT_FOUND", "message": "Practice session not found."})

    # If already awaiting answer, return current state idempotently
    if session.lifecycle_status == PracticeSessionLifecycle.AWAITING_ANSWER.value:
        return _map_session_to_dto(session, db=db)

    if session.lifecycle_status != PracticeSessionLifecycle.QUESTION_GENERATION_FAILED_RETRYABLE.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_STATE_FOR_RETRY", "message": f"Session status is {session.lifecycle_status}, not retryable."},
        )

    snapshot = session.questions_snapshot or []
    target_idx = session.current_conv_index
    if target_idx >= len(snapshot):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "INDEX_OUT_OF_BOUNDS", "message": "Question index out of bounds."})

    target_item = snapshot[target_idx]
    target_comp = target_item.get("competency_name") or "General"
    desired_type = target_item.get("question_type", "TECHNICAL_SCENARIO")
    q_id = UUID(target_item["question_id"])

    # Gather prior context
    covered_comps = [q.get("competency_name") for q in snapshot[:target_idx] if q.get("competency_name")]
    prev_q_texts = [q.get("wording") for q in snapshot[:target_idx] if q.get("wording")]

    transcript_history = []
    stmt_prior_msgs = (
        select(CandidatePracticeMessageRecord)
        .where(CandidatePracticeMessageRecord.session_id == session.session_id)
        .order_by(CandidatePracticeMessageRecord.sequence_number.asc())
    )
    prior_msgs = db.execute(stmt_prior_msgs).scalars().all()
    curr_q = None
    for pm in prior_msgs:
        if pm.message_kind == PracticeMessageKind.PRIMARY_QUESTION.value:
            curr_q = pm.text
        elif pm.message_kind == PracticeMessageKind.ANSWER.value and curr_q:
            transcript_history.append({"question": curr_q, "answer": pm.text})
            curr_q = None

    job_version = session.job_version
    req_dicts = []
    if job_version:
        stmt_reqs = select(JobRequirement).where(JobRequirement.job_version_id == job_version.job_version_id).order_by(JobRequirement.display_order.asc())
        j_reqs = db.execute(stmt_reqs).scalars().all()
        req_dicts = [{"id": str(r.requirement_id), "requirement_id": str(r.requirement_id), "title": r.label, "competency_name": r.label, "summary": r.rationale or r.label} for r in j_reqs]

    if not req_dicts:
        req_dicts = [{"id": "req-1", "title": target_comp, "competency_name": target_comp, "summary": "Technical competence"}]

    gen_ok, q_payload, prov_class, model_id, err_msg = await generate_live_question_with_repair(
        session=session,
        target_competency=target_comp,
        desired_question_type=desired_type,
        question_index=target_idx + 1,
        total_count=session.total_conv_count,
        requirements=req_dicts,
        covered_competencies=covered_comps,
        previous_questions=prev_q_texts,
        previous_transcript=transcript_history,
        db=db,
        custom_endpoint_url=custom_endpoint_url,
    )

    if gen_ok and q_payload:
        stmt_max_seq = select(func.coalesce(func.max(CandidatePracticeMessageRecord.sequence_number), 0)).where(
            CandidatePracticeMessageRecord.session_id == session.session_id
        )
        retry_seq_num = (db.execute(stmt_max_seq).scalar() or 0) + 1

        q_msg = CandidatePracticeMessageRecord(
            message_id=uuid4(),
            session_id=session.session_id,
            sequence_number=retry_seq_num,
            role=PracticeMessageRole.INTERVIEWER.value,
            message_kind=PracticeMessageKind.PRIMARY_QUESTION.value,
            source_question_id=q_id,
            text=q_payload.question,
            model_invocation_ref=f"{prov_class}:{model_id}",
            created_at=datetime.now(timezone.utc),
        )
        db.add(q_msg)
        target_item["wording"] = q_payload.question
        session.questions_snapshot = snapshot
        session.lifecycle_status = PracticeSessionLifecycle.AWAITING_ANSWER.value
        session.model_call_count += 1
    else:
        logger.warning(f"Retry live question {target_idx + 1} generation failed: {err_msg}")
        session.lifecycle_status = PracticeSessionLifecycle.QUESTION_GENERATION_FAILED_RETRYABLE.value

    session.updated_at = now
    db.commit()
    return _map_session_to_dto(session, db=db)


async def retry_final_review(
    candidate_user: UserRecord,
    session_id: UUID,
    db: Session,
    custom_endpoint_url: Optional[str] = None,
) -> PracticeSessionDetailResponse:
    """Retries final review generation for a completed Q&A session."""
    binding = _get_active_binding(candidate_user, db)
    now = datetime.now(timezone.utc)

    stmt = (
        select(CandidatePracticeSessionRecord)
        .options(
            selectinload(CandidatePracticeSessionRecord.messages),
            selectinload(CandidatePracticeSessionRecord.job),
            selectinload(CandidatePracticeSessionRecord.job_version),
        )
        .where(
            CandidatePracticeSessionRecord.session_id == session_id,
            CandidatePracticeSessionRecord.candidate_id == binding.candidate_id,
        )
        .with_for_update()
    )
    session = db.execute(stmt).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "SESSION_NOT_FOUND", "message": "Practice session not found."})

    transcript, source_hash = construct_canonical_transcript(session, db)
    gen_ok, final_review, prov_class, model_id, err_msg = await generate_final_interview_review_with_repair(
        session=session,
        transcript=transcript,
        db=db,
        custom_endpoint_url=custom_endpoint_url,
    )

    if gen_ok and final_review:
        # Check if a report for this session already exists
        stmt_existing = select(CandidatePracticeReportRecord).where(
            CandidatePracticeReportRecord.session_id == session.session_id,
            CandidatePracticeReportRecord.is_current.is_(True),
        )
        existing_rep = db.execute(stmt_existing).scalars().first()

        from app.services.practice_report_service import assemble_live_interview_report_data
        rep_data = assemble_live_interview_report_data(
            session=session,
            transcript=transcript,
            final_review=final_review,
            source_hash=source_hash,
            prov_class=prov_class,
            model_id=model_id,
        )

        if existing_rep:
            existing_rep.summary_text = rep_data["summary_text"]
            existing_rep.strongest_areas = rep_data["strongest_areas"]
            existing_rep.priority_improvements = rep_data["priority_improvements"]
            existing_rep.competency_commentary = rep_data["competency_commentary"]
            existing_rep.next_practice_plan = rep_data["next_practice_plan"]
            existing_rep.competency_aggregates = rep_data["competency_aggregates"]
            existing_rep.dimension_aggregates = rep_data["dimension_aggregates"]
            existing_rep.question_trend = rep_data["question_trend"]
            existing_rep.source_review_hash = rep_data["source_review_hash"]
            existing_rep.provider_class = prov_class
            existing_rep.model_identifier = model_id
            existing_rep.validation_status = "PASSED"
            existing_rep.lifecycle_status = "SUMMARY_READY"
            existing_rep.completion_counts = rep_data["completion_counts"]
            existing_rep.trend_classification = rep_data["trend_classification"]
            existing_rep.retry_count = (existing_rep.retry_count or 0) + 1
            existing_rep.finalized_at = datetime.now(timezone.utc)
        else:
            stmt_max_v = (
                select(CandidatePracticeReportRecord.report_version)
                .where(CandidatePracticeReportRecord.session_id == session.session_id)
                .order_by(CandidatePracticeReportRecord.report_version.desc())
            )
            current_max_v = db.execute(stmt_max_v).scalars().first() or 0
            next_version = current_max_v + 1

            report_rec = CandidatePracticeReportRecord(
                report_id=uuid4(),
                session_id=session.session_id,
                candidate_id=binding.candidate_id,
                organization_id=session.organization_id,
                report_version=next_version,
                schema_version="live-interview-final-review.v1",
                lifecycle_status="SUMMARY_READY",
                source_review_hash=source_hash,
                aggregation_policy_version="1.0.0",
                summary_text=rep_data["summary_text"],
                strongest_areas=rep_data["strongest_areas"],
                priority_improvements=rep_data["priority_improvements"],
                competency_commentary=rep_data["competency_commentary"],
                response_patterns=[],
                next_practice_plan=rep_data["next_practice_plan"],
                competency_aggregates=rep_data["competency_aggregates"],
                dimension_aggregates=rep_data["dimension_aggregates"],
                question_trend=rep_data["question_trend"],
                trend_classification=rep_data["trend_classification"],
                mcq_breakdown={},
                completion_counts=rep_data["completion_counts"],
                disclaimer=final_review.disclaimer,
                provider_class=prov_class,
                model_identifier=model_id,
                prompt_template_version="1.0.0",
                guardrail_policy_version="1.0.0",
                validation_status="PASSED",
                retry_count=0,
                is_current=True,
                created_at=datetime.now(timezone.utc),
                finalized_at=datetime.now(timezone.utc),
            )
            db.add(report_rec)

        session.lifecycle_status = PracticeSessionLifecycle.COMPLETED.value
        session.completed_at = datetime.now(timezone.utc)
    else:
        session.lifecycle_status = PracticeSessionLifecycle.FINAL_REVIEW_FAILED_RETRYABLE.value

    session.updated_at = now
    db.commit()
    return _map_session_to_dto(session, db=db)


async def retry_answer_review(
    candidate_user: UserRecord,
    session_id: UUID,
    payload: PracticeReviewRetryRequest,
    db: Session,
    custom_endpoint_url: Optional[str] = None,
) -> PracticeSessionDetailResponse:
    """Legacy helper maintained for backward compatibility."""
    return await retry_question_generation(
        candidate_user=candidate_user,
        session_id=session_id,
        payload=PracticeQuestionRetryRequest(idempotency_key=payload.idempotency_key),
        db=db,
        custom_endpoint_url=custom_endpoint_url,
    )


def advance_practice_question(
    candidate_user: UserRecord,
    session_id: UUID,
    db: Session,
    payload: Optional[PracticeAdvanceQuestionRequest] = None,
) -> PracticeSessionDetailResponse:
    """Legacy helper maintained for backward compatibility."""
    binding = _get_active_binding(candidate_user, db)
    stmt = (
        select(CandidatePracticeSessionRecord)
        .where(
            CandidatePracticeSessionRecord.session_id == session_id,
            CandidatePracticeSessionRecord.candidate_id == binding.candidate_id,
        )
    )
    session = db.execute(stmt).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "SESSION_NOT_FOUND", "message": "Practice session not found."})
    return _map_session_to_dto(session, db=db)




def submit_mcq_answer(
    candidate_user: UserRecord,
    session_id: UUID,
    payload: PracticeMcqAnswerRequest,
    db: Session,
) -> PracticeSessionDetailResponse:
    """Submits a candidate answer for the active MCQ question, evaluating correctness deterministically."""
    binding = _get_active_binding(candidate_user, db)
    now = datetime.now(timezone.utc)

    stmt = (
        select(CandidatePracticeSessionRecord)
        .options(
            selectinload(CandidatePracticeSessionRecord.messages),
            selectinload(CandidatePracticeSessionRecord.job),
            selectinload(CandidatePracticeSessionRecord.job_version),
        )
        .where(
            CandidatePracticeSessionRecord.session_id == session_id,
            CandidatePracticeSessionRecord.candidate_id == binding.candidate_id,
        )
        .with_for_update()
    )
    session = db.execute(stmt).scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Practice session not found."},
        )

    if session.lifecycle_status != PracticeSessionLifecycle.IN_PROGRESS.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "SESSION_NOT_ACTIVE", "message": f"Session is already {session.lifecycle_status}."},
        )

    if session.current_section != PracticeSection.MCQ.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_SECTION", "message": "Current section is not MCQ."},
        )

    snapshot = session.questions_snapshot or []
    mcq_questions = [q for q in snapshot if q.get("question_format") == QuestionFormat.MCQ_SINGLE_ANSWER.value]
    if session.current_mcq_index >= len(mcq_questions):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "MCQ_COMPLETE", "message": "All MCQ questions are already completed."},
        )

    active_mcq = mcq_questions[session.current_mcq_index]
    if str(payload.question_id) != active_mcq["question_id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "QUESTION_MISMATCH", "message": "Submitted answer does not match active MCQ question."},
        )

    # Validate option exists
    options = active_mcq.get("options", [])
    valid_option_ids = [opt["option_id"] for opt in options]
    if payload.selected_option_id not in valid_option_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_OPTION_ID", "message": f"Selected option '{payload.selected_option_id}' is not among valid options {valid_option_ids}."},
        )

    # Check for duplicate answer
    stmt_ans = select(CandidatePracticeMcqAnswerRecord).where(
        CandidatePracticeMcqAnswerRecord.session_id == session.session_id,
        CandidatePracticeMcqAnswerRecord.question_id == payload.question_id,
    )
    existing_ans = db.execute(stmt_ans).scalar_one_or_none()
    if existing_ans:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "ANSWER_ALREADY_SUBMITTED", "message": "An answer has already been submitted for this MCQ."},
        )

    # Deterministic correctness check against server snapshot (NEVER use LLM)
    correct_option_id = active_mcq.get("correct_option_id", "A")
    is_correct = (payload.selected_option_id.strip().upper() == correct_option_id.strip().upper())

    mcq_ans_rec = CandidatePracticeMcqAnswerRecord(
        answer_id=uuid4(),
        session_id=session.session_id,
        question_id=payload.question_id,
        selected_option_id=payload.selected_option_id,
        is_correct=is_correct,
        submitted_at=now,
    )
    db.add(mcq_ans_rec)

    # Advance MCQ position
    session.current_mcq_index += 1
    session.total_response_count += 1

    if session.current_mcq_index >= len(mcq_questions):
        session.current_section = PracticeSection.COMPLETE.value
        session.lifecycle_status = PracticeSessionLifecycle.COMPLETED.value
        session.completed_at = now

    session.concurrency_version += 1
    session.updated_at = now
    db.commit()

    return _map_session_to_dto(session, db=db)


def abandon_practice_session(
    candidate_user: UserRecord,
    session_id: UUID,
    db: Session,
) -> PracticeSessionAbandonResponse:
    """Abandons an active practice session."""
    binding = _get_active_binding(candidate_user, db)
    now = datetime.now(timezone.utc)

    stmt = select(CandidatePracticeSessionRecord).where(
        CandidatePracticeSessionRecord.session_id == session_id,
        CandidatePracticeSessionRecord.candidate_id == binding.candidate_id,
    )
    session = db.execute(stmt).scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Practice session not found."},
        )

    allowed_abandon_states = {
        PracticeSessionLifecycle.IN_PROGRESS.value,
        PracticeSessionLifecycle.QUESTION_GENERATING.value,
        PracticeSessionLifecycle.AWAITING_ANSWER.value,
        PracticeSessionLifecycle.ANSWER_SAVING.value,
        PracticeSessionLifecycle.QUESTION_GENERATION_FAILED_RETRYABLE.value,
        PracticeSessionLifecycle.FINAL_ANSWER_SAVING.value,
        PracticeSessionLifecycle.FINAL_REVIEW_GENERATING.value,
        PracticeSessionLifecycle.FINAL_REVIEW_FAILED_RETRYABLE.value,
    }
    if session.lifecycle_status not in allowed_abandon_states:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "SESSION_NOT_ACTIVE", "message": f"Session is already {session.lifecycle_status}."},
        )

    session.lifecycle_status = PracticeSessionLifecycle.ABANDONED.value
    session.abandoned_at = now
    session.updated_at = now
    db.commit()

    return PracticeSessionAbandonResponse(
        session_id=session.session_id,
        lifecycle_status=PracticeSessionLifecycle.ABANDONED.value,
        safe_message="Practice interview session ended.",
    )


def _map_report_to_dto(
    report: CandidatePracticeReportRecord,
    session: CandidatePracticeSessionRecord,
) -> CandidatePracticeReportDto:
    job_title = session.job_version.title if session.job_version else (session.job.title if session.job else "Practice Job")
    radar_eligible = 3 <= len([c for c in report.competency_aggregates if c.get("numeric_score") is not None]) <= 6

    from app.schemas.practice_report_contract import (
        CandidatePracticeReportDto,
        CompetencyAggregateDto,
        CompetencyCommentaryItem,
        CompletionCountsDto,
        DimensionAggregateDto,
        McqBreakdownDto,
        PriorityImprovementItem,
        QuestionTrendItemDto,
        ResponsePatternItem,
        StrengthAreaItem,
    )

    mcq_b_dto = None
    if report.mcq_breakdown and isinstance(report.mcq_breakdown, dict) and report.mcq_breakdown.get("total_count", 0) > 0:
        mcq_b_dto = McqBreakdownDto.model_validate(report.mcq_breakdown)

    return CandidatePracticeReportDto(
        report_id=report.report_id,
        session_id=report.session_id,
        candidate_id=report.candidate_id,
        job_id=session.job_id,
        job_title=job_title,
        job_revision=session.job_revision,
        report_version=report.report_version,
        schema_version=report.schema_version,
        lifecycle_status=report.lifecycle_status,
        aggregation_policy_version=report.aggregation_policy_version,
        source_review_hash=report.source_review_hash,
        summary_text=report.summary_text,
        strongest_areas=[StrengthAreaItem.model_validate(item) for item in (report.strongest_areas or [])],
        priority_improvements=[PriorityImprovementItem.model_validate(item) for item in (report.priority_improvements or [])],
        competency_commentary=[CompetencyCommentaryItem.model_validate(item) for item in (report.competency_commentary or [])],
        response_patterns=[ResponsePatternItem.model_validate(item) for item in (report.response_patterns or [])],
        next_practice_plan=report.next_practice_plan or [],
        competency_aggregates=[CompetencyAggregateDto.model_validate(item) for item in (report.competency_aggregates or [])],
        radar_eligible=radar_eligible,
        dimension_aggregates=[DimensionAggregateDto.model_validate(item) for item in (report.dimension_aggregates or [])],
        question_trend=[QuestionTrendItemDto.model_validate(item) for item in (report.question_trend or [])],
        trend_classification=report.trend_classification,
        mcq_breakdown=mcq_b_dto,
        completion_counts=CompletionCountsDto.model_validate(report.completion_counts or {
            "total_primary_questions": 0,
            "total_conversational_questions": 0,
            "total_mcq_questions": 0,
            "assessable_answers_count": 0,
            "insufficient_answers_count": 0,
            "skipped_count": 0,
            "unavailable_count": 0,
        }),
        disclaimer=report.disclaimer,
        validation_status=report.validation_status,
        retry_count=report.retry_count,
        is_current=report.is_current,
        created_at=report.created_at,
        finalized_at=report.finalized_at,
    )


async def get_or_generate_practice_report(
    candidate_user: UserRecord,
    session_id: UUID,
    db: Session,
    custom_endpoint_url: Optional[str] = None,
) -> CandidatePracticeReportDto:
    """Retrieves existing report or calculates deterministic aggregates and invokes local AI for final summary."""
    binding = _get_active_binding(candidate_user, db)
    now = datetime.now(timezone.utc)

    # 1. Load Session
    stmt = (
        select(CandidatePracticeSessionRecord)
        .options(
            selectinload(CandidatePracticeSessionRecord.messages),
            selectinload(CandidatePracticeSessionRecord.job),
            selectinload(CandidatePracticeSessionRecord.job_version),
            selectinload(CandidatePracticeSessionRecord.reports),
        )
        .where(
            CandidatePracticeSessionRecord.session_id == session_id,
            CandidatePracticeSessionRecord.candidate_id == binding.candidate_id,
        )
    )
    session = db.execute(stmt).scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Practice session not found."},
        )

    # Session must be completed or answers complete
    if session.lifecycle_status not in (PracticeSessionLifecycle.COMPLETED.value, "ANSWERS_COMPLETE"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "SESSION_NOT_COMPLETED", "message": "Cannot generate coaching report for an in-progress session."},
        )

    # 1. Check if a current valid report already exists for this session
    stmt_existing = (
        select(CandidatePracticeReportRecord)
        .where(
            CandidatePracticeReportRecord.session_id == session.session_id,
            CandidatePracticeReportRecord.is_current.is_(True),
        )
    )
    existing_rep = db.execute(stmt_existing).scalar_one_or_none()
    if existing_rep:
        # Check if existing report was generated with the old broken 0-assessable logic
        comp_counts = existing_rep.completion_counts or {}
        has_broken_counts = (comp_counts.get("assessable_answers_count", 0) == 0 and comp_counts.get("skipped_count", 0) > 0)
        if not has_broken_counts and existing_rep.lifecycle_status == "SUMMARY_READY":
            return _map_report_to_dto(existing_rep, session)

    # 2. Build canonical transcript and generate final review
    transcript, source_hash = construct_canonical_transcript(session, db)
    gen_ok, final_review, prov_class, model_id, err_msg = await generate_final_interview_review_with_repair(
        session=session,
        transcript=transcript,
        db=db,
        custom_endpoint_url=custom_endpoint_url,
    )

    from app.services.practice_report_service import assemble_live_interview_report_data
    from app.schemas.practice_report_contract import ReportLifecycleStatus

    # Determine report version
    stmt_max_v = (
        select(CandidatePracticeReportRecord.report_version)
        .where(CandidatePracticeReportRecord.session_id == session.session_id)
        .order_by(CandidatePracticeReportRecord.report_version.desc())
    )
    current_max_v = db.execute(stmt_max_v).scalars().first() or 0
    next_version = current_max_v + 1

    report_id = uuid4()
    if gen_ok and final_review:
        rep_data = assemble_live_interview_report_data(
            session=session,
            transcript=transcript,
            final_review=final_review,
            source_hash=source_hash,
            prov_class=prov_class,
            model_id=model_id,
        )
        val_status = "PASSED"
        final_status = ReportLifecycleStatus.SUMMARY_READY.value
        fin_at = now
    else:
        # Fallback structured report data from transcript
        from app.schemas.live_interview_contract import (
            LiveInterviewFinalReviewPayload,
            ResponseQualityDimensions,
            LiveResponseQualityLevel,
        )
        fallback_review = LiveInterviewFinalReviewPayload(
            schema_version="live-interview-final-review.v1",
            overall_summary="Practice coaching completed. Automated AI summary generation is currently unavailable. Review your question breakdown below.",
            strongest_responses=[],
            improvement_areas=[],
            competency_reviews=[],
            response_quality=ResponseQualityDimensions(
                relevance=LiveResponseQualityLevel.DEVELOPING,
                clarity=LiveResponseQualityLevel.DEVELOPING,
                technical_depth=LiveResponseQualityLevel.DEVELOPING,
                evidence=LiveResponseQualityLevel.DEVELOPING,
                problem_solving_structure=LiveResponseQualityLevel.DEVELOPING,
            ),
            next_practice_plan=[
                "Use a structured problem-action-result format for your answers.",
                "Explain specific verification steps you performed in your technical work.",
                "Practice explaining edge cases and trade-offs for core competencies.",
            ],
            disclaimer="This is practice coaching and not a hiring decision.",
        )
        rep_data = assemble_live_interview_report_data(
            session=session,
            transcript=transcript,
            final_review=fallback_review,
            source_hash=source_hash,
            prov_class=prov_class,
            model_id=model_id,
        )
        val_status = "FAILED"
        final_status = ReportLifecycleStatus.SUMMARY_FAILED_RETRYABLE.value
        fin_at = None

    new_report = CandidatePracticeReportRecord(
        report_id=report_id,
        session_id=session.session_id,
        candidate_id=binding.candidate_id,
        organization_id=session.organization_id,
        report_version=next_version,
        schema_version="live-interview-final-review.v1",
        lifecycle_status=final_status,
        source_review_hash=source_hash,
        aggregation_policy_version="1.0.0",
        summary_text=rep_data["summary_text"],
        strongest_areas=rep_data["strongest_areas"],
        priority_improvements=rep_data["priority_improvements"],
        competency_commentary=rep_data["competency_commentary"],
        response_patterns=[],
        next_practice_plan=rep_data["next_practice_plan"],
        competency_aggregates=rep_data["competency_aggregates"],
        dimension_aggregates=rep_data["dimension_aggregates"],
        question_trend=rep_data["question_trend"],
        trend_classification=rep_data["trend_classification"],
        mcq_breakdown={},
        completion_counts=rep_data["completion_counts"],
        disclaimer="This is practice coaching and not a hiring decision.",
        provider_class=prov_class,
        model_identifier=model_id,
        prompt_template_version="1.0.0",
        guardrail_policy_version="1.0.0",
        validation_status=val_status,
        retry_count=0,
        is_current=True,
        created_at=now,
        finalized_at=fin_at,
    )
    db.add(new_report)
    db.commit()

    return _map_report_to_dto(new_report, session)


async def retry_practice_report(
    candidate_user: UserRecord,
    session_id: UUID,
    db: Session,
    custom_endpoint_url: Optional[str] = None,
) -> CandidatePracticeReportDto:
    """Retries a failed coaching report summary generation without duplicating data or altering transcript."""
    binding = _get_active_binding(candidate_user, db)
    now = datetime.now(timezone.utc)

    stmt_rep = (
        select(CandidatePracticeReportRecord)
        .where(
            CandidatePracticeReportRecord.session_id == session_id,
            CandidatePracticeReportRecord.candidate_id == binding.candidate_id,
            CandidatePracticeReportRecord.is_current.is_(True),
        )
    )
    report = db.execute(stmt_rep).scalar_one_or_none()

    stmt_sess = select(CandidatePracticeSessionRecord).where(
        CandidatePracticeSessionRecord.session_id == session_id
    )
    session = db.execute(stmt_sess).scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Practice session not found."},
        )

    transcript, source_hash = construct_canonical_transcript(session, db)
    gen_ok, final_review, prov_class, model_id, err_msg = await generate_final_interview_review_with_repair(
        session=session,
        transcript=transcript,
        db=db,
        custom_endpoint_url=custom_endpoint_url,
    )

    from app.services.practice_report_service import assemble_live_interview_report_data
    from app.schemas.practice_report_contract import ReportLifecycleStatus

    if not report:
        # Generate new report version
        return await get_or_generate_practice_report(
            candidate_user=candidate_user,
            session_id=session_id,
            db=db,
            custom_endpoint_url=custom_endpoint_url,
        )

    report.retry_count = (report.retry_count or 0) + 1
    report.provider_class = prov_class
    report.model_identifier = model_id
    if gen_ok and final_review:
        rep_data = assemble_live_interview_report_data(
            session=session,
            transcript=transcript,
            final_review=final_review,
            source_hash=source_hash,
            prov_class=prov_class,
            model_id=model_id,
        )
        report.summary_text = rep_data["summary_text"]
        report.strongest_areas = rep_data["strongest_areas"]
        report.priority_improvements = rep_data["priority_improvements"]
        report.competency_commentary = rep_data["competency_commentary"]
        report.next_practice_plan = rep_data["next_practice_plan"]
        report.competency_aggregates = rep_data["competency_aggregates"]
        report.dimension_aggregates = rep_data["dimension_aggregates"]
        report.question_trend = rep_data["question_trend"]
        report.completion_counts = rep_data["completion_counts"]
        report.trend_classification = rep_data["trend_classification"]
        report.source_review_hash = rep_data["source_review_hash"]
        report.validation_status = "PASSED"
        report.lifecycle_status = ReportLifecycleStatus.SUMMARY_READY.value
        report.finalized_at = now
    else:
        # Re-assemble deterministic aggregates from transcript so report UI is never stuck on empty/skipped
        from app.schemas.live_interview_contract import (
            LiveInterviewFinalReviewPayload,
            ResponseQualityDimensions,
            LiveResponseQualityLevel,
        )
        fallback_review = LiveInterviewFinalReviewPayload(
            schema_version="live-interview-final-review.v1",
            overall_summary="Practice coaching completed. Automated AI summary generation is currently unavailable. Review your question breakdown below.",
            strongest_responses=[],
            improvement_areas=[],
            competency_reviews=[],
            response_quality=ResponseQualityDimensions(
                relevance=LiveResponseQualityLevel.DEVELOPING,
                clarity=LiveResponseQualityLevel.DEVELOPING,
                technical_depth=LiveResponseQualityLevel.DEVELOPING,
                evidence=LiveResponseQualityLevel.DEVELOPING,
                problem_solving_structure=LiveResponseQualityLevel.DEVELOPING,
            ),
            next_practice_plan=[
                "Use a structured problem-action-result format for your answers.",
                "Explain specific verification steps you performed in your technical work.",
                "Practice explaining edge cases and trade-offs for core competencies.",
            ],
            disclaimer="This is practice coaching and not a hiring decision.",
        )
        rep_data = assemble_live_interview_report_data(
            session=session,
            transcript=transcript,
            final_review=fallback_review,
            source_hash=source_hash,
            prov_class=prov_class,
            model_id=model_id,
        )
        report.summary_text = rep_data["summary_text"]
        report.competency_aggregates = rep_data["competency_aggregates"]
        report.dimension_aggregates = rep_data["dimension_aggregates"]
        report.question_trend = rep_data["question_trend"]
        report.completion_counts = rep_data["completion_counts"]
        report.trend_classification = rep_data["trend_classification"]
        report.source_review_hash = rep_data["source_review_hash"]
        report.validation_status = "FAILED"
        report.lifecycle_status = ReportLifecycleStatus.SUMMARY_FAILED_RETRYABLE.value

    db.commit()
    return _map_report_to_dto(report, session)

