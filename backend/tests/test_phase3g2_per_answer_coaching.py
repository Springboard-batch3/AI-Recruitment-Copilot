"""Comprehensive test suite for Phase 3G.2: Per-Answer Local AI Coaching and Bounded Follow-ups."""
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.auth import UserRecord, UserRole
from app.models.candidate import CandidateIdentityRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateBindingStatus,
    CandidateInvitationRecord,
    CandidatePracticeAnswerReviewRecord,
    CandidatePracticeAnswerSegmentRecord,
    CandidatePracticeMessageRecord,
    CandidatePracticeReleaseRecord,
    CandidatePracticeSessionRecord,
)
from app.models.interview import (
    AiInterviewQuestionSetRecord,
    JobInterviewTemplateRecord,
)
from app.models.job import JobLifecycleStatus, JobRequisition, JobRequirement, JobVersion
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.mcq_contract import McqGenerateRequest
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    PracticeReleaseStatus,
    QuestionDifficulty,
    QuestionFormat,
)
from app.schemas.practice_review_contract import (
    AnswerStatus,
    ConversationalReviewPayload,
    DimensionLevel,
    PracticeAnswerReviewDto,
    ReviewLifecycleStatus,
)
from app.schemas.practice_session_contract import (
    PracticeConversationAnswerRequest,
    PracticeMcqAnswerRequest,
    PracticeReviewRetryRequest,
)
from app.services.ai_question_set_service import (
    approve_ai_question_set,
    generate_ai_questions_by_job_id,
    generate_mcq_slots_for_template,
    update_ai_question_slot,
)
from app.schemas.ai_question_set import AiQuestionSlotUpdateRequest
from app.services.candidate_practice_service import release_practice_to_candidate
from app.services.practice_review_service import (
    extract_answer_segments,
    is_deterministic_insufficient_response,
    validate_conversational_review,
)
from app.services.practice_session_service import (
    advance_practice_question,
    get_practice_session_state,
    retry_answer_review,
    start_or_resume_practice_session,
    submit_conversational_answer,
    submit_mcq_answer,
)
from app.services.wording_provider import FakeWordingProvider


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
def test_recruiter(test_db: Session) -> UserRecord:
    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email=f"recruiter_{uuid.uuid4().hex[:8]}@example.com",
        display_name="Test Recruiter",
        password_hash="hashed_secret",
        role=UserRole.RECRUITER,
        is_active=True,
    )
    test_db.add(recruiter)
    test_db.commit()
    test_db.refresh(recruiter)
    return recruiter


@pytest.fixture
def test_job_and_version(test_db: Session, test_recruiter: UserRecord):
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=test_recruiter.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(job)
    test_db.flush()

    ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        title="Senior Python Backend Engineer",
        created_by_user_id=test_recruiter.user_id,
    )
    test_db.add(ver)
    test_db.flush()

    req1 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        label="PostgreSQL Optimization",
        normalized_label="postgresql optimization",
        category="SKILL",
        necessity="MUST_HAVE",
        priority="HIGH",
        display_order=0,
    )
    req2 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        label="FastAPI & Async Architecture",
        normalized_label="fastapi & async architecture",
        category="SKILL",
        necessity="MUST_HAVE",
        priority="HIGH",
        display_order=1,
    )
    test_db.add_all([req1, req2])
    test_db.commit()
    test_db.refresh(job)
    test_db.refresh(ver)
    return job, ver, [req1, req2]


@pytest.fixture
def test_candidate(test_db: Session, test_recruiter: UserRecord):
    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="resume.pdf",
        detected_format=ArtifactFormat.pdf,
        size_bytes=1024,
        sha256_hash="e" * 64,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    test_db.add(artifact)
    test_db.flush()

    cand = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        full_name="Alex Mercer",
        primary_email=f"candidate_{uuid.uuid4().hex[:8]}@example.com",
        identity_payload={"skills": ["Python", "PostgreSQL"]},
    )
    test_db.add(cand)
    test_db.flush()

    org_id = uuid.uuid4()
    invitation = CandidateInvitationRecord(
        invitation_id=uuid.uuid4(),
        organization_id=org_id,
        candidate_id=cand.identity_id,
        token_hash="a" * 64,
        expires_at=datetime.now(timezone.utc),
        created_by_user_id=test_recruiter.user_id,
    )
    test_db.add(invitation)
    test_db.flush()

    cand_user = UserRecord(
        user_id=uuid.uuid4(),
        email=cand.primary_email,
        display_name="Alex Mercer",
        password_hash="hashed_secret",
        role=UserRole.CANDIDATE,
        is_active=True,
    )
    test_db.add(cand_user)
    test_db.flush()

    binding = CandidateAccountBindingRecord(
        binding_id=uuid.uuid4(),
        user_id=cand_user.user_id,
        candidate_id=cand.identity_id,
        organization_id=org_id,
        invitation_id=invitation.invitation_id,
        status=CandidateBindingStatus.ACTIVE.value,
    )
    test_db.add(binding)
    test_db.commit()
    test_db.refresh(cand)
    test_db.refresh(cand_user)
    return cand, cand_user


@pytest.fixture
async def setup_practice_session(test_db: Session, test_recruiter: UserRecord, test_candidate, test_job_and_version):
    job, ver, reqs = test_job_and_version
    cand, cand_user = test_candidate
    fake_provider = FakeWordingProvider(
        simulated_mode="SUCCESS",
        reworded_text_override="Describe how you optimize PostgreSQL database queries and indexes.",
    )

    detail = await generate_ai_questions_by_job_id(
        actor=test_recruiter,
        job_id=job.job_id,
        force_regenerate=False,
        db=test_db,
        custom_provider=fake_provider,
    )

    update_ai_question_slot(
        actor=test_recruiter,
        template_id=detail.ai_set_id,
        slot_id=detail.slots[1].slot_id,
        payload=AiQuestionSlotUpdateRequest(
            decision="EDIT",
            wording="Explain how you handle async event loops and query execution in FastAPI.",
        ),
        db=test_db,
    )

    gen_payload = McqGenerateRequest(count=1, difficulty=QuestionDifficulty.INTERMEDIATE)
    await generate_mcq_slots_for_template(
        actor=test_recruiter,
        template_id=detail.ai_set_id,
        payload=gen_payload,
        db=test_db,
    )

    approve_ai_question_set(actor=test_recruiter, template_id=detail.ai_set_id, db=test_db)

    release_resp = release_practice_to_candidate(
        actor=test_recruiter,
        candidate_id=cand.identity_id,
        job_id=job.job_id,
        ai_set_id=detail.ai_set_id,
        db=test_db,
    )

    rel_id = release_resp["release_id"]
    if isinstance(rel_id, str):
        rel_id = uuid.UUID(rel_id)

    session_detail = start_or_resume_practice_session(
        candidate_user=cand_user,
        release_id=rel_id,
        db=test_db,
    )

    return cand_user, session_detail


# =========================================================================
# Unit Tests: Segmentation and Grounding Validation
# =========================================================================

def test_extract_answer_segments():
    text = "First paragraph describing the problem.\n\nSecond paragraph explaining the resolution."
    ans_id = uuid.uuid4()
    sess_id = uuid.uuid4()
    q_id = uuid.uuid4()
    segments = extract_answer_segments(
        text=text,
        answer_id=ans_id,
        session_id=sess_id,
        question_id=q_id,
    )
    assert len(segments) == 2
    assert "First paragraph" in segments[0].segment_text
    assert "Second paragraph" in segments[1].segment_text



def test_is_deterministic_insufficient_response():
    assert is_deterministic_insufficient_response("") is True
    assert is_deterministic_insufficient_response("   ") is True
    assert is_deterministic_insufficient_response("???!!!") is True
    assert is_deterministic_insufficient_response("idk") is True
    assert is_deterministic_insufficient_response("nothing") is True
    assert is_deterministic_insufficient_response("I used Redis caching with a 60s TTL to reduce database query load.") is False


def test_validate_conversational_review_valid():
    q_id = uuid.uuid4()
    seg_id = str(uuid.uuid4())
    payload = {
        "schema_version": "practice-answer-review.v1",
        "source_question_id": str(q_id),
        "answer_status": "ASSESSABLE",
        "acknowledgement": "You explained your index design.",
        "strengths": [
            {
                "dimension": "relevance",
                "observation": "Directly addressed the indexing problem.",
                "answer_segment_ids": [seg_id],
            }
        ],
        "improvement_areas": [
            {
                "dimension": "evidence",
                "observation": "Did not specify before-and-after query latency.",
                "answer_segment_ids": [seg_id],
            }
        ],
        "next_steps": [
            "Add measurable query time reduction metrics.",
            "Explain how the index was verified in staging.",
        ],
        "stronger_structure": "Describe the slow query, explain the partial index chosen, and cite verified p95 latency.",
        "dimension_levels": {
            "relevance": "STRONG",
            "clarity": "DEVELOPING",
            "technical_depth": "DEVELOPING",
            "evidence": "NEEDS_DETAIL",
        },
        "follow_up": {
            "needed": True,
            "question": "What measurable latency improvement did you observe after creating the index?",
        },
        "uncertainty_codes": [],
    }
    is_valid, errors, parsed = validate_conversational_review(
        payload=payload,
        source_question_id=str(q_id),
        valid_segment_ids={seg_id},
    )
    assert is_valid is True
    assert parsed is not None
    assert parsed.strengths[0].dimension == "relevance"


def test_validate_conversational_review_rejects_hallucinated_segment():
    q_id = uuid.uuid4()
    seg_id = str(uuid.uuid4())
    payload = {
        "schema_version": "practice-answer-review.v1",
        "source_question_id": str(q_id),
        "answer_status": "ASSESSABLE",
        "acknowledgement": "You explained your index design.",
        "strengths": [
            {
                "dimension": "relevance",
                "observation": "Directly addressed the indexing problem.",
                "answer_segment_ids": ["non-existent-segment-id"],
            }
        ],
        "improvement_areas": [],
        "next_steps": ["Action 1", "Action 2"],
        "stronger_structure": "Structure format.",
        "dimension_levels": {"relevance": "STRONG"},
        "follow_up": {"needed": False, "question": None},
        "uncertainty_codes": [],
    }
    is_valid, errors, parsed = validate_conversational_review(
        payload=payload,
        source_question_id=str(q_id),
        valid_segment_ids={seg_id},
    )
    assert is_valid is False
    assert any("UNKNOWN_SEGMENT_ID" in e for e in errors)


def test_validate_conversational_review_rejects_prohibited_inferences():
    q_id = uuid.uuid4()
    seg_id = str(uuid.uuid4())
    payload = {
        "schema_version": "practice-answer-review.v1",
        "source_question_id": str(q_id),
        "answer_status": "ASSESSABLE",
        "acknowledgement": "We should hire this candidate immediately.",  # Prohibited hiring recommendation
        "strengths": [],
        "improvement_areas": [],
        "next_steps": ["Action 1", "Action 2"],
        "stronger_structure": "Structure.",
        "dimension_levels": {},
        "follow_up": {"needed": False, "question": None},
        "uncertainty_codes": [],
    }
    is_valid, errors, parsed = validate_conversational_review(
        payload=payload,
        source_question_id=str(q_id),
        valid_segment_ids={seg_id},
    )
    assert is_valid is False
    assert any("PROHIBITED" in e for e in errors)


# =========================================================================
# Integration Tests: End-to-End Submission & Persistence
# =========================================================================

@pytest.mark.anyio
async def test_valid_conversational_answer_persists_answer_and_review(
    test_db: Session,
    setup_practice_session,
):
    cand_user, session = setup_practice_session
    q_id = session.active_question.question_id

    mock_llm_response = {
        "schema_version": "practice-answer-review.v1",
        "source_question_id": str(q_id),
        "answer_status": "ASSESSABLE",
        "acknowledgement": "You articulated your database indexing strategy clearly.",
        "strengths": [
            {
                "dimension": "relevance",
                "observation": "Directly addressed PostgreSQL B-Tree query indexing.",
                "answer_segment_ids": [],
            }
        ],
        "improvement_areas": [
            {
                "dimension": "evidence",
                "observation": "Did not mention testing with EXPLAIN ANALYZE.",
                "answer_segment_ids": [],
            }
        ],
        "next_steps": [
            "Cite specific EXPLAIN ANALYZE execution plan metrics.",
            "Discuss indexing trade-offs on high-write tables.",
        ],
        "stronger_structure": "State the bottleneck query, your chosen composite index, and the measured query speedup.",
        "dimension_levels": {
            "relevance": "STRONG",
            "clarity": "DEVELOPING",
            "technical_depth": "DEVELOPING",
            "evidence": "NEEDS_DETAIL",
        },
        "follow_up": {
            "needed": True,
            "question": "How did you verify using EXPLAIN ANALYZE that the new index was actually selected by the query planner?",
        },
        "uncertainty_codes": [],
    }

    parsed = ConversationalReviewPayload.model_validate(mock_llm_response)

    with patch("app.services.practice_session_service.generate_conversational_review_with_repair", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = (ReviewLifecycleStatus.REVIEW_READY, parsed, "LOCAL_OPENAI_COMPATIBLE", "qwen-4b")

        answer_text = "I investigated the slow query and added a composite B-Tree index on user_id and created_at."
        resp = await submit_conversational_answer(
            candidate_user=cand_user,
            session_id=session.session_id,
            payload=PracticeConversationAnswerRequest(
                question_id=q_id,
                answer_text=answer_text,
                idempotency_key="idemp-1",
            ),
            db=test_db,
        )

        assert resp.latest_review is not None
        assert resp.latest_review.lifecycle_status == ReviewLifecycleStatus.REVIEW_READY.value
        assert resp.latest_review.acknowledgement == "You articulated your database indexing strategy clearly."
        assert len(resp.latest_review.strengths) == 1
        assert resp.latest_review.strengths[0].dimension == "relevance"
        assert resp.latest_review.follow_up.needed is True

        # Verify database records
        db_review = test_db.query(CandidatePracticeAnswerReviewRecord).filter(
            CandidatePracticeAnswerReviewRecord.session_id == session.session_id
        ).first()
        assert db_review is not None
        assert db_review.lifecycle_status == ReviewLifecycleStatus.REVIEW_READY.value

        db_segments = test_db.query(CandidatePracticeAnswerSegmentRecord).filter(
            CandidatePracticeAnswerSegmentRecord.session_id == session.session_id
        ).all()
        assert len(db_segments) >= 1
        assert "composite B-Tree index" in db_segments[0].segment_text


@pytest.mark.anyio
async def test_insufficient_response_deterministic_handling(
    test_db: Session,
    setup_practice_session,
):
    cand_user, session = setup_practice_session
    q_id = session.active_question.question_id

    resp = await submit_conversational_answer(
        candidate_user=cand_user,
        session_id=session.session_id,
        payload=PracticeConversationAnswerRequest(
            question_id=q_id,
            answer_text="idk nothing",
            idempotency_key="idemp-insufficient",
        ),
        db=test_db,
    )

    assert resp.latest_review is not None
    assert resp.latest_review.answer_status == AnswerStatus.INSUFFICIENT_RESPONSE.value
    assert len(resp.latest_review.strengths) == 0
    assert len(resp.latest_review.improvement_areas) >= 1
    assert resp.latest_review.dimension_levels["relevance"] == DimensionLevel.NOT_DEMONSTRATED.value


@pytest.mark.anyio
async def test_idempotent_submission_does_not_duplicate_review(
    test_db: Session,
    setup_practice_session,
):
    cand_user, session = setup_practice_session
    q_id = session.active_question.question_id

    mock_llm_response = {
        "schema_version": "practice-answer-review.v1",
        "source_question_id": str(q_id),
        "answer_status": "ASSESSABLE",
        "acknowledgement": "You explained your answer.",
        "strengths": [],
        "improvement_areas": [],
        "next_steps": ["Step 1", "Step 2"],
        "stronger_structure": "Structure.",
        "dimension_levels": {"relevance": "DEVELOPING"},
        "follow_up": {"needed": False, "question": None},
        "uncertainty_codes": [],
    }
    parsed = ConversationalReviewPayload.model_validate(mock_llm_response)

    with patch("app.services.practice_session_service.generate_conversational_review_with_repair", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = (ReviewLifecycleStatus.REVIEW_READY, parsed, "LOCAL_OPENAI_COMPATIBLE", "qwen-4b")

        payload = PracticeConversationAnswerRequest(
            question_id=q_id,
            answer_text="I used connection pooling in SQLAlchemy.",
            idempotency_key="idemp-duplicate-test",
        )

        resp1 = await submit_conversational_answer(
            candidate_user=cand_user,
            session_id=session.session_id,
            payload=payload,
            db=test_db,
        )

        # Resubmit identical payload
        resp2 = await submit_conversational_answer(
            candidate_user=cand_user,
            session_id=session.session_id,
            payload=payload,
            db=test_db,
        )

        assert resp1.latest_review.review_id == resp2.latest_review.review_id
        # LLM should have only been invoked once
        assert mock_generate.call_count == 1


@pytest.mark.anyio
async def test_provider_failure_persists_answer_with_retryable_review(
    test_db: Session,
    setup_practice_session,
):
    cand_user, session = setup_practice_session
    q_id = session.active_question.question_id

    with patch("app.services.practice_session_service.generate_conversational_review_with_repair", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = (ReviewLifecycleStatus.REVIEW_FAILED_RETRYABLE, None, "LOCAL_OPENAI_COMPATIBLE", "qwen-4b")

        resp = await submit_conversational_answer(
            candidate_user=cand_user,
            session_id=session.session_id,
            payload=PracticeConversationAnswerRequest(
                question_id=q_id,
                answer_text="I tuned work_mem and shared_buffers in postgresql.conf.",
                idempotency_key="idemp-failure-test",
            ),
            db=test_db,
        )

        # Submitted answer must be preserved
        assert resp.latest_review is not None
        assert resp.latest_review.lifecycle_status == ReviewLifecycleStatus.REVIEW_FAILED_RETRYABLE.value
        assert resp.latest_review.answer_status == AnswerStatus.REVIEW_UNAVAILABLE.value

        # Retry review successfully
        mock_valid_llm = {
            "schema_version": "practice-answer-review.v1",
            "source_question_id": str(q_id),
            "answer_status": "ASSESSABLE",
            "acknowledgement": "You configured PostgreSQL memory parameters.",
            "strengths": [
                {
                    "dimension": "technical_depth",
                    "observation": "Appropriately identified work_mem and shared_buffers.",
                    "answer_segment_ids": [],
                }
            ],
            "improvement_areas": [],
            "next_steps": ["Explain cache hit ratio.", "Describe OS buffer interactions."],
            "stronger_structure": "Structure.",
            "dimension_levels": {"technical_depth": "STRONG"},
            "follow_up": {"needed": False, "question": None},
            "uncertainty_codes": [],
        }
        parsed = ConversationalReviewPayload.model_validate(mock_valid_llm)
        mock_generate.return_value = (ReviewLifecycleStatus.REVIEW_READY, parsed, "LOCAL_OPENAI_COMPATIBLE", "qwen-4b")

        retry_resp = await retry_answer_review(
            candidate_user=cand_user,
            session_id=session.session_id,
            payload=PracticeReviewRetryRequest(
                answer_id=resp.latest_review.answer_id,
                idempotency_key="idemp-retry-1",
            ),
            db=test_db,
        )
        assert retry_resp.latest_review.lifecycle_status == ReviewLifecycleStatus.REVIEW_READY.value
        assert retry_resp.latest_review.retry_count == 1


@pytest.mark.anyio
async def test_bounded_follow_up_and_question_advancement(
    test_db: Session,
    setup_practice_session,
):
    cand_user, session = setup_practice_session
    q_id = session.active_question.question_id

    mock_llm_response = {
        "schema_version": "practice-answer-review.v1",
        "source_question_id": str(q_id),
        "answer_status": "ASSESSABLE",
        "acknowledgement": "Good explanation.",
        "strengths": [],
        "improvement_areas": [],
        "next_steps": ["Step 1", "Step 2"],
        "stronger_structure": "Structure.",
        "dimension_levels": {},
        "follow_up": {
            "needed": True,
            "question": "Can you elaborate on your indexing strategy?",
        },
        "uncertainty_codes": [],
    }
    parsed = ConversationalReviewPayload.model_validate(mock_llm_response)

    with patch("app.services.practice_session_service.generate_conversational_review_with_repair", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = (ReviewLifecycleStatus.REVIEW_READY, parsed, "LOCAL_OPENAI_COMPATIBLE", "qwen-4b")

        # 1. Answer primary question
        resp1 = await submit_conversational_answer(
            candidate_user=cand_user,
            session_id=session.session_id,
            payload=PracticeConversationAnswerRequest(
                question_id=q_id,
                answer_text="Primary answer on indexing.",
                idempotency_key="turn-1",
            ),
            db=test_db,
        )

        assert resp1.latest_review.follow_up.needed is True

        # 2. Candidate advances
        adv_resp = advance_practice_question(
            candidate_user=cand_user,
            session_id=session.session_id,
            db=test_db,
        )

        # Should advance to Question 2 of conversation
        assert adv_resp.current_conv_index == 1
        assert adv_resp.active_question.question_id != q_id


@pytest.mark.anyio
async def test_mcq_answer_evaluates_correctness_server_side(
    test_db: Session,
    setup_practice_session,
):
    cand_user, session = setup_practice_session
    # Fast-forward to MCQ section
    session_rec = test_db.query(CandidatePracticeSessionRecord).filter(
        CandidatePracticeSessionRecord.session_id == session.session_id
    ).first()
    session_rec.current_section = "MCQ"
    test_db.commit()

    state = get_practice_session_state(candidate_user=cand_user, session_id=session.session_id, db=test_db)
    assert state.current_section == "MCQ"
    mcq_q_id = state.active_question.question_id

    # Verify active question options do NOT leak the answer key
    for opt in state.active_question.options:
        assert not hasattr(opt, "is_correct")
        assert not hasattr(opt, "correct_option_id")

    # Submit MCQ answer
    selected_opt = state.active_question.options[0].option_id
    mcq_resp = submit_mcq_answer(
        candidate_user=cand_user,
        session_id=session.session_id,
        payload=PracticeMcqAnswerRequest(
            question_id=mcq_q_id,
            selected_option_id=selected_opt,
            idempotency_key="mcq-ans-1",
        ),
        db=test_db,
    )

    # Explanation and correctness revealed only post-submission
    assert mcq_resp.latest_mcq_review is not None
    assert isinstance(mcq_resp.latest_mcq_review.is_correct, bool)
    assert mcq_resp.latest_mcq_review.explanation is not None

