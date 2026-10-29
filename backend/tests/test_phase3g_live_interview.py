"""Focused test suite for Urgent Phase 3G Architecture Correction: Live Sequential AI Interview.

Tests live question generation, answer persistence, next question generation,
elimination of per-answer visible coaching and follow-up states, session failure & retry,
final Q&A transcript aggregation, post-session evaluation, and candidate isolation.
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.auth import UserRecord, UserRole
from app.models.base import Base
from app.models.candidate import CandidateIdentityRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateBindingStatus,
    CandidateInvitationRecord,
    CandidatePracticeMessageRecord,
    CandidatePracticeReleaseRecord,
    CandidatePracticeReportRecord,
    CandidatePracticeSessionRecord,
)
from app.models.interview import AiInterviewQuestionSetRecord
from app.models.job import JobLifecycleStatus, JobRequisition, JobRequirement, JobVersion, JobVersionStatus
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.live_interview_contract import (
    LiveInterviewFinalReviewPayload,
    LiveInterviewQuestionPayload,
    LiveTranscriptItem,
)
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    PracticeMessageKind,
    PracticeMessageRole,
    PracticeReleaseStatus,
    PracticeSection,
    PracticeSessionLifecycle,
    QuestionFormat,
)
from app.schemas.practice_session_contract import (
    PracticeConversationAnswerRequest,
    PracticeQuestionRetryRequest,
    PracticeSessionStartRequest,
)
from app.services.live_interview_service import (
    construct_canonical_transcript,
    generate_final_interview_review_with_repair,
    generate_live_question_with_repair,
    validate_final_review_payload,
    validate_live_question_payload,
)
from app.services.practice_session_service import (
    get_practice_session_state,
    retry_final_review,
    retry_question_generation,
    start_or_resume_practice_session,
    submit_conversational_answer,
)


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def test_candidate_bundle(test_db: Session):
    """Creates a candidate user, resume artifact, candidate identity, job requisition, approved job version,
    practice release, and account binding."""
    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email=f"recruiter_{uuid.uuid4().hex[:6]}@example.com",
        display_name="Test Recruiter",
        password_hash="pw_hash",
        role=UserRole.RECRUITER,
        is_active=True,
    )
    cand_user = UserRecord(
        user_id=uuid.uuid4(),
        email=f"candidate_{uuid.uuid4().hex[:6]}@example.com",
        display_name="Candidate Live Practice",
        password_hash="pw_hash",
        role=UserRole.CANDIDATE,
        is_active=True,
    )
    test_db.add_all([recruiter, cand_user])
    test_db.flush()

    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="candidate_resume.pdf",
        declared_mime_type="application/pdf",
        size_bytes=1024,
        sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    test_db.add(artifact)
    test_db.flush()

    identity = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        full_name="Candidate Live Practice",
        primary_email=cand_user.email,
        identity_payload={"skills": ["Python", "PostgreSQL"]},
    )
    test_db.add(identity)
    test_db.flush()

    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(job)
    test_db.flush()

    job_ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        title="Senior Python Engineer",
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(job_ver)
    test_db.flush()

    req1 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=job_ver.job_version_id,
        label="Must be proficient in Python async APIs and Postgres",
        normalized_label="python async apis and postgres",
        category="SKILL",
        necessity="MUST_HAVE",
        priority="HIGH",
        display_order=1,
    )
    req2 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=job_ver.job_version_id,
        label="Experience with distributed systems and fault tolerance",
        normalized_label="distributed systems and fault tolerance",
        category="EXPERIENCE",
        necessity="MUST_HAVE",
        priority="HIGH",
        display_order=2,
    )
    test_db.add_all([req1, req2])
    test_db.flush()

    org_id = uuid.uuid4()
    invitation = CandidateInvitationRecord(
        invitation_id=uuid.uuid4(),
        organization_id=org_id,
        candidate_id=identity.identity_id,
        token_hash="a" * 64,
        expires_at=datetime.now(timezone.utc),
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(invitation)
    test_db.flush()

    binding = CandidateAccountBindingRecord(
        binding_id=uuid.uuid4(),
        organization_id=org_id,
        candidate_id=identity.identity_id,
        user_id=cand_user.user_id,
        invitation_id=invitation.invitation_id,
        status=CandidateBindingStatus.ACTIVE.value,
    )
    test_db.add(binding)
    test_db.flush()

    release = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        organization_id=org_id,
        candidate_id=identity.identity_id,
        job_id=job.job_id,
        job_version_id=job_ver.job_version_id,
        released_by_user_id=recruiter.user_id,
        status=PracticeReleaseStatus.RELEASED.value,
    )
    test_db.add(release)
    test_db.commit()

    return {
        "recruiter": recruiter,
        "candidate": cand_user,
        "identity": identity,
        "job": job,
        "job_version": job_ver,
        "requirements": [req1, req2],
        "release": release,
    }


# ==============================================================================
# 1 & 2 & 3 & 4. START SESSION: INVOKES LOCAL AI, PERSISTS QUESTION 1, PROVENANCE
# ==============================================================================

@pytest.mark.anyio
async def test_start_session_invokes_local_ai_and_persists_question_1(test_db: Session, test_candidate_bundle):
    bundle = test_candidate_bundle
    cand_user = bundle["candidate"]
    release = bundle["release"]
    req = bundle["requirements"][0]

    mock_q1 = LiveInterviewQuestionPayload(
        schema_version="live-interview-question.v1",
        question="Describe how you would handle connection pooling and transaction rollbacks in Python async APIs.",
        competency_id="python_core",
        question_type="TECHNICAL_SCENARIO",
        intent_summary="Assess Python database transaction handling",
        source_requirement_ids=[str(req.requirement_id)],
        question_index=1,
        estimated_answer_minutes=3,
    )

    with patch("app.services.practice_session_service.generate_live_question_with_repair", new=AsyncMock(return_value=(True, mock_q1, "LMStudioProvider", "qwen/qwen3-4b-2507", None))):
        result = await start_or_resume_practice_session(
            candidate_user=cand_user,
            release_id=release.release_id,
            db=test_db,
        )

    assert result.lifecycle_status == PracticeSessionLifecycle.AWAITING_ANSWER.value
    assert len(result.messages) == 2  # Greeting + Q1
    q1_msg = result.messages[1]
    assert q1_msg.role == PracticeMessageRole.INTERVIEWER.value
    assert q1_msg.text == mock_q1.question

    # Verify session persisted in DB
    session_rec = test_db.query(CandidatePracticeSessionRecord).filter_by(session_id=result.session_id).first()
    assert session_rec is not None
    assert session_rec.lifecycle_status == PracticeSessionLifecycle.AWAITING_ANSWER.value
    assert session_rec.current_conv_index == 0


@pytest.mark.anyio
async def test_answer_committed_and_generates_next_question(test_db: Session, test_candidate_bundle):
    bundle = test_candidate_bundle
    cand_user = bundle["candidate"]
    release = bundle["release"]
    req = bundle["requirements"][0]

    mock_q1 = LiveInterviewQuestionPayload(
        schema_version="live-interview-question.v1",
        question="Describe how you would handle connection pooling in Python.",
        competency_id="python_core",
        question_type="TECHNICAL_SCENARIO",
        intent_summary="Assess Python DB pooling",
        source_requirement_ids=[str(req.requirement_id)],
        question_index=1,
        estimated_answer_minutes=3,
    )

    with patch("app.services.practice_session_service.generate_live_question_with_repair", new=AsyncMock(return_value=(True, mock_q1, "LMStudioProvider", "qwen/qwen3-4b-2507", None))):
        start_res = await start_or_resume_practice_session(
            candidate_user=cand_user,
            release_id=release.release_id,
            db=test_db,
        )

    session_id = start_res.session_id
    q1_id = start_res.active_question.question_id

    mock_q2 = LiveInterviewQuestionPayload(
        schema_version="live-interview-question.v1",
        question="How do you handle circuit breakers and cascading failures in microservices?",
        competency_id="sys_design",
        question_type="SYSTEM_DESIGN",
        intent_summary="Assess fault tolerance and circuit breakers",
        source_requirement_ids=[str(bundle["requirements"][1].requirement_id)],
        question_index=2,
        estimated_answer_minutes=3,
    )

    with patch("app.services.practice_session_service.generate_live_question_with_repair", new=AsyncMock(return_value=(True, mock_q2, "LMStudioProvider", "qwen/qwen3-4b-2507", None))):
        ans_res = await submit_conversational_answer(
            candidate_user=cand_user,
            session_id=session_id,
            payload=PracticeConversationAnswerRequest(
                question_id=q1_id,
                answer_text="I use SQLAlchemy async engine with connection pooling and context managers for rollback.",
                idempotency_key="idemp_ans_1",
            ),
            db=test_db,
        )

    assert ans_res.lifecycle_status == PracticeSessionLifecycle.AWAITING_ANSWER.value
    messages = ans_res.messages
    assert len(messages) == 4  # Greeting, Q1, A1, Q2
    assert messages[1].role == PracticeMessageRole.INTERVIEWER.value
    assert messages[2].role == PracticeMessageRole.CANDIDATE.value
    assert messages[2].text == "I use SQLAlchemy async engine with connection pooling and context managers for rollback."
    assert messages[3].role == PracticeMessageRole.INTERVIEWER.value
    assert messages[3].text == mock_q2.question


@pytest.mark.anyio
async def test_idempotent_answer_submission_prevents_duplicate_generation(test_db: Session, test_candidate_bundle):
    bundle = test_candidate_bundle
    cand_user = bundle["candidate"]
    release = bundle["release"]
    req = bundle["requirements"][0]

    mock_q1 = LiveInterviewQuestionPayload(
        schema_version="live-interview-question.v1",
        question="Question 1 prompt",
        competency_id="python_core",
        question_type="TECHNICAL_SCENARIO",
        intent_summary="Q1 intent",
        source_requirement_ids=[str(req.requirement_id)],
        question_index=1,
        estimated_answer_minutes=3,
    )

    with patch("app.services.practice_session_service.generate_live_question_with_repair", new=AsyncMock(return_value=(True, mock_q1, "LMStudioProvider", "qwen/qwen3-4b-2507", None))):
        start_res = await start_or_resume_practice_session(
            candidate_user=cand_user,
            release_id=release.release_id,
            db=test_db,
        )

    session_id = start_res.session_id
    q1_id = start_res.active_question.question_id

    mock_q2 = LiveInterviewQuestionPayload(
        schema_version="live-interview-question.v1",
        question="Question 2 prompt",
        competency_id="sys_design",
        question_type="SYSTEM_DESIGN",
        intent_summary="Q2 intent",
        source_requirement_ids=[str(bundle["requirements"][1].requirement_id)],
        question_index=2,
        estimated_answer_minutes=3,
    )

    with patch("app.services.practice_session_service.generate_live_question_with_repair", new=AsyncMock(return_value=(True, mock_q2, "LMStudioProvider", "qwen/qwen3-4b-2507", None))):
        res1 = await submit_conversational_answer(
            candidate_user=cand_user,
            session_id=session_id,
            payload=PracticeConversationAnswerRequest(
                question_id=q1_id,
                answer_text="My first answer",
                idempotency_key="same_key_123",
            ),
            db=test_db,
        )

    # Submitting again
    res2 = await submit_conversational_answer(
        candidate_user=cand_user,
        session_id=session_id,
        payload=PracticeConversationAnswerRequest(
            question_id=q1_id,
            answer_text="My first answer",
            idempotency_key="same_key_123",
        ),
        db=test_db,
    )

    assert len(res1.messages) == len(res2.messages) == 4


@pytest.mark.anyio
async def test_generation_failure_sets_retryable_and_retry_succeeds(test_db: Session, test_candidate_bundle):
    bundle = test_candidate_bundle
    cand_user = bundle["candidate"]
    release = bundle["release"]
    req = bundle["requirements"][0]

    # Model fails on start
    with patch("app.services.practice_session_service.generate_live_question_with_repair", new=AsyncMock(return_value=(False, None, "LMStudioProvider", "qwen/qwen3-4b-2507", "Connection refused"))):
        start_res = await start_or_resume_practice_session(
            candidate_user=cand_user,
            release_id=release.release_id,
            db=test_db,
        )

    assert start_res.lifecycle_status == PracticeSessionLifecycle.QUESTION_GENERATION_FAILED_RETRYABLE.value
    assert len(start_res.messages) == 1  # Only greeting, no question visible!

    session_id = start_res.session_id

    # Now candidate clicks Retry Question Generation
    mock_q1 = LiveInterviewQuestionPayload(
        schema_version="live-interview-question.v1",
        question="Describe python concurrency models (asyncio vs threading).",
        competency_id="python_core",
        question_type="TECHNICAL_SCENARIO",
        intent_summary="Assess concurrency",
        source_requirement_ids=[str(req.requirement_id)],
        question_index=1,
        estimated_answer_minutes=3,
    )

    with patch("app.services.practice_session_service.generate_live_question_with_repair", new=AsyncMock(return_value=(True, mock_q1, "LMStudioProvider", "qwen/qwen3-4b-2507", None))):
        retry_res = await retry_question_generation(
            candidate_user=cand_user,
            session_id=session_id,
            payload=PracticeQuestionRetryRequest(idempotency_key="retry_key_1"),
            db=test_db,
        )

    assert retry_res.lifecycle_status == PracticeSessionLifecycle.AWAITING_ANSWER.value
    assert len(retry_res.messages) == 2
    assert retry_res.messages[1].text == mock_q1.question


# ==============================================================================
# 15, 16, 17. VALIDATION OF LIVE QUESTION PAYLOAD
# ==============================================================================

def test_question_validation_rules():
    allowed_comps = ["python_core", "sys_design"]
    valid_req_ids = ["req-1", "req-2"]

    # 1. Valid payload
    payload = LiveInterviewQuestionPayload(
        schema_version="live-interview-question.v1",
        question="How do you optimize slow Postgres queries?",
        competency_id="python_core",
        question_type="TECHNICAL_SCENARIO",
        intent_summary="Database query optimization",
        source_requirement_ids=["req-1"],
        question_index=2,
        estimated_answer_minutes=3,
    )
    is_valid, err_code, err_msg = validate_live_question_payload(
        payload=payload,
        expected_index=2,
        allowed_competencies=allowed_comps,
        allowed_requirement_ids=valid_req_ids,
        existing_questions=["Describe how you validate input in Python."],
    )
    assert is_valid is True
    assert err_code is None

    # 2. Duplicate question rejected
    duplicate_payload = payload.model_copy(update={"question": "Describe how you validate input in Python."})
    is_valid, err_code, _ = validate_live_question_payload(
        payload=duplicate_payload,
        expected_index=2,
        allowed_competencies=allowed_comps,
        allowed_requirement_ids=valid_req_ids,
        existing_questions=["Describe how you validate input in Python."],
    )
    assert is_valid is False
    assert err_code == "DUPLICATE_QUESTION"

    # 3. Disallowed competency rejected
    bad_comp = payload.model_copy(update={"competency_id": "unauthorized_comp"})
    is_valid, err_code, _ = validate_live_question_payload(
        payload=bad_comp,
        expected_index=2,
        allowed_competencies=allowed_comps,
        allowed_requirement_ids=valid_req_ids,
        existing_questions=[],
    )
    assert is_valid is False
    assert err_code == "INVALID_COMPETENCY"

    # 4. Invalid requirement ID rejected
    bad_req = payload.model_copy(update={"source_requirement_ids": ["forged_req_id"]})
    is_valid, err_code, _ = validate_live_question_payload(
        payload=bad_req,
        expected_index=2,
        allowed_competencies=allowed_comps,
        allowed_requirement_ids=valid_req_ids,
        existing_questions=[],
    )
    assert is_valid is False
    assert err_code == "INVALID_REQUIREMENT_ID"

    # 5. Coaching / scoring cues in question rejected
    coaching_cue = payload.model_copy(update={"question": "Good job on your last answer! Now tell me about indexing."})
    is_valid, err_code, _ = validate_live_question_payload(
        payload=coaching_cue,
        expected_index=2,
        allowed_competencies=allowed_comps,
        allowed_requirement_ids=valid_req_ids,
        existing_questions=[],
    )
    assert is_valid is False
    assert err_code == "COACHING_LEAKAGE"


# ==============================================================================
# 20 & 21 & 22 & 23 & 24. FINAL ANSWER COMMITTED -> POST-SESSION EVALUATION
# ==============================================================================

@pytest.mark.anyio
async def test_final_answer_triggers_post_session_review_and_persists_report(test_db: Session, test_candidate_bundle):
    bundle = test_candidate_bundle
    cand_user = bundle["candidate"]
    release = bundle["release"]
    req = bundle["requirements"][0]

    # Start session with 1 total question to test completion
    mock_q1 = LiveInterviewQuestionPayload(
        schema_version="live-interview-question.v1",
        question="How do you manage state and caching in FastAPI?",
        competency_id="python_core",
        question_type="TECHNICAL_SCENARIO",
        intent_summary="FastAPI caching",
        source_requirement_ids=[str(req.requirement_id)],
        question_index=1,
        estimated_answer_minutes=3,
    )

    with patch("app.services.practice_session_service.generate_live_question_with_repair", new=AsyncMock(return_value=(True, mock_q1, "LMStudioProvider", "qwen/qwen3-4b-2507", None))):
        start_res = await start_or_resume_practice_session(
            candidate_user=cand_user,
            release_id=release.release_id,
            db=test_db,
        )

    session_id = start_res.session_id
    q1_id = start_res.active_question.question_id

    # Adjust session to have total 1 question for fast testing
    sess_rec = test_db.query(CandidatePracticeSessionRecord).filter_by(session_id=session_id).first()
    sess_rec.total_conv_count = 1
    sess_rec.questions_snapshot = sess_rec.questions_snapshot[:1]
    test_db.commit()

    # Candidate submits final answer
    mock_final_review = LiveInterviewFinalReviewPayload(
        schema_version="live-interview-final-review.v1",
        overall_summary="The candidate demonstrated solid understanding of FastAPI async dependencies and Redis caching.",
        strongest_responses=[
            {
                "question_id": str(q1_id),
                "reason": "Clear explanation of Redis connection pools and lifespan event management.",
                "answer_evidence": "I use Redis connection pool injected via lifespan context.",
            }
        ],
        improvement_areas=[
            {
                "question_id": str(q1_id),
                "issue": "Cache invalidation strategies were not detailed.",
                "how_to_improve": "Discuss TTL expiration and pub/sub cache invalidation in future responses.",
            }
        ],
        competency_reviews=[
            {
                "competency_id": "python_core",
                "level": "STRONG",
                "commentary": "Comprehensive knowledge of async Python dependencies.",
                "question_ids": [str(q1_id)],
            }
        ],
        response_quality={
            "relevance": "STRONG",
            "clarity": "STRONG",
            "technical_depth": "DEVELOPING",
            "evidence": "STRONG",
            "problem_solving_structure": "STRONG",
        },
        best_answer_question_id=str(q1_id),
        best_answer_reason="Structured and concise use of async patterns.",
        next_practice_plan=[
            "Practice explaining distributed cache eviction policies.",
            "Review Python GIL impact on CPU-bound microservices.",
            "Work through rate limiting architectural patterns.",
        ],
        disclaimer="This is practice coaching and not a hiring decision.",
    )

    with patch("app.services.practice_session_service.generate_final_interview_review_with_repair", new=AsyncMock(return_value=(True, mock_final_review, "LMStudioProvider", "qwen/qwen3-4b-2507", None))):
        sub_res = await submit_conversational_answer(
            candidate_user=cand_user,
            session_id=session_id,
            payload=PracticeConversationAnswerRequest(
                question_id=q1_id,
                answer_text="I use Redis connection pool injected via lifespan context.",
                idempotency_key="final_ans_key",
            ),
            db=test_db,
        )

    assert sub_res.lifecycle_status == PracticeSessionLifecycle.COMPLETED.value

    # Check report record in DB
    report = test_db.query(CandidatePracticeReportRecord).filter_by(session_id=session_id).first()
    assert report is not None
    assert report.summary_text == mock_final_review.overall_summary
    assert len(report.strongest_areas) == 1
    assert len(report.priority_improvements) == 1
    assert len(report.next_practice_plan) == 3

    # Now test retry_final_review does not throw duplicate key UniqueViolation
    from app.services.practice_session_service import retry_final_review

    mock_retried_review = LiveInterviewFinalReviewPayload(
        schema_version="live-interview-final-review.v1",
        overall_summary="Updated summary from retry review.",
        strongest_responses=[
            {
                "question_id": str(q1_id),
                "reason": "Clear explanation of Redis connection pools.",
                "answer_evidence": "I use Redis connection pool.",
            }
        ],
        improvement_areas=[
            {
                "question_id": str(q1_id),
                "issue": "Cache invalidation strategies need detail.",
                "how_to_improve": "Discuss TTL expiration.",
            }
        ],
        competency_reviews=[
            {
                "competency_id": "python_core",
                "level": "STRONG",
                "commentary": "Comprehensive knowledge.",
                "question_ids": [str(q1_id)],
            }
        ],
        response_quality={
            "relevance": "STRONG",
            "clarity": "STRONG",
            "technical_depth": "STRONG",
            "evidence": "STRONG",
            "problem_solving_structure": "STRONG",
        },
        best_answer_question_id=str(q1_id),
        best_answer_reason="Structured and concise use of async patterns.",
        next_practice_plan=[
            "Practice explaining distributed cache eviction policies.",
            "Review Python GIL impact on CPU-bound microservices.",
            "Work through rate limiting architectural patterns.",
        ],
        disclaimer="This is practice coaching and not a hiring decision.",
    )

    with patch("app.services.practice_session_service.generate_final_interview_review_with_repair", new=AsyncMock(return_value=(True, mock_retried_review, "LMStudioProvider", "qwen/qwen3-4b-2507", None))):
        retry_res = await retry_final_review(
            candidate_user=cand_user,
            session_id=session_id,
            db=test_db,
        )

    assert retry_res.lifecycle_status == PracticeSessionLifecycle.COMPLETED.value
    report_updated = test_db.query(CandidatePracticeReportRecord).filter_by(session_id=session_id).first()
    assert report_updated.summary_text == "Updated summary from retry review."
    assert report_updated.retry_count == 1


# ==============================================================================
# 28. CANDIDATE ACCESS ISOLATION
# ==============================================================================

@pytest.mark.anyio
async def test_candidate_cannot_access_other_candidate_session(test_db: Session, test_candidate_bundle):
    bundle = test_candidate_bundle
    cand_user = bundle["candidate"]
    release = bundle["release"]

    # Other candidate
    other_user = UserRecord(
        user_id=uuid.uuid4(),
        email="unauthorized@example.com",
        display_name="Other Candidate",
        password_hash="pw_hash",
        role=UserRole.CANDIDATE,
        is_active=True,
    )
    test_db.add(other_user)
    test_db.commit()

    # Start session for candidate 1
    mock_q1 = LiveInterviewQuestionPayload(
        schema_version="live-interview-question.v1",
        question="Describe how you test Python code.",
        competency_id="python_core",
        question_type="TECHNICAL_SCENARIO",
        intent_summary="Unit testing",
        source_requirement_ids=[str(bundle["requirements"][0].requirement_id)],
        question_index=1,
        estimated_answer_minutes=3,
    )

    with patch("app.services.practice_session_service.generate_live_question_with_repair", new=AsyncMock(return_value=(True, mock_q1, "LMStudioProvider", "qwen/qwen3-4b-2507", None))):
        start_res = await start_or_resume_practice_session(
            candidate_user=cand_user,
            release_id=release.release_id,
            db=test_db,
        )

    session_id = start_res.session_id

    # Other candidate attempts to read or submit
    with pytest.raises(HTTPException) as exc:
        get_practice_session_state(
            candidate_user=other_user,
            session_id=session_id,
            db=test_db,
        )
    assert exc.value.status_code == 403
