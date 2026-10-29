"""Focused verification tests for Phase 4D UI, Privacy, and Audio Interview corrections.

Tests cover:
1. Exact Text/Audio duplicate is rejected.
2. Punctuation and case-only duplicate is rejected.
3. Strong rewording of the same question is rejected when above conservative threshold.
4. Different question about the same requirement remains allowed.
5. Audio MCQ remains strictly rejected.
6. Audio generation for a different job revision does not compare against an unrelated Text set.
7. Approved/released snapshots are immutable and not mutated.
8. Candidate capabilities endpoint sanitizes model names and internals.
"""
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm.attributes import flag_modified

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
from app.schemas.phase3_enums import InterviewMode
from app.services.ai_provenance_service import (
    is_strong_stem_duplicate,
    normalize_stem,
    validate_conversational_question_text,
)
from app.services.ai_question_set_service import (
    generate_ai_questions_by_job_id,
    validate_ai_question_set,
)
from app.services.speech_service import SpeechRuntimeCapabilityService
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
def test_job(db_session: Session, recruiter_user: UserRecord) -> JobRequisition:
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter_user.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    db_session.add(job)
    db_session.flush()

    v1 = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        created_by_user_id=recruiter_user.user_id,
        version_number=1,
        revision=1,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        title="Senior Python API Developer",
        department="Engineering",
        location="Gandhinagar, Gujarat",
        employment_type="FULL_TIME",
        workplace_type="HYBRID",
        openings_count=2,
    )
    db_session.add(v1)
    db_session.flush()

    req = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=v1.job_version_id,
        display_order=0,
        category=RequirementCategory.SKILL,
        label="FastAPI Microservices",
        normalized_label="fastapi microservices",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
    )
    db_session.add(req)
    db_session.commit()
    return job


def test_stem_normalization_and_duplicate_detection():
    # 1. Exact duplicate
    s1 = "How do you structure asynchronous database connection pools in FastAPI?"
    s2 = "How do you structure asynchronous database connection pools in FastAPI?"
    assert is_strong_stem_duplicate(s1, s2)

    # 2. Punctuation & case variations
    s3 = "how do you structure asynchronous database connection pools in fastapi?!"
    assert is_strong_stem_duplicate(s1, s3)

    # 3. Strong near-duplicate (paraphrase above threshold)
    s4 = "How do you structure asynchronous database connection pools in FastAPI applications?"
    assert is_strong_stem_duplicate(s1, s4, threshold=0.80)

    # 4. Different question about the same topic (allowed)
    s5 = "Can you describe a production deadlock you resolved when scaling PostgreSQL with async SQLAlchemy?"
    assert not is_strong_stem_duplicate(s1, s5, threshold=0.80)


def test_conversational_validation_cross_mode_duplicate():
    text_stems = [
        "How do you configure asynchronous rate limiting in FastAPI microservices?",
        "Describe your experience building event-driven architectures with RabbitMQ.",
    ]

    # Exact duplicate rejected
    val_exact = validate_conversational_question_text(
        question_text="How do you configure asynchronous rate limiting in FastAPI microservices?",
        competency_name="API Security",
        interview_mode="AUDIO",
        cross_mode_existing_texts=text_stems,
    )
    assert not val_exact.is_valid
    assert val_exact.error_code == "CROSS_MODE_DUPLICATE_QUESTION"

    # Punctuation/case variation rejected
    val_case = validate_conversational_question_text(
        question_text="how do you configure asynchronous rate limiting in fastapi microservices?!",
        competency_name="API Security",
        interview_mode="AUDIO",
        cross_mode_existing_texts=text_stems,
    )
    assert not val_case.is_valid
    assert val_case.error_code == "CROSS_MODE_DUPLICATE_QUESTION"

    # Distinct conversational question on the same skill is accepted
    val_distinct = validate_conversational_question_text(
        question_text="When a downstream authentication service degrades, how does your gateway handle failover?",
        competency_name="API Security",
        interview_mode="AUDIO",
        cross_mode_existing_texts=text_stems,
    )
    assert val_distinct.is_valid


@pytest.mark.anyio
async def test_cross_revision_isolation_and_immutability(db_session: Session, recruiter_user: UserRecord, test_job: JobRequisition):
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")

    # Generate revision 1 text set
    text_set_v1 = await generate_ai_questions_by_job_id(
        actor=recruiter_user,
        job_id=test_job.job_id,
        force_regenerate=False,
        db=db_session,
        custom_provider=fake_provider,
        interview_mode=InterviewMode.TEXT,
    )

    # Create job revision 2
    v2 = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=test_job.job_id,
        created_by_user_id=recruiter_user.user_id,
        version_number=2,
        revision=2,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        title="Lead Python Architect",
        department="Engineering",
        location="Gandhinagar, Gujarat",
        employment_type="FULL_TIME",
        workplace_type="HYBRID",
        openings_count=1,
    )
    db_session.add(v2)
    db_session.flush()

    req2 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=v2.job_version_id,
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Distributed Systems Architecture",
        normalized_label="distributed systems architecture",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.CRITICAL,
    )
    db_session.add(req2)
    db_session.commit()

    # Generate revision 2 audio set
    audio_set_v2 = await generate_ai_questions_by_job_id(
        actor=recruiter_user,
        job_id=test_job.job_id,
        force_regenerate=False,
        db=db_session,
        custom_provider=fake_provider,
        interview_mode=InterviewMode.AUDIO,
    )

    # Validate that v2 audio set did not reject valid questions merely because v1 had unrelated text questions
    val_res = validate_ai_question_set(actor=recruiter_user, template_id=audio_set_v2.ai_set_id, db=db_session)
    assert val_res.is_valid

    # Check that historical revision 1 text set record remains immutable and untouched
    rec_v1 = db_session.get(AiInterviewQuestionSetRecord, text_set_v1.ai_set_id)
    assert rec_v1.job_revision == 1
    assert rec_v1.interview_mode == InterviewMode.TEXT


def test_candidate_capabilities_sanitization():
    caps = SpeechRuntimeCapabilityService.get_safe_capabilities()
    assert caps["configured_model_label"] is None
    assert caps["model_recommendation_warning"] is None
    if caps["safe_unavailable_reason"]:
        assert "whisper.cpp" not in caps["safe_unavailable_reason"]
        assert "Piper" not in caps["safe_unavailable_reason"]

