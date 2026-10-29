"""Candidate Official Interview Service (Phase 4C.2).

Orchestrates official application-bound interview sessions, durable answer persistence in PostgreSQL,
zero-leakage question formatting, recovery on refresh/interruption, and atomic submission.
Strictly prohibits LLM calls, AI feedback, scoring, or mutation of match/application/job state.
"""
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional
import uuid
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models.auth import AuditEventType, AuditReasonCode, AuditResult, UserRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateApplicationRecord,
    CandidateApplicationStatus,
    CandidateBindingStatus,
    CandidateInterviewAnswerRecord,
    CandidateInterviewSessionRecord,
    CandidatePracticeReleaseRecord,
)
from app.models.interview import AiInterviewQuestionSetRecord
from app.models.job import JobRequisition, JobVersion
from app.schemas.candidate_interview import (
    CandidateInterviewCapabilityResponse,
    CandidateInterviewMcqOptionDto,
    CandidateInterviewQuestionDto,
    CandidateInterviewSessionResponse,
    CandidateSaveAnswerRequest,
    CandidateSaveAnswerResponse,
    CandidateSubmitInterviewRequest,
    CandidateSubmitInterviewResponse,
    CandidateTranscriptionResponse,
)
from app.schemas.phase3_enums import PracticeReleaseStatus
from app.services.speech_service import (
    EphemeralAudioProcessor,
    SpeechRuntimeCapabilityService,
    TextToSpeechAdapter,
)

logger = logging.getLogger("ai_hiring.services.candidate_interview")


def get_interview_capabilities() -> CandidateInterviewCapabilityResponse:
    """Returns candidate-safe capability metadata for local audio interviews."""
    caps = SpeechRuntimeCapabilityService.get_safe_capabilities()
    return CandidateInterviewCapabilityResponse(**caps)


def _get_candidate_binding(candidate_user: UserRecord, db: Session) -> CandidateAccountBindingRecord:
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


def _get_candidate_identities(candidate_user: UserRecord, binding: CandidateAccountBindingRecord, db: Session) -> List[UUID]:
    user_cand_ids = db.execute(
        select(CandidateApplicationRecord.candidate_id).where(
            (CandidateApplicationRecord.user_id == candidate_user.user_id)
            | (CandidateApplicationRecord.binding_id == binding.binding_id)
            | (CandidateApplicationRecord.candidate_id == binding.candidate_id)
        )
    ).scalars().all()
    return list(set([binding.candidate_id] + [c for c in user_cand_ids if c]))


def _compute_snapshot_hash(snapshot: List[Dict[str, Any]]) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _format_candidate_safe_questions(
    snapshot_items: List[Dict[str, Any]],
    saved_answers: Dict[UUID, CandidateInterviewAnswerRecord],
) -> List[CandidateInterviewQuestionDto]:
    # 1. Classify items into Conversational and MCQ
    conversational_items: List[Dict[str, Any]] = []
    mcq_items: List[Dict[str, Any]] = []

    for raw_item in snapshot_items:
        raw_fmt = (raw_item.get("question_format") or "").upper()
        if raw_fmt in ("MCQ", "MCQ_SINGLE_ANSWER") or (raw_item.get("options") and len(raw_item.get("options", [])) > 0):
            mcq_items.append(raw_item)
        else:
            conversational_items.append(raw_item)

    # Order strictly: conversational first, MCQs after
    ordered_items = conversational_items + mcq_items

    formatted: List[CandidateInterviewQuestionDto] = []
    for idx, raw_item in enumerate(ordered_items):
        slot_id_str = raw_item.get("slot_id") or raw_item.get("question_id") or str(uuid.uuid4())
        try:
            slot_id = UUID(str(slot_id_str))
        except Exception:
            slot_id = uuid.uuid4()

        raw_fmt = (raw_item.get("question_format") or "").upper()
        is_mcq = raw_fmt in ("MCQ", "MCQ_SINGLE_ANSWER") or (raw_item.get("options") and len(raw_item.get("options", [])) > 0)
        q_format = "MCQ_SINGLE_ANSWER" if is_mcq else "CONVERSATION"

        q_text = (
            raw_item.get("question_text")
            or raw_item.get("ai_wording")
            or raw_item.get("question")
            or raw_item.get("stem")
            or raw_item.get("text")
            or "Interview Question"
        )
        competency = raw_item.get("competency") or raw_item.get("competency_name")
        is_supp = raw_item.get("is_supplemental", False) or raw_item.get("layer") == "SUPPLEMENTAL_EVIDENCE"

        options_dto: List[CandidateInterviewMcqOptionDto] = []
        if is_mcq and "options" in raw_item:
            raw_options = raw_item.get("options") or []
            for opt in raw_options:
                if isinstance(opt, dict):
                    opt_id = str(opt.get("option_id") or opt.get("id") or opt.get("key") or "")
                    opt_text = str(opt.get("text") or opt.get("label") or "")
                else:
                    opt_id = str(opt)
                    opt_text = str(opt)
                options_dto.append(CandidateInterviewMcqOptionDto(id=opt_id, text=opt_text))

        saved = saved_answers.get(slot_id)
        is_ans = False
        saved_text = None
        saved_opt = None
        saved_at = None

        if saved:
            saved_text = saved.text_answer
            saved_opt = saved.selected_option_id
            saved_at = saved.saved_at
            if q_format == "CONVERSATION" and saved_text and len(saved_text.strip()) > 0:
                is_ans = True
            elif q_format == "MCQ_SINGLE_ANSWER" and saved_opt:
                is_ans = True

        formatted.append(
            CandidateInterviewQuestionDto(
                slot_id=slot_id,
                question_order=idx + 1,
                question_format=q_format,
                question_text=q_text,
                competency=competency,
                is_supplemental=is_supp,
                options=options_dto,
                saved_text_answer=saved_text,
                saved_selected_option_id=saved_opt,
                is_answered=is_ans,
                saved_at=saved_at,
            )
        )
    return formatted


def start_or_resume_interview_session(
    release_id: UUID,
    candidate_user: UserRecord,
    db: Session,
) -> CandidateInterviewSessionResponse:
    """Starts a new official interview session or returns the existing session for the assignment."""
    binding = _get_candidate_binding(candidate_user, db)
    cand_ids = _get_candidate_identities(candidate_user, binding, db)

    # 1. Fetch assignment / release
    stmt_rel = (
        select(CandidatePracticeReleaseRecord)
        .options(
            selectinload(CandidatePracticeReleaseRecord.job),
            selectinload(CandidatePracticeReleaseRecord.job_version),
            selectinload(CandidatePracticeReleaseRecord.ai_set),
        )
        .where(
            CandidatePracticeReleaseRecord.release_id == release_id,
            CandidatePracticeReleaseRecord.candidate_id.in_(cand_ids),
            CandidatePracticeReleaseRecord.organization_id == binding.organization_id,
        )
    )
    release = db.execute(stmt_rel).scalar_one_or_none()
    if not release:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ASSIGNMENT_NOT_FOUND", "message": "Official interview assignment not found."},
        )

    if release.status != PracticeReleaseStatus.RELEASED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "ASSIGNMENT_REVOKED", "message": "This interview assignment has been revoked or expired."},
        )

    # 1.1 Authoritative Application Integrity Rule:
    # Verify the release is bound to a submitted application for the candidate, job, revision, and organization
    if release.application_id:
        stmt_app = select(CandidateApplicationRecord).where(
            CandidateApplicationRecord.application_id == release.application_id,
            CandidateApplicationRecord.job_id == release.job_id,
            CandidateApplicationRecord.status == CandidateApplicationStatus.SUBMITTED.value,
        ).filter(
            (CandidateApplicationRecord.candidate_id == binding.candidate_id)
            | (CandidateApplicationRecord.binding_id == binding.binding_id)
            | (CandidateApplicationRecord.user_id == candidate_user.user_id)
        ).filter(
            (CandidateApplicationRecord.job_version_id == release.job_version_id)
            | (CandidateApplicationRecord.job_revision == release.job_revision)
        )
        bound_app = db.execute(stmt_app).scalar_one_or_none()
        if not bound_app:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "INVALID_RELEASE_APPLICATION_BINDING",
                    "message": "The application bound to this interview release is missing, non-submitted, or invalid.",
                },
            )
    else:
        # Check if an eligible submitted application exists for this job & revision
        stmt_app_fallback = select(CandidateApplicationRecord).where(
            CandidateApplicationRecord.job_id == release.job_id,
            CandidateApplicationRecord.status == CandidateApplicationStatus.SUBMITTED.value,
        ).filter(
            (CandidateApplicationRecord.candidate_id == binding.candidate_id)
            | (CandidateApplicationRecord.binding_id == binding.binding_id)
            | (CandidateApplicationRecord.user_id == candidate_user.user_id)
        ).filter(
            (CandidateApplicationRecord.job_version_id == release.job_version_id)
            | (CandidateApplicationRecord.job_revision == release.job_revision)
        )
        fallback_app = db.execute(stmt_app_fallback).scalars().first()
        if not fallback_app:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "APPLICATION_REQUIRED_FOR_INTERVIEW",
                    "message": "No submitted application found for this candidate and job revision.",
                },
            )

    # 2. Check for existing session (Idempotent start / resume)
    stmt_sess = (
        select(CandidateInterviewSessionRecord)
        .options(selectinload(CandidateInterviewSessionRecord.answers))
        .where(CandidateInterviewSessionRecord.release_id == release_id)
        .order_by(desc(CandidateInterviewSessionRecord.created_at))
    )
    existing_sess = db.execute(stmt_sess).scalars().first()

    if existing_sess:
        answers_map = {ans.slot_id: ans for ans in existing_sess.answers}
        questions_dto = _format_candidate_safe_questions(existing_sess.questions_snapshot, answers_map)
        answered_count = sum(1 for q in questions_dto if q.is_answered)

        return CandidateInterviewSessionResponse(
            session_id=existing_sess.session_id,
            release_id=existing_sess.release_id,
            application_id=existing_sess.application_id,
            job_id=existing_sess.job_id,
            job_title=existing_sess.job_version.title if existing_sess.job_version else (existing_sess.job.title if existing_sess.job else "Interview"),
            job_revision=existing_sess.job_revision,
            interview_mode=existing_sess.interview_mode or release.interview_mode or "TEXT",
            status=existing_sess.status,
            total_questions=len(questions_dto),
            answered_questions=answered_count,
            started_at=existing_sess.started_at,
            last_saved_at=existing_sess.last_saved_at,
            submitted_at=existing_sess.submitted_at,
            questions=questions_dto,
        )

    # Phase 4D.3 Audio interview validation
    release_mode = (release.interview_mode or (release.ai_set.interview_mode if release.ai_set else "TEXT") or "TEXT").upper()
    if release_mode == "AUDIO":
        # 1. Require speech capability
        caps = SpeechRuntimeCapabilityService.get_safe_capabilities()
        if not caps["speech_to_text_available"] or not caps["text_to_speech_available"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "SPEECH_RUNTIME_UNAVAILABLE",
                    "message": caps.get("unavailable_reason") or "Local speech runtime (whisper.cpp / Piper) is unavailable. Please check configuration.",
                },
            )

    # 3. Create new session from frozen approved question set
    ai_set = release.ai_set
    if not ai_set or not ai_set.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "QUESTION_SET_EMPTY", "message": "Approved question set has no questions to conduct interview."},
        )

    snapshot_items = ai_set.items

    if release_mode == "AUDIO":
        # Reject if contains any MCQ
        has_mcq = any(
            (item.get("question_format") or "").upper() in ("MCQ", "MCQ_SINGLE_ANSWER")
            or bool(item.get("options") and len(item.get("options", [])) > 0)
            for item in snapshot_items
        )
        if has_mcq:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "AUDIO_MCQ_NOT_ALLOWED",
                    "message": "Audio interviews strictly reject multiple choice questions (MCQs).",
                },
            )
    snapshot_hash = _compute_snapshot_hash(snapshot_items)
    now = datetime.now(timezone.utc)

    new_session = CandidateInterviewSessionRecord(
        session_id=uuid.uuid4(),
        organization_id=binding.organization_id,
        release_id=release.release_id,
        candidate_id=binding.candidate_id,
        application_id=release.application_id,
        job_id=release.job_id,
        job_version_id=release.job_version_id,
        job_revision=release.job_revision,
        ai_set_id=release.ai_set_id,
        set_version=release.set_version,
        interview_mode=release_mode,
        snapshot_hash=snapshot_hash,
        questions_snapshot=snapshot_items,
        status="IN_PROGRESS",
        started_at=now,
        last_saved_at=now,
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)

    questions_dto = _format_candidate_safe_questions(snapshot_items, {})
    return CandidateInterviewSessionResponse(
        session_id=new_session.session_id,
        release_id=new_session.release_id,
        application_id=new_session.application_id,
        job_id=new_session.job_id,
        job_title=release.job_version.title if release.job_version else (release.job.title if release.job else "Interview"),
        job_revision=new_session.job_revision,
        interview_mode=new_session.interview_mode or release_mode,
        status=new_session.status,
        total_questions=len(questions_dto),
        answered_questions=0,
        started_at=new_session.started_at,
        last_saved_at=new_session.last_saved_at,
        submitted_at=None,
        questions=questions_dto,
    )


def get_interview_session(
    session_id: UUID,
    candidate_user: UserRecord,
    db: Session,
) -> CandidateInterviewSessionResponse:
    """Retrieves an official interview session with saved answers for the authenticated candidate."""
    binding = _get_candidate_binding(candidate_user, db)
    cand_ids = _get_candidate_identities(candidate_user, binding, db)

    stmt = (
        select(CandidateInterviewSessionRecord)
        .options(
            selectinload(CandidateInterviewSessionRecord.job),
            selectinload(CandidateInterviewSessionRecord.job_version),
            selectinload(CandidateInterviewSessionRecord.answers),
        )
        .where(
            CandidateInterviewSessionRecord.session_id == session_id,
            CandidateInterviewSessionRecord.candidate_id.in_(cand_ids),
            CandidateInterviewSessionRecord.organization_id == binding.organization_id,
        )
    )
    sess = db.execute(stmt).scalar_one_or_none()
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Interview session not found."},
        )

    answers_map = {ans.slot_id: ans for ans in sess.answers}
    questions_dto = _format_candidate_safe_questions(sess.questions_snapshot, answers_map)
    answered_count = sum(1 for q in questions_dto if q.is_answered)

    return CandidateInterviewSessionResponse(
        session_id=sess.session_id,
        release_id=sess.release_id,
        application_id=sess.application_id,
        job_id=sess.job_id,
        job_title=sess.job_version.title if sess.job_version else (sess.job.title if sess.job else "Interview"),
        job_revision=sess.job_revision,
        interview_mode=sess.interview_mode or "TEXT",
        status=sess.status,
        total_questions=len(questions_dto),
        answered_questions=answered_count,
        started_at=sess.started_at,
        last_saved_at=sess.last_saved_at,
        submitted_at=sess.submitted_at,
        questions=questions_dto,
    )


def save_interview_answer(
    session_id: UUID,
    slot_id: UUID,
    payload: CandidateSaveAnswerRequest,
    candidate_user: UserRecord,
    db: Session,
) -> CandidateSaveAnswerResponse:
    """Saves or updates draft text or MCQ answer for one question in an in-progress session."""
    binding = _get_candidate_binding(candidate_user, db)
    cand_ids = _get_candidate_identities(candidate_user, binding, db)

    stmt = (
        select(CandidateInterviewSessionRecord)
        .options(selectinload(CandidateInterviewSessionRecord.answers))
        .where(
            CandidateInterviewSessionRecord.session_id == session_id,
            CandidateInterviewSessionRecord.candidate_id.in_(cand_ids),
            CandidateInterviewSessionRecord.organization_id == binding.organization_id,
        )
    )
    sess = db.execute(stmt).scalar_one_or_none()
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Interview session not found."},
        )

    if sess.status == "SUBMITTED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "SESSION_ALREADY_SUBMITTED", "message": "Cannot modify answers for a submitted interview."},
        )

    # Find question in snapshot
    matched_q = None
    q_index = 0
    for idx, item in enumerate(sess.questions_snapshot):
        item_slot = item.get("slot_id") or item.get("question_id")
        if item_slot and str(item_slot) == str(slot_id):
            matched_q = item
            q_index = idx + 1
            break

    if not matched_q:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "QUESTION_NOT_FOUND", "message": "Question does not belong to this interview session."},
        )

    q_format = matched_q.get("question_format") or ("MCQ_SINGLE_ANSWER" if "options" in matched_q else "CONVERSATION")
    now = datetime.now(timezone.utc)

    # Validate inputs based on format
    clean_text = None
    clean_option_id = None
    is_answered = False

    if q_format == "CONVERSATION":
        if payload.selected_option_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_ANSWER_FORMAT", "message": "Conversational questions accept text answers only."},
            )
        clean_text = payload.text_answer.strip() if payload.text_answer is not None else None
        if clean_text:
            is_answered = True
    elif q_format == "MCQ_SINGLE_ANSWER":
        if payload.text_answer is not None and len(payload.text_answer.strip()) > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_ANSWER_FORMAT", "message": "MCQ questions accept selected option ID only."},
            )
        if payload.selected_option_id is not None:
            raw_options = matched_q.get("options") or []
            valid_opt_ids = [
                str(o.get("option_id") or o.get("id") or o.get("key") or "")
                if isinstance(o, dict)
                else str(o)
                for o in raw_options
            ]
            if payload.selected_option_id not in valid_opt_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "INVALID_MCQ_OPTION", "message": f"Option '{payload.selected_option_id}' is not valid for this question."},
                )
            clean_option_id = payload.selected_option_id
            is_answered = True

    # Ensure slot_id is a UUID
    if isinstance(slot_id, str):
        try:
            slot_uuid = UUID(slot_id)
        except Exception:
            slot_uuid = uuid.uuid4()
    else:
        slot_uuid = slot_id

    # Check for existing answer row
    stmt_ans = select(CandidateInterviewAnswerRecord).where(
        CandidateInterviewAnswerRecord.session_id == session_id,
        CandidateInterviewAnswerRecord.slot_id == slot_uuid,
    )
    ans_record = db.execute(stmt_ans).scalar_one_or_none()

    if ans_record:
        ans_record.question_format = q_format
        ans_record.text_answer = clean_text
        ans_record.selected_option_id = clean_option_id
        ans_record.saved_at = now
        ans_record.answer_order = q_index
    else:
        ans_record = CandidateInterviewAnswerRecord(
            answer_id=uuid.uuid4(),
            session_id=session_id,
            slot_id=slot_uuid,
            question_format=q_format,
            text_answer=clean_text,
            selected_option_id=clean_option_id,
            answer_order=q_index,
            saved_at=now,
        )
        db.add(ans_record)

    sess.last_saved_at = now
    db.commit()
    db.refresh(ans_record)

    # Compute updated overall answered count
    answers_map = {ans.slot_id: ans for ans in sess.answers}
    answers_map[slot_uuid] = ans_record
    questions_dto = _format_candidate_safe_questions(sess.questions_snapshot, answers_map)
    answered_count = sum(1 for q in questions_dto if q.is_answered)

    return CandidateSaveAnswerResponse(
        answer_id=ans_record.answer_id,
        session_id=session_id,
        slot_id=slot_uuid,
        is_answered=is_answered,
        saved_at=ans_record.saved_at,
        total_questions=len(questions_dto),
        answered_questions=answered_count,
        message="Answer saved successfully.",
    )



def submit_interview_session(
    session_id: UUID,
    payload: CandidateSubmitInterviewRequest,
    candidate_user: UserRecord,
    db: Session,
) -> CandidateSubmitInterviewResponse:
    """Atomically finalizes and submits the official interview session, making answers immutable."""
    binding = _get_candidate_binding(candidate_user, db)
    cand_ids = _get_candidate_identities(candidate_user, binding, db)

    stmt = (
        select(CandidateInterviewSessionRecord)
        .options(selectinload(CandidateInterviewSessionRecord.answers))
        .where(
            CandidateInterviewSessionRecord.session_id == session_id,
            CandidateInterviewSessionRecord.candidate_id.in_(cand_ids),
            CandidateInterviewSessionRecord.organization_id == binding.organization_id,
        )
    )
    sess = db.execute(stmt).scalar_one_or_none()
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Interview session not found."},
        )

    # Idempotent return if already submitted
    if sess.status == "SUBMITTED":
        answers_map = {ans.slot_id: ans for ans in sess.answers}
        questions_dto = _format_candidate_safe_questions(sess.questions_snapshot, answers_map)
        answered_count = sum(1 for q in questions_dto if q.is_answered)
        total_q = len(questions_dto)

        return CandidateSubmitInterviewResponse(
            session_id=sess.session_id,
            release_id=sess.release_id,
            status=sess.status,
            submitted_at=sess.submitted_at or sess.updated_at,
            total_questions=total_q,
            answered_questions=answered_count,
            unanswered_questions=total_q - answered_count,
            message="Official interview has already been submitted.",
        )

    answers_map = {ans.slot_id: ans for ans in sess.answers}
    questions_dto = _format_candidate_safe_questions(sess.questions_snapshot, answers_map)
    total_q = len(questions_dto)
    answered_count = sum(1 for q in questions_dto if q.is_answered)
    unanswered_count = total_q - answered_count

    if unanswered_count > 0 and not payload.confirmed_incomplete:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INCOMPLETE_SUBMISSION_REQUIRES_CONFIRMATION",
                "message": f"You have {unanswered_count} unanswered question(s). Explicit confirmation is required to submit incomplete.",
                "unanswered_count": unanswered_count,
                "total_questions": total_q,
            },
        )

    now = datetime.now(timezone.utc)
    sess.status = "SUBMITTED"
    sess.submitted_at = now
    sess.last_saved_at = now

    db.commit()
    db.refresh(sess)

    return CandidateSubmitInterviewResponse(
        session_id=sess.session_id,
        release_id=sess.release_id,
        status=sess.status,
        submitted_at=sess.submitted_at,
        total_questions=total_q,
        answered_questions=answered_count,
        unanswered_questions=unanswered_count,
        message="Official interview submitted successfully. Your responses have been recorded.",
    )


def get_question_audio(
    session_id: UUID,
    slot_id: str,
    candidate_user: UserRecord,
    db: Session,
) -> bytes:
    """Generates or retrieves Piper TTS speech for an approved question in an official Audio interview session."""
    binding = _get_candidate_binding(candidate_user, db)

    stmt = select(CandidateInterviewSessionRecord).where(
        CandidateInterviewSessionRecord.session_id == session_id,
        CandidateInterviewSessionRecord.candidate_id == binding.candidate_id,
        CandidateInterviewSessionRecord.organization_id == binding.organization_id,
    )
    sess = db.execute(stmt).scalar_one_or_none()
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Interview session not found."},
        )

    if (sess.interview_mode or "").upper() != "AUDIO":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "AUDIO_NOT_SUPPORTED_FOR_TEXT_INTERVIEW", "message": "Question audio playback is only available for Audio interviews."},
        )

    try:
        target_slot_uuid = UUID(slot_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_SLOT_ID", "message": "Invalid slot identifier format."},
        )

    # Find the question in the frozen snapshot
    target_question = None
    for item in sess.questions_snapshot:
        item_slot_str = item.get("slot_id") or item.get("question_id")
        if item_slot_str and UUID(str(item_slot_str)) == target_slot_uuid:
            target_question = item
            break

    if not target_question:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "QUESTION_NOT_FOUND", "message": "Question slot not found in interview session."},
        )

    # Strictly candidate-safe question text only - never include rubrics, notes, or scoring info
    question_text = (target_question.get("question_text") or target_question.get("text") or "").strip()
    if not question_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "EMPTY_QUESTION_TEXT", "message": "Question text is empty and cannot be converted to speech."},
        )

    snapshot_identity = str(sess.ai_set_id or sess.session_id)
    return TextToSpeechAdapter.synthesize_question_speech(
        question_snapshot_id=snapshot_identity,
        question_text=question_text,
    )


def transcribe_candidate_question_audio(
    session_id: UUID,
    slot_id: str,
    audio_bytes: bytes,
    original_content_type: str,
    candidate_user: UserRecord,
    db: Session,
) -> CandidateTranscriptionResponse:
    """Ephemeral conversion and local whisper.cpp transcription for candidate audio response."""
    binding = _get_candidate_binding(candidate_user, db)

    stmt = select(CandidateInterviewSessionRecord).where(
        CandidateInterviewSessionRecord.session_id == session_id,
        CandidateInterviewSessionRecord.candidate_id == binding.candidate_id,
        CandidateInterviewSessionRecord.organization_id == binding.organization_id,
    )
    sess = db.execute(stmt).scalar_one_or_none()
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Interview session not found."},
        )

    if sess.status == "SUBMITTED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "SESSION_ALREADY_SUBMITTED", "message": "Cannot transcribe audio for a submitted interview."},
        )

    if (sess.interview_mode or "").upper() != "AUDIO":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "AUDIO_NOT_SUPPORTED_FOR_TEXT_INTERVIEW", "message": "Audio transcription is only available for Audio interviews."},
        )

    try:
        target_slot_uuid = UUID(slot_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_SLOT_ID", "message": "Invalid slot identifier format."},
        )

    target_question = None
    for item in sess.questions_snapshot:
        item_slot_str = item.get("slot_id") or item.get("question_id")
        if item_slot_str and UUID(str(item_slot_str)) == target_slot_uuid:
            target_question = item
            break

    if not target_question:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "QUESTION_NOT_FOUND", "message": "Question slot not found in interview session."},
        )

    q_format = (target_question.get("question_format") or "").upper()
    if q_format in ("MCQ", "MCQ_SINGLE_ANSWER") or (target_question.get("options") and len(target_question.get("options", [])) > 0):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "AUDIO_MCQ_NOT_ALLOWED", "message": "Audio transcription is not allowed for MCQs."},
        )

    # Process and transcribe ephemerally (zero-retention: deleted in finally)
    try:
        res = EphemeralAudioProcessor.process_and_transcribe(
            audio_bytes=audio_bytes,
            original_content_type=original_content_type,
        )
        if isinstance(res, tuple):
            transcript, duration_s = res
        else:
            transcript, duration_s = res, None
    except ValueError as val_err:
        err_str = str(val_err)
        err_code = "TRANSCRIPT_DURATION_MISMATCH" if "TRANSCRIPT_DURATION_MISMATCH" in err_str else "INVALID_AUDIO_RECORDING"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": err_code, "message": err_str},
        )
    except RuntimeError as run_err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "SPEECH_PROCESSING_ERROR", "message": str(run_err)},
        )

    is_empty = bool(not transcript.strip())
    return CandidateTranscriptionResponse(
        session_id=session_id,
        slot_id=target_slot_uuid,
        transcript=transcript,
        is_empty=is_empty,
        duration_seconds=duration_s,
    )
