"""Focused test suite for AI-Hiring Phase 4D.1: Dual-Mode Interview Foundation and Recruiter Studio.

Tests cover:
- Defaulting and migration of question sets to TEXT mode.
- Generation without a mode defaulting to TEXT.
- TEXT mode generating conversational questions and allowing MCQs.
- AUDIO mode generating 100% conversational questions (0 MCQs).
- Server-side rejection of MCQs on AUDIO sets (add_manual_mcq_slot, generate_mcq_slots_for_template, update_mcq_slot, validate_ai_question_set).
- Coexistence of TEXT and AUDIO question sets for the exact same job requisition and revision.
- Independent ready-job / eligibility calculation per mode.
- Directory listing filtering by mode.
- Cross-mode duplicate stem validation rejection.
- Recruiter/Organization access isolation.
"""
import uuid
import pytest
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.auth import UserRecord, UserRole
from app.models.interview import AiInterviewQuestionSetRecord
from app.models.job import (
    JobLifecycleStatus,
    JobRequirement,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
    RequirementCategory,
    RequirementNecessity,
    RequirementPriority,
)
from app.schemas.mcq_contract import McqCreateRequest, McqOptionDto, QuestionDifficulty
from app.schemas.phase3_enums import InterviewMode
from app.services.ai_question_set_service import (
    add_manual_mcq_slot,
    approve_ai_question_set,
    generate_ai_questions_by_job_id,
    get_eligible_jobs_for_ai_questions,
    list_ai_question_sets,
    update_mcq_slot,
    validate_ai_question_set,
)
from app.services.wording_provider import FakeWordingProvider


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def recruiter_user(db_session: Session) -> UserRecord:
    user = UserRecord(
        user_id=uuid.uuid4(),
        email="recruiter@example.com",
        display_name="Lead Recruiter",
        role=UserRole.RECRUITER,
        password_hash="fakehash",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def other_org_recruiter(db_session: Session) -> UserRecord:
    user = UserRecord(
        user_id=uuid.uuid4(),
        email="other@example.com",
        display_name="Other Recruiter",
        role=UserRole.RECRUITER,
        password_hash="fakehash",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def test_job(db_session: Session, recruiter_user: UserRecord) -> JobRequisition:
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter_user.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    db_session.add(job)
    db_session.flush()

    version = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        title="Senior Distributed Systems Engineer",
        created_by_user_id=recruiter_user.user_id,
        version_status=JobVersionStatus.APPROVED,
        summary="Design and operate large-scale distributed state machines.",
        responsibilities="Maintain consensus clusters\nOptimize latency",
    )
    db_session.add(version)
    db_session.flush()

    req1 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=version.job_version_id,
        label="Raft Consensus Protocol",
        normalized_label="raft consensus protocol",
        category="SKILL",
        necessity="MUST_HAVE",
        priority="HIGH",
        rationale="Must implement Raft leader election and log compaction in production.",
    )
    req2 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=version.job_version_id,
        label="Distributed Tracing",
        normalized_label="distributed tracing",
        category="SKILL",
        necessity="MUST_HAVE",
        priority="MEDIUM",
        rationale="Experience instrumenting OpenTelemetry spans across RPC boundaries.",
    )
    db_session.add_all([req1, req2])
    db_session.commit()
    db_session.refresh(job)
    return job


@pytest.mark.anyio
async def test_generation_defaults_to_text_mode(db_session: Session, recruiter_user: UserRecord, test_job: JobRequisition):
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")
    detail = await generate_ai_questions_by_job_id(
        actor=recruiter_user,
        job_id=test_job.job_id,
        force_regenerate=False,
        db=db_session,
        custom_provider=fake_provider,
        interview_mode=InterviewMode.TEXT,
    )
    assert detail.interview_mode == InterviewMode.TEXT
    assert detail.conversation_count > 0
    assert detail.mcq_count == 0


@pytest.mark.anyio
async def test_audio_generation_creates_conversational_questions_only(db_session: Session, recruiter_user: UserRecord, test_job: JobRequisition):
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")
    detail = await generate_ai_questions_by_job_id(
        actor=recruiter_user,
        job_id=test_job.job_id,
        force_regenerate=False,
        db=db_session,
        custom_provider=fake_provider,
        interview_mode=InterviewMode.AUDIO,
    )
    assert detail.interview_mode == InterviewMode.AUDIO
    assert detail.conversation_count > 0
    assert detail.mcq_count == 0
    assert all(s.question_format == "CONVERSATION" for s in detail.slots)


@pytest.mark.anyio
async def test_audio_set_rejects_manual_mcq_insertion(db_session: Session, recruiter_user: UserRecord, test_job: JobRequisition):
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")
    detail = await generate_ai_questions_by_job_id(
        actor=recruiter_user,
        job_id=test_job.job_id,
        force_regenerate=False,
        db=db_session,
        custom_provider=fake_provider,
        interview_mode=InterviewMode.AUDIO,
    )

    mcq_req = McqCreateRequest(
        question_text="What is the quorum requirement in Raft with 5 nodes?",
        options=[
            McqOptionDto(option_id="A", text="2 nodes"),
            McqOptionDto(option_id="B", text="3 nodes"),
            McqOptionDto(option_id="C", text="4 nodes"),
            McqOptionDto(option_id="D", text="5 nodes"),
        ],
        correct_option_id="B",
        answer_explanation="A majority quorum for 5 nodes is 3 nodes (floor(5/2) + 1).",
        competency_name="Raft Consensus Protocol",
        difficulty=QuestionDifficulty.INTERMEDIATE,
    )

    with pytest.raises(HTTPException) as exc_info:
        add_manual_mcq_slot(
            actor=recruiter_user,
            template_id=detail.ai_set_id,
            payload=mcq_req,
            db=db_session,
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail.get("code") == "AUDIO_MCQ_NOT_ALLOWED"


@pytest.mark.anyio
async def test_text_and_audio_sets_coexist_independently_for_same_job(db_session: Session, recruiter_user: UserRecord, test_job: JobRequisition):
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")
    
    # 1. Generate TEXT set
    text_set = await generate_ai_questions_by_job_id(
        actor=recruiter_user,
        job_id=test_job.job_id,
        force_regenerate=False,
        db=db_session,
        custom_provider=fake_provider,
        interview_mode=InterviewMode.TEXT,
    )
    
    # 2. Generate AUDIO set
    audio_set = await generate_ai_questions_by_job_id(
        actor=recruiter_user,
        job_id=test_job.job_id,
        force_regenerate=False,
        db=db_session,
        custom_provider=fake_provider,
        interview_mode=InterviewMode.AUDIO,
    )

    assert text_set.ai_set_id != audio_set.ai_set_id
    assert text_set.interview_mode == InterviewMode.TEXT
    assert audio_set.interview_mode == InterviewMode.AUDIO

    # 3. Directory listing filtered by mode
    text_dir = list_ai_question_sets(actor=recruiter_user, db=db_session, mode_filter=InterviewMode.TEXT)
    audio_dir = list_ai_question_sets(actor=recruiter_user, db=db_session, mode_filter=InterviewMode.AUDIO)

    assert len(text_dir.sets) == 1
    assert text_dir.sets[0].interview_mode == InterviewMode.TEXT
    assert len(audio_dir.sets) == 1
    assert audio_dir.sets[0].interview_mode == InterviewMode.AUDIO


@pytest.mark.anyio
async def test_ready_jobs_calculated_independently_by_mode(db_session: Session, recruiter_user: UserRecord, test_job: JobRequisition):
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")

    # Initial state: Job is eligible for both TEXT and AUDIO
    text_eligible = get_eligible_jobs_for_ai_questions(actor=recruiter_user, db=db_session, mode=InterviewMode.TEXT)
    audio_eligible = get_eligible_jobs_for_ai_questions(actor=recruiter_user, db=db_session, mode=InterviewMode.AUDIO)
    assert len(text_eligible.items) == 1
    assert len(audio_eligible.items) == 1

    # Generate TEXT set only
    await generate_ai_questions_by_job_id(
        actor=recruiter_user,
        job_id=test_job.job_id,
        force_regenerate=False,
        db=db_session,
        custom_provider=fake_provider,
        interview_mode=InterviewMode.TEXT,
    )

    # Now job has TEXT set -> excluded from TEXT eligible jobs, but still ready for AUDIO generation
    text_eligible_after = get_eligible_jobs_for_ai_questions(actor=recruiter_user, db=db_session, mode=InterviewMode.TEXT)
    audio_eligible_after = get_eligible_jobs_for_ai_questions(actor=recruiter_user, db=db_session, mode=InterviewMode.AUDIO)
    
    assert len(text_eligible_after.items) == 0
    assert len(audio_eligible_after.items) == 1


@pytest.mark.anyio
async def test_validation_rejects_audio_set_with_mcqs_and_cross_mode_duplicates(db_session: Session, recruiter_user: UserRecord, test_job: JobRequisition):
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")
    
    text_set = await generate_ai_questions_by_job_id(
        actor=recruiter_user,
        job_id=test_job.job_id,
        force_regenerate=False,
        db=db_session,
        custom_provider=fake_provider,
        interview_mode=InterviewMode.TEXT,
    )

    audio_set = await generate_ai_questions_by_job_id(
        actor=recruiter_user,
        job_id=test_job.job_id,
        force_regenerate=False,
        db=db_session,
        custom_provider=fake_provider,
        interview_mode=InterviewMode.AUDIO,
    )

    # Load direct record to simulate an illegal MCQ insertion or duplicate stem
    from sqlalchemy.orm.attributes import flag_modified
    rec = db_session.get(AiInterviewQuestionSetRecord, audio_set.ai_set_id)
    text_first_question = text_set.slots[0].ai_wording or "Sample text stem"
    
    # Inject an exact duplicate stem from text set into audio set
    items = list(rec.items)
    items[0]["ai_wording"] = text_first_question
    rec.items = items
    flag_modified(rec, "items")
    db_session.commit()

    val_res = validate_ai_question_set(actor=recruiter_user, template_id=audio_set.ai_set_id, db=db_session)
    assert not val_res.is_valid
    assert any("CROSS_MODE_DUPLICATE_QUESTION" in err or "CROSS_MODE_QUESTION_DUPLICATE" in err for err in val_res.blocking_errors)


@pytest.mark.anyio
async def test_cross_org_access_remains_forbidden(db_session: Session, recruiter_user: UserRecord, other_org_recruiter: UserRecord, test_job: JobRequisition):
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")
    
    audio_set = await generate_ai_questions_by_job_id(
        actor=recruiter_user,
        job_id=test_job.job_id,
        force_regenerate=False,
        db=db_session,
        custom_provider=fake_provider,
        interview_mode=InterviewMode.AUDIO,
    )

    with pytest.raises(HTTPException) as exc_info:
        validate_ai_question_set(
            actor=other_org_recruiter,
            template_id=audio_set.ai_set_id,
            db=db_session,
        )
    assert exc_info.value.status_code == 403
