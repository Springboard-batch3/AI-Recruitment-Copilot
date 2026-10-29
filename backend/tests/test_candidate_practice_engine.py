"""Comprehensive test suite for Phase 3F.3: Candidate Practice Engine, turn state machine, follow-ups, and deterministic MCQ evaluation."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch
from uuid import UUID

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
    CandidatePracticeMcqAnswerRecord,
    CandidatePracticeMessageRecord,
    CandidatePracticeReleaseRecord,
    CandidatePracticeSessionRecord,
)
from app.models.interview import AiInterviewQuestionSetRecord, JobInterviewTemplateRecord
from app.models.job import JobLifecycleStatus, JobRequisition, JobRequirement, JobVersion
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.mcq_contract import McqOptionDto
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    PracticeMessageKind,
    PracticeMessageRole,
    PracticeReleaseStatus,
    PracticeSection,
    PracticeSessionLifecycle,
    QuestionDifficulty,
    QuestionFormat,
)
from app.schemas.practice_follow_up_contract import (
    BoundedPracticeFollowUpRequest,
    BoundedPracticeFollowUpResponse,
)
from app.schemas.practice_session_contract import (
    PracticeConversationAnswerRequest,
    PracticeMcqAnswerRequest,
    PracticeSessionStartRequest,
)
from app.services.practice_follow_up_service import (
    DEFAULT_FALLBACK_ACKNOWLEDGEMENT,
    request_practice_conversation_follow_up,
    validate_follow_up_semantics,
)
from app.services.practice_session_service import (
    abandon_practice_session,
    get_practice_session_state,
    start_or_resume_practice_session,
    submit_conversational_answer,
    submit_mcq_answer,
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
def test_candidate_bundle(test_db: Session):
    """Creates a candidate user, candidate identity, and active account binding."""
    candidate_user = UserRecord(
        user_id=uuid.uuid4(),
        email="candidate@example.com",
        display_name="Jane Doe",
        password_hash="cand_pw_hash",
        role=UserRole.CANDIDATE,
        is_active=True,
    )
    test_db.add(candidate_user)
    test_db.flush()

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

    identity = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        full_name="Jane Doe",
        primary_email="candidate@example.com",
        identity_payload={"skills": ["Python", "PostgreSQL"]},
    )
    test_db.add(identity)
    test_db.flush()

    org_id = uuid.uuid4()
    invitation = CandidateInvitationRecord(
        invitation_id=uuid.uuid4(),
        organization_id=org_id,
        candidate_id=identity.identity_id,
        token_hash="a" * 64,
        expires_at=datetime.now(timezone.utc),
        created_by_user_id=uuid.uuid4(),
    )
    test_db.add(invitation)
    test_db.flush()

    binding = CandidateAccountBindingRecord(
        binding_id=uuid.uuid4(),
        candidate_id=identity.identity_id,
        user_id=candidate_user.user_id,
        organization_id=org_id,
        invitation_id=invitation.invitation_id,
        status=CandidateBindingStatus.ACTIVE.value,
    )
    test_db.add(binding)
    test_db.commit()
    test_db.refresh(candidate_user)
    return candidate_user, identity, binding


@pytest.fixture
def test_released_job(test_db: Session, test_recruiter: UserRecord, test_candidate_bundle):
    candidate_user, identity, binding = test_candidate_bundle

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
        title="Backend Engineer",
        created_by_user_id=test_recruiter.user_id,
    )
    test_db.add(ver)
    test_db.flush()

    org_id = binding.organization_id
    template = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=1,
        template_version=1,
        lifecycle_status="APPROVED",
        content_hash="dummy_tpl_hash",
        guardrail_policy_version="1.0.0",
        guardrail_policy_hash="dummy_policy_hash",
        deterministic_builder_version="1.0.0",
        question_catalogue_version="1.0.0",
        rubric_catalogue_version="1.0.0",
        input_snapshot_hash="dummy_input_hash",
        created_by_user_id=test_recruiter.user_id,
    )
    test_db.add(template)
    test_db.flush()

    opt_a = str(uuid.uuid4())
    opt_b = str(uuid.uuid4())
    opt_c = str(uuid.uuid4())
    opt_d = str(uuid.uuid4())

    q_items = [
        {
            "question_id": str(uuid.uuid4()),
            "format": QuestionFormat.CONVERSATION.value,
            "text": "How do you optimize slow database queries in PostgreSQL?",
            "category": "technical",
            "competency": "PostgreSQL Optimization",
            "difficulty": QuestionDifficulty.INTERMEDIATE.value,
            "display_order": 1,
            "authoring_origin": AuthoringOrigin.LOCAL_LLM_GENERATED.value,
            "validation_status": "PASSED",
            "model_identifier": "qwen/qwen3-4b-2507",
        },
        {
            "question_id": str(uuid.uuid4()),
            "format": QuestionFormat.CONVERSATION.value,
            "text": "Describe how you design distributed microservices.",
            "category": "architecture",
            "competency": "System Design",
            "difficulty": QuestionDifficulty.ADVANCED.value,
            "display_order": 2,
            "authoring_origin": AuthoringOrigin.LOCAL_LLM_GENERATED.value,
            "validation_status": "PASSED",
            "model_identifier": "qwen/qwen3-4b-2507",
        },
        {
            "question_id": str(uuid.uuid4()),
            "format": QuestionFormat.MCQ_SINGLE_ANSWER.value,
            "text": "What is the primary difference between a B-Tree index and a Hash index?",
            "category": "technical",
            "competency": "Databases",
            "difficulty": QuestionDifficulty.INTERMEDIATE.value,
            "display_order": 3,
            "authoring_origin": AuthoringOrigin.LOCAL_LLM_GENERATED.value,
            "validation_status": "PASSED",
            "model_identifier": "qwen/qwen3-4b-2507",
            "options": [
                {"option_id": opt_a, "text": "B-Tree supports range queries, Hash does not", "display_order": 1},
                {"option_id": opt_b, "text": "Hash supports range queries, B-Tree does not", "display_order": 2},
                {"option_id": opt_c, "text": "They have identical query performance", "display_order": 3},
                {"option_id": opt_d, "text": "Hash is always faster for all queries", "display_order": 4},
            ],
            "correct_option_id": opt_a,
            "explanation": "B-Trees keep keys sorted, allowing range scan operations, while Hash indexes only support equality lookups.",
        },
    ]

    q_set = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        organization_id=org_id,
        template_id=template.template_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        job_revision=ver.version_number,
        provider_class="LMStudioProvider",
        model_identifier="qwen/qwen3-4b-2507",
        prompt_template_version="practice-question.v1",
        guardrail_policy_version="1.0.0",
        schema_version="practice-question.v1",
        source_template_version=1,
        source_content_hash=template.content_hash,
        set_version=1,
        lifecycle_status="APPROVED",
        is_immutable=True,
        created_by_user_id=test_recruiter.user_id,
        items=q_items,
    )
    test_db.add(q_set)
    test_db.flush()

    release = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        organization_id=org_id,
        candidate_id=identity.identity_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        ai_set_id=q_set.ai_set_id,
        released_by_user_id=test_recruiter.user_id,
        status=PracticeReleaseStatus.RELEASED.value,
    )
    test_db.add(release)
    test_db.commit()

    return {
        "job": job,
        "version": ver,
        "template": template,
        "q_set": q_set,
        "release": release,
        "q_items": q_items,
        "mcq_correct_option_id": opt_a,
        "org_id": org_id,
    }


# =========================================================================
# Unit Tests for Semantic Safety Validation
# =========================================================================

def test_semantic_validation_valid_follow_up():
    req = BoundedPracticeFollowUpRequest(
        source_question_id="q1",
        competency_name="PostgreSQL Optimization",
        question_wording="How do you optimize slow queries?",
        candidate_answer="I use explain analyze to find bottlenecks.",
        follow_up_available=True,
    )
    res = BoundedPracticeFollowUpResponse(
        action="FOLLOW_UP",
        source_question_id="q1",
        acknowledgement="That makes sense.",
        follow_up_question="Could you explain how EXPLAIN ANALYZE helps identify sequential scans?",
        validation_passed=True,
        safe_fallback_used=False,
    )
    assert validate_follow_up_semantics(res, req) is True


def test_semantic_validation_rejects_empty_or_id_mismatch():
    req = BoundedPracticeFollowUpRequest(
        source_question_id="q1",
        competency_name="PostgreSQL Optimization",
        question_wording="How do you optimize slow queries?",
        candidate_answer="I use explain analyze.",
        follow_up_available=True,
    )
    res_mismatch = BoundedPracticeFollowUpResponse(
        action="FOLLOW_UP",
        source_question_id="different_q",
        acknowledgement="That makes sense.",
        follow_up_question="Valid question?",
        validation_passed=True,
        safe_fallback_used=False,
    )
    assert validate_follow_up_semantics(res_mismatch, req) is False


def test_semantic_validation_rejects_scoring_and_rubric_leaks():
    req = BoundedPracticeFollowUpRequest(
        source_question_id="q1",
        competency_name="PostgreSQL Optimization",
        question_wording="How do you optimize slow queries?",
        candidate_answer="I use indexes.",
        follow_up_available=True,
    )
    res = BoundedPracticeFollowUpResponse(
        action="FOLLOW_UP",
        source_question_id="q1",
        acknowledgement="You scored 3 points out of 5 based on our scoring rubric.",
        follow_up_question="Can you improve your rating?",
        validation_passed=True,
        safe_fallback_used=False,
    )
    assert validate_follow_up_semantics(res, req) is False


def test_semantic_validation_rejects_prohibited_topics():
    req = BoundedPracticeFollowUpRequest(
        source_question_id="q1",
        competency_name="PostgreSQL Optimization",
        question_wording="How do you optimize slow queries?",
        candidate_answer="I use indexes.",
        follow_up_available=True,
    )
    res = BoundedPracticeFollowUpResponse(
        action="FOLLOW_UP",
        source_question_id="q1",
        acknowledgement="Got it.",
        follow_up_question="What is your current salary and compensation?",
        validation_passed=True,
        safe_fallback_used=False,
    )
    assert validate_follow_up_semantics(res, req) is False


# =========================================================================
# Unit & Integration Tests for Practice Session Service
# =========================================================================

def test_start_practice_session_success(test_db: Session, test_candidate_bundle, test_released_job):
    cand_user, identity, binding = test_candidate_bundle
    job = test_released_job["job"]
    release = test_released_job["release"]

    req = PracticeSessionStartRequest(release_id=release.release_id)
    res = start_or_resume_practice_session(candidate_user=cand_user, release_id=release.release_id, db=test_db)

    assert res.job_id == job.job_id
    assert res.lifecycle_status == PracticeSessionLifecycle.IN_PROGRESS.value
    assert res.current_section == PracticeSection.CONVERSATION.value
    assert res.current_conv_index == 0
    assert res.total_conv_count == 2
    assert res.total_mcq_count == 1
    assert res.active_question is not None
    assert res.active_question.question_format == QuestionFormat.CONVERSATION.value
    assert res.active_question.wording == "How do you optimize slow database queries in PostgreSQL?"
    assert len(res.messages) == 2
    assert res.messages[0].message_kind == PracticeMessageKind.GREETING.value
    assert res.messages[1].role == PracticeMessageRole.INTERVIEWER.value
    assert res.messages[1].message_kind == PracticeMessageKind.PRIMARY_QUESTION.value


def test_start_practice_session_resumes_existing(test_db: Session, test_candidate_bundle, test_released_job):
    cand_user, identity, binding = test_candidate_bundle
    release = test_released_job["release"]

    req1 = PracticeSessionStartRequest(release_id=release.release_id)
    res1 = start_or_resume_practice_session(candidate_user=cand_user, release_id=release.release_id, db=test_db)

    req2 = PracticeSessionStartRequest(release_id=release.release_id)
    res2 = start_or_resume_practice_session(candidate_user=cand_user, release_id=release.release_id, db=test_db)

    assert res1.session_id == res2.session_id


def test_start_practice_session_attempt_limit_enforced(test_db: Session, test_candidate_bundle, test_released_job):
    cand_user, identity, binding = test_candidate_bundle
    job = test_released_job["job"]
    release = test_released_job["release"]

    # Complete 3 sessions for this job
    for _ in range(3):
        session = CandidatePracticeSessionRecord(
            session_id=uuid.uuid4(),
            organization_id=test_released_job["org_id"],
            binding_id=binding.binding_id,
            candidate_id=identity.identity_id,
            release_id=release.release_id,
            job_id=job.job_id,
            job_version_id=test_released_job["version"].job_version_id,
            ai_set_id=test_released_job["q_set"].ai_set_id,
            lifecycle_status=PracticeSessionLifecycle.COMPLETED.value,
            current_section=PracticeSection.COMPLETE.value,
            current_conv_index=2,
            current_mcq_index=1,
            questions_snapshot=test_released_job["q_items"],
            snapshot_hash="a" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            started_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        test_db.add(session)
    test_db.commit()

    req = PracticeSessionStartRequest(release_id=release.release_id)
    with pytest.raises(HTTPException) as exc:
        start_or_resume_practice_session(candidate_user=cand_user, release_id=release.release_id, db=test_db)
    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "ATTEMPT_LIMIT_REACHED"


def test_get_session_state_and_isolation(test_db: Session, test_candidate_bundle, test_released_job):
    cand_user, identity, binding = test_candidate_bundle
    release = test_released_job["release"]

    req = PracticeSessionStartRequest(release_id=release.release_id)
    session_dto = start_or_resume_practice_session(candidate_user=cand_user, release_id=release.release_id, db=test_db)

    # Candidate can view state
    state = get_practice_session_state(candidate_user=cand_user, session_id=session_dto.session_id, db=test_db)
    assert state.session_id == session_dto.session_id

    # Another candidate cannot view state
    other_user = UserRecord(
        user_id=uuid.uuid4(),
        email="other@example.com",
        display_name="Other Candidate",
        password_hash="other_pw",
        role=UserRole.CANDIDATE,
        is_active=True,
    )
    test_db.add(other_user)
    other_artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="other.pdf",
        detected_format=ArtifactFormat.pdf,
        size_bytes=1024,
        sha256_hash="f" * 64,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    test_db.add(other_artifact)
    test_db.flush()

    other_identity = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=other_artifact.artifact_id,
        primary_email="other@example.com",
        full_name="Other Candidate",
        identity_payload={},
    )
    test_db.add(other_identity)
    test_db.flush()

    other_org_id = uuid.uuid4()
    other_invitation = CandidateInvitationRecord(
        invitation_id=uuid.uuid4(),
        organization_id=other_org_id,
        candidate_id=other_identity.identity_id,
        token_hash="b" * 64,
        expires_at=datetime.now(timezone.utc),
        created_by_user_id=uuid.uuid4(),
    )
    test_db.add(other_invitation)
    test_db.flush()

    other_binding = CandidateAccountBindingRecord(
        binding_id=uuid.uuid4(),
        candidate_id=other_identity.identity_id,
        user_id=other_user.user_id,
        organization_id=other_org_id,
        invitation_id=other_invitation.invitation_id,
        status=CandidateBindingStatus.ACTIVE.value,
    )
    test_db.add(other_binding)
    test_db.commit()

    with pytest.raises(HTTPException) as exc:
        get_practice_session_state(candidate_user=other_user, session_id=session_dto.session_id, db=test_db)
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_conversational_flow_with_ai_follow_up(test_db: Session, test_candidate_bundle, test_released_job):
    cand_user, identity, binding = test_candidate_bundle
    release = test_released_job["release"]
    q0_id = UUID(test_released_job["q_items"][0]["question_id"])

    req = PracticeSessionStartRequest(release_id=release.release_id)
    session = start_or_resume_practice_session(candidate_user=cand_user, release_id=release.release_id, db=test_db)

    # Mock review service to return a follow-up
    from app.schemas.practice_review_contract import ConversationalReviewPayload, FollowUpItem, AnswerStrengthItem, ImprovementAreaItem, DimensionLevel
    mock_review_payload = ConversationalReviewPayload(
        source_question_id=str(q0_id),
        acknowledgement="Thank you for explaining that.",
        strengths=[AnswerStrengthItem(dimension="relevance", observation="Good explanation", answer_segment_ids=["segment-1"])],
        improvement_areas=[ImprovementAreaItem(dimension="evidence_depth", observation="Add depth", answer_segment_ids=["segment-1"])],
        next_steps=["Explain your exact action", "Add one measurable result"],
        stronger_structure="State problem, action, and verified outcome.",
        dimension_levels={"relevance": DimensionLevel.STRONG, "clarity": DimensionLevel.STRONG, "technical_depth": DimensionLevel.DEVELOPING, "evidence": DimensionLevel.NEEDS_DETAIL},
        follow_up=FollowUpItem(needed=True, question="How would you use composite indexes to speed up multi-column filtering?"),
    )

    from app.schemas.practice_review_contract import ReviewLifecycleStatus
    with patch("app.services.practice_session_service.generate_conversational_review_with_repair", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = (ReviewLifecycleStatus.REVIEW_READY, mock_review_payload, None, "qwen3-4b-mlx")

        # Candidate submits answer 1 (turn 0 -> candidate answer + interviewer follow-up)
        ans_req1 = PracticeConversationAnswerRequest(
            question_id=q0_id,
            answer_text="I usually run EXPLAIN ANALYZE on slow queries to identify sequential scans.",
            idempotency_key=str(uuid.uuid4()),
        )
        res1 = await submit_conversational_answer(
            candidate_user=cand_user,
            session_id=session.session_id,
            payload=ans_req1,
            db=test_db,
        )

        assert res1.current_conv_index == 0
        assert len(res1.messages) == 4
        assert res1.messages[2].role == PracticeMessageRole.CANDIDATE.value
        assert res1.messages[2].message_kind == PracticeMessageKind.ANSWER.value
        assert res1.messages[3].role == PracticeMessageRole.INTERVIEWER.value
        assert res1.messages[3].message_kind == PracticeMessageKind.FOLLOW_UP.value
        assert res1.messages[3].text == "How would you use composite indexes to speed up multi-column filtering?"

        # Candidate submits follow-up answer (turn 1 -> candidate answer + advance to Q1 via advance_practice_question)
        ans_req2 = PracticeConversationAnswerRequest(
            question_id=q0_id,
            answer_text="I create composite indexes with the most selective columns first.",
            idempotency_key=str(uuid.uuid4()),
        )
        res2 = await submit_conversational_answer(
            candidate_user=cand_user,
            session_id=session.session_id,
            payload=ans_req2,
            db=test_db,
        )

        assert len(res2.messages) == 5
        assert res2.messages[4].role == PracticeMessageRole.CANDIDATE.value

        # Now advance to next question
        from app.services.practice_session_service import advance_practice_question
        from app.schemas.practice_session_contract import PracticeAdvanceQuestionRequest
        adv_res = advance_practice_question(
            candidate_user=cand_user,
            session_id=session.session_id,
            payload=PracticeAdvanceQuestionRequest(question_id=q0_id),
            db=test_db,
        )
        assert adv_res.current_conv_index == 1
        assert adv_res.active_question is not None
        assert adv_res.active_question.wording == "Describe how you design distributed microservices."


@pytest.mark.anyio
async def test_conversational_flow_fallback_when_ai_disabled(test_db: Session, test_candidate_bundle, test_released_job):
    cand_user, identity, binding = test_candidate_bundle
    release = test_released_job["release"]
    q0_id = UUID(test_released_job["q_items"][0]["question_id"])

    req = PracticeSessionStartRequest(release_id=release.release_id)
    session = start_or_resume_practice_session(candidate_user=cand_user, release_id=release.release_id, db=test_db)

    # When AI returns should_follow_up=False (e.g. disabled or offline)
    from app.schemas.practice_review_contract import ConversationalReviewPayload, FollowUpItem, DimensionLevel, ReviewLifecycleStatus
    mock_no_follow_up = ConversationalReviewPayload(
        source_question_id=str(q0_id),
        acknowledgement="Thank you for sharing your thoughts.",
        strengths=[],
        improvement_areas=[],
        next_steps=["Explain your exact action", "Add one measurable result"],
        stronger_structure="State problem, action, and verified outcome.",
        dimension_levels={"relevance": DimensionLevel.DEVELOPING, "clarity": DimensionLevel.DEVELOPING, "technical_depth": DimensionLevel.NEEDS_DETAIL, "evidence": DimensionLevel.NEEDS_DETAIL},
        follow_up=FollowUpItem(needed=False, question=None),
    )

    with patch("app.services.practice_session_service.generate_conversational_review_with_repair", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = (ReviewLifecycleStatus.REVIEW_READY, mock_no_follow_up, None, "qwen3-4b-mlx")

        ans_req = PracticeConversationAnswerRequest(
            question_id=q0_id,
            answer_text="I check the query plan and vacuum analyze the table.",
            idempotency_key=str(uuid.uuid4()),
        )
        res = await submit_conversational_answer(
            candidate_user=cand_user,
            session_id=session.session_id,
            payload=ans_req,
            db=test_db,
        )

        assert res.current_conv_index == 0
        assert res.latest_review is not None
        assert res.latest_review.acknowledgement == "Thank you for sharing your thoughts."


@pytest.mark.anyio
async def test_full_practice_session_transition_to_mcq_and_completion(test_db: Session, test_candidate_bundle, test_released_job):
    cand_user, identity, binding = test_candidate_bundle
    release = test_released_job["release"]
    q0_id = UUID(test_released_job["q_items"][0]["question_id"])
    q1_id = UUID(test_released_job["q_items"][1]["question_id"])

    req = PracticeSessionStartRequest(release_id=release.release_id)
    session = start_or_resume_practice_session(candidate_user=cand_user, release_id=release.release_id, db=test_db)

    from app.schemas.practice_review_contract import ConversationalReviewPayload, FollowUpItem, DimensionLevel, ReviewLifecycleStatus
    mock_no_follow_up_q0 = ConversationalReviewPayload(
        source_question_id=str(q0_id),
        acknowledgement="Understood.",
        strengths=[],
        improvement_areas=[],
        next_steps=["Step 1 action", "Step 2 action"],
        stronger_structure="Action and result.",
        dimension_levels={"relevance": DimensionLevel.DEVELOPING, "clarity": DimensionLevel.DEVELOPING, "technical_depth": DimensionLevel.NEEDS_DETAIL, "evidence": DimensionLevel.NEEDS_DETAIL},
        follow_up=FollowUpItem(needed=False, question=None),
    )
    mock_no_follow_up_q1 = ConversationalReviewPayload(
        source_question_id=str(q1_id),
        acknowledgement="Understood.",
        strengths=[],
        improvement_areas=[],
        next_steps=["Step 1 action", "Step 2 action"],
        stronger_structure="Action and result.",
        dimension_levels={"relevance": DimensionLevel.DEVELOPING, "clarity": DimensionLevel.DEVELOPING, "technical_depth": DimensionLevel.NEEDS_DETAIL, "evidence": DimensionLevel.NEEDS_DETAIL},
        follow_up=FollowUpItem(needed=False, question=None),
    )

    with patch("app.services.practice_session_service.generate_conversational_review_with_repair", new_callable=AsyncMock) as mock_ai:
        mock_ai.side_effect = [
            (ReviewLifecycleStatus.REVIEW_READY, mock_no_follow_up_q0, None, "qwen3-4b-mlx"),
            (ReviewLifecycleStatus.REVIEW_READY, mock_no_follow_up_q1, None, "qwen3-4b-mlx"),
        ]

        # Q0 Answer
        await submit_conversational_answer(
            candidate_user=cand_user,
            session_id=session.session_id,
            payload=PracticeConversationAnswerRequest(question_id=q0_id, answer_text="Answer to Q0", idempotency_key=str(uuid.uuid4())),
            db=test_db,
        )

        from app.services.practice_session_service import advance_practice_question
        from app.schemas.practice_session_contract import PracticeAdvanceQuestionRequest
        advance_practice_question(
            candidate_user=cand_user,
            session_id=session.session_id,
            payload=PracticeAdvanceQuestionRequest(question_id=q0_id),
            db=test_db,
        )

        # Q1 Answer -> then advance should transition section to MCQ
        await submit_conversational_answer(
            candidate_user=cand_user,
            session_id=session.session_id,
            payload=PracticeConversationAnswerRequest(question_id=q1_id, answer_text="Answer to Q1", idempotency_key=str(uuid.uuid4())),
            db=test_db,
        )

        mcq_entry_res = advance_practice_question(
            candidate_user=cand_user,
            session_id=session.session_id,
            payload=PracticeAdvanceQuestionRequest(question_id=q1_id),
            db=test_db,
        )

        assert mcq_entry_res.current_section == PracticeSection.MCQ.value
        assert mcq_entry_res.current_mcq_index == 0
        assert mcq_entry_res.active_question is not None
        assert mcq_entry_res.active_question.question_format == QuestionFormat.MCQ_SINGLE_ANSWER.value
        assert mcq_entry_res.active_question.options is not None
        assert len(mcq_entry_res.active_question.options) == 4

        # Verify MCQ question NEVER leaks correct_option_id or explanation
        active_q_dict = mcq_entry_res.active_question.model_dump()
        assert "correct_option_id" not in active_q_dict
        assert "explanation" not in active_q_dict

        # Answer MCQ with correct option
        correct_opt = test_released_job["mcq_correct_option_id"]
        mcq_ans_req = PracticeMcqAnswerRequest(
            question_id=mcq_entry_res.active_question.question_id,
            selected_option_id=correct_opt,
            idempotency_key=str(uuid.uuid4()),
        )
        completed_res = submit_mcq_answer(
            candidate_user=cand_user,
            session_id=session.session_id,
            payload=mcq_ans_req,
            db=test_db,
        )

        # Session is now COMPLETED
        assert completed_res.lifecycle_status == PracticeSessionLifecycle.COMPLETED.value
        assert completed_res.current_section == PracticeSection.COMPLETE.value
        assert completed_res.active_question is None
        assert completed_res.completed_at is not None

        # Verify database record evaluated deterministically
        db_mcq_answer = test_db.query(CandidatePracticeMcqAnswerRecord).filter_by(session_id=session.session_id).first()
        assert db_mcq_answer is not None
        assert db_mcq_answer.is_correct is True


def test_mcq_invalid_option_rejected(test_db: Session, test_candidate_bundle, test_released_job):
    cand_user, identity, binding = test_candidate_bundle
    release = test_released_job["release"]

    req = PracticeSessionStartRequest(release_id=release.release_id)
    session = start_or_resume_practice_session(candidate_user=cand_user, release_id=release.release_id, db=test_db)

    # Advance session to MCQ section directly in DB
    db_session = test_db.query(CandidatePracticeSessionRecord).filter_by(session_id=session.session_id).first()
    db_session.current_section = PracticeSection.MCQ.value
    db_session.current_mcq_index = 0
    test_db.commit()

    invalid_opt_id = str(uuid.uuid4())
    mcq_req = PracticeMcqAnswerRequest(
        question_id=UUID(test_released_job["q_items"][2]["question_id"]),
        selected_option_id=invalid_opt_id,
        idempotency_key=str(uuid.uuid4()),
    )

    with pytest.raises(HTTPException) as exc:
        submit_mcq_answer(candidate_user=cand_user, session_id=session.session_id, payload=mcq_req, db=test_db)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_OPTION_ID"


def test_abandon_practice_session(test_db: Session, test_candidate_bundle, test_released_job):
    cand_user, identity, binding = test_candidate_bundle
    release = test_released_job["release"]

    req = PracticeSessionStartRequest(release_id=release.release_id)
    session = start_or_resume_practice_session(candidate_user=cand_user, release_id=release.release_id, db=test_db)

    abandon_res = abandon_practice_session(candidate_user=cand_user, session_id=session.session_id, db=test_db)
    assert abandon_res.session_id == session.session_id
    assert abandon_res.lifecycle_status == PracticeSessionLifecycle.ABANDONED.value

    # Query state again
    state = get_practice_session_state(candidate_user=cand_user, session_id=session.session_id, db=test_db)
    assert state.lifecycle_status == PracticeSessionLifecycle.ABANDONED.value
    assert state.active_question is None
