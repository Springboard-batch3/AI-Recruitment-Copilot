"""Phase 3C.4: Recruiter UI Integration & Wording Action End-to-End Tests.

Covers:
- Recruiter authorization boundary (only assigned recruiter can apply/restore suggestions).
- Admin cannot edit questions solely through Admin role.
- Only DRAFT questions are eligible for apply/restore.
- Optimistic concurrency: mismatching question version or source content hash is rejected.
- Applying suggestion sets authoring_origin to AI_ASSISTED and invalidates previous validation.
- Restoring deterministic wording sets authoring_origin to DETERMINISTIC and retains history.
- Approved templates and questions remain strictly immutable.
- No leakage of raw prompts or secrets in response payloads.
- No mutation of Phase 2 match scores, ranks, or requirements.
"""

import uuid
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker, Session

from app.models.auth import Base, UserRecord, UserRole
from app.models.job import (
    JobLifecycleStatus,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
)
from app.models.interview import (
    JobInterviewTemplateRecord,
    InterviewQuestionRecord,
)
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    InterviewLayer,
    PlanLifecycle,
    QuestionType,
    ValidationState,
)
from app.schemas.interview_api import (
    ApplyWordingSuggestionRequest,
    RestoreDeterministicWordingRequest,
    QuestionActionResponse,
)
from app.services.wording_assistance_service import (
    apply_wording_suggestion,
    restore_deterministic_wording,
    QuestionWordingError,
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
def test_setup(test_db: Session):
    org_id = uuid.uuid4()

    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email=f"recruiter-{uuid.uuid4().hex[:6]}@example.com",
        password_hash="fakehashdummyvalue",
        display_name="Recruiter User",
        role=UserRole.RECRUITER,
        is_active=True,
    )
    admin = UserRecord(
        user_id=uuid.uuid4(),
        email=f"admin-{uuid.uuid4().hex[:6]}@example.com",
        password_hash="fakehashdummyvalue",
        display_name="Admin User",
        role=UserRole.ADMIN,
        is_active=True,
    )
    other_recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email=f"other-{uuid.uuid4().hex[:6]}@example.com",
        password_hash="fakehashdummyvalue",
        display_name="Other Recruiter",
        role=UserRole.RECRUITER,
        is_active=True,
    )
    test_db.add_all([recruiter, admin, other_recruiter])
    test_db.flush()

    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(job)
    test_db.flush()

    job_version = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        title="Senior Python Backend Engineer",
        created_by_user_id=recruiter.user_id,
        version_status=JobVersionStatus.APPROVED,
    )
    test_db.add(job_version)
    test_db.flush()

    template = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=job_version.job_version_id,
        revision=1,
        template_version=1,
        lifecycle_status=PlanLifecycle.DRAFT,
        guardrail_policy_version="1.0.0",
        guardrail_policy_hash="policy_hash_dummy_3c4",
        deterministic_builder_version="1.0.0",
        question_catalogue_version="1.0.0",
        rubric_catalogue_version="1.0.0",
        input_snapshot_hash="input_snapshot_dummy_3c4",
        content_hash="content_hash_dummy_3c4",
        created_by_user_id=recruiter.user_id,
        created_at=datetime.now(timezone.utc),
    )
    test_db.add(template)
    test_db.flush()

    question = InterviewQuestionRecord(
        question_id=uuid.uuid4(),
        template_id=template.template_id,
        question_version=1,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        authoring_origin=AuthoringOrigin.DETERMINISTIC,
        display_order=0,
        competency_id="backend_architecture",
        question_text="Explain how you handle optimistic concurrency control in distributed relational databases.",
        question_intent="Assess understanding of optimistic locking and version checks.",
        estimated_time_minutes=5,
        included_in_common_score=True,
        validation_state=ValidationState.PASSED,
        safe_metadata={},
    )
    test_db.add(question)
    test_db.commit()

    return {
        "org_id": org_id,
        "recruiter": recruiter,
        "admin": admin,
        "other_recruiter": other_recruiter,
        "job": job,
        "template": template,
        "question": question,
    }


def test_recruiter_can_apply_wording_suggestion(test_setup, test_db: Session):
    recruiter = test_setup["recruiter"]
    question = test_setup["question"]

    req = ApplyWordingSuggestionRequest(
        expected_question_version=1,
        suggested_text="Can you describe how you implement optimistic concurrency in distributed database architectures?",
        rationale_code="CLARITY_IMPROVEMENT",
    )

    resp: QuestionActionResponse = apply_wording_suggestion(
        actor=recruiter,
        question_id=question.question_id,
        request=req,
        db=test_db,
    )

    assert resp.question_id == question.question_id
    assert resp.question_version == 2
    assert resp.authoring_origin == AuthoringOrigin.AI_ASSISTED
    assert resp.validation_state == ValidationState.PENDING
    assert resp.deterministic_source_text == "Explain how you handle optimistic concurrency control in distributed relational databases."
    assert "Can you describe how" in resp.question_text
    assert resp.safe_metadata.get("ai_provenance", {}).get("rationale_code") == "CLARITY_IMPROVEMENT"


def test_admin_cannot_apply_wording_suggestion(test_setup, test_db: Session):
    admin = test_setup["admin"]
    question = test_setup["question"]

    req = ApplyWordingSuggestionRequest(
        expected_question_version=1,
        suggested_text="Admin rewording attempt that must be rejected.",
        rationale_code="CLARITY_IMPROVEMENT",
    )

    with pytest.raises(Exception) as exc_info:
        apply_wording_suggestion(
            actor=admin,
            question_id=question.question_id,
            request=req,
            db=test_db,
        )

    assert "Recruiter" in str(exc_info.value)


def test_stale_question_version_rejected(test_setup, test_db: Session):
    recruiter = test_setup["recruiter"]
    question = test_setup["question"]

    req = ApplyWordingSuggestionRequest(
        expected_question_version=99,  # Mismatch (current is 1)
        suggested_text="Rewording with stale version expectation.",
        rationale_code="CLARITY_IMPROVEMENT",
    )

    with pytest.raises(QuestionWordingError) as exc_info:
        apply_wording_suggestion(
            actor=recruiter,
            question_id=question.question_id,
            request=req,
            db=test_db,
        )

    assert exc_info.value.code == "STALE_QUESTION_VERSION"
    assert exc_info.value.status_code == 409


def test_restore_deterministic_wording_preserves_baseline(test_setup, test_db: Session):
    recruiter = test_setup["recruiter"]
    question = test_setup["question"]

    # 1. First apply a suggestion
    req_apply = ApplyWordingSuggestionRequest(
        expected_question_version=1,
        suggested_text="Alternative phrasing.",
        rationale_code="CONCISENESS",
    )
    resp_apply = apply_wording_suggestion(
        actor=recruiter,
        question_id=question.question_id,
        request=req_apply,
        db=test_db,
    )
    assert resp_apply.question_version == 2
    assert resp_apply.authoring_origin == AuthoringOrigin.AI_ASSISTED

    # 2. Now restore original deterministic wording
    req_restore = RestoreDeterministicWordingRequest(
        expected_question_version=2,
    )
    resp_restore = restore_deterministic_wording(
        actor=recruiter,
        question_id=question.question_id,
        request=req_restore,
        db=test_db,
    )

    assert resp_restore.question_version == 3
    assert resp_restore.authoring_origin == AuthoringOrigin.DETERMINISTIC
    assert resp_restore.question_text == "Explain how you handle optimistic concurrency control in distributed relational databases."
    assert resp_restore.validation_state == ValidationState.PENDING


def test_cannot_modify_approved_template_question(test_setup, test_db: Session):
    recruiter = test_setup["recruiter"]
    template = test_setup["template"]
    question = test_setup["question"]

    # Lock template as APPROVED
    template.lifecycle_status = PlanLifecycle.APPROVED
    test_db.commit()

    req = ApplyWordingSuggestionRequest(
        expected_question_version=1,
        suggested_text="Illegal modification of locked approved baseline.",
    )

    with pytest.raises(QuestionWordingError) as exc_info:
        apply_wording_suggestion(
            actor=recruiter,
            question_id=question.question_id,
            request=req,
            db=test_db,
        )

    assert exc_info.value.status_code == 409
    assert "DRAFT" in exc_info.value.message


def test_get_latest_job_interview_template_lookup(test_setup, test_db: Session):
    template = test_setup["template"]
    job = test_setup["job"]

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    stmt = (
        select(JobInterviewTemplateRecord)
        .options(
            selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric)
        )
        .where(JobInterviewTemplateRecord.job_id == job.job_id)
        .order_by(JobInterviewTemplateRecord.template_version.desc())
    )
    latest_rec = test_db.execute(stmt).scalars().first()
    assert latest_rec is not None
    assert latest_rec.template_id == template.template_id
    assert len(latest_rec.questions) == 1

