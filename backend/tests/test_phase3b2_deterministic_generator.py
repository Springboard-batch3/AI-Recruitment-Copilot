import json
import uuid
from datetime import datetime, timezone
from typing import List, Tuple
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models.auth import Base, UserRecord, UserRole
from app.models.interview import (
    InterviewQuestionRecord,
    InterviewRubricRecord,
    JobInterviewTemplateRecord,
)
from app.models.job import (
    JobLifecycleStatus,
    JobRequirement,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
    RequirementCategory,
    RequirementNecessity,
    RequirementOrigin,
    RequirementPriority,
)
from app.schemas.interview_snapshot import (
    DETERMINISTIC_BUILDER_VERSION,
    QUESTION_CATALOGUE_VERSION,
    RUBRIC_CATALOGUE_VERSION,
    JobBlueprintInputSnapshot,
    PolicyVersionReference,
)
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    InterviewLayer,
    PlanLifecycle,
    QuestionType,
    ValidationState,
)
from app.services.interview_catalogue import (
    CURATED_QUESTION_CATALOGUE,
    CURATED_RUBRIC_CATALOGUE,
)
from app.services.interview_generator_service import (
    InterviewBlueprintBuilder,
    InterviewPolicyValidator,
    InterviewSuitability,
    create_deterministic_interview_template_draft,
    evaluate_requirement_interview_suitability,
    preview_deterministic_interview_template,
)
from app.services.interview_snapshot_service import (
    InterviewJobNotReadyError,
    InterviewRequisitionNotFoundError,
    InterviewUnauthorizedError,
    load_authoritative_job_blueprint_snapshot,
)
from app.services.security import canonicalize_email, hash_password


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(test_db):
    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app, base_url="https://testserver")
    app.dependency_overrides.clear()


def _seed_user(db, email="recruiter@example.com", role=UserRole.RECRUITER) -> UserRecord:
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        display_name="Test Recruiter",
        password_hash=hash_password("ValidPassword123!"),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login_user(client, email="recruiter@example.com", password="ValidPassword123!"):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == status.HTTP_200_OK
    csrf_token = resp.json()["csrfToken"]
    return {
        "X-CSRF-Token": csrf_token,
    }


def _seed_comprehensive_job(
    db,
    recruiter_id: uuid.UUID,
    status=JobVersionStatus.READY_FOR_MATCHING,
    revision=1,
) -> Tuple[JobRequisition, JobVersion, List[JobRequirement]]:
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    db.add(job)
    db.flush()

    ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        revision=revision,
        version_status=status,
        title="Senior Distributed Systems & Backend Engineer",
        created_by_user_id=recruiter_id,
    )
    db.add(ver)
    db.flush()

    # Create a balanced mix of requirements
    reqs = [
        # 1. PostgreSQL (Must-Have, Critical)
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            display_order=0,
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.CRITICAL,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            label="PostgreSQL Performance Tuning",
            normalized_label="postgresql performance tuning",
            rationale="Critical for database scaling and low-latency transactional execution",
            min_experience_months=36,
        ),
        # 2. Distributed Systems (Must-Have, Critical)
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            display_order=1,
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.CRITICAL,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            label="Distributed Systems",
            normalized_label="distributed systems",
            rationale="Architecting fault-tolerant microservices and asynchronous event messaging",
            min_experience_months=48,
        ),
        # 3. Python Core (Must-Have, High)
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            display_order=2,
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.HIGH,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            label="Python AsyncIO",
            normalized_label="python",
            rationale="High-throughput asynchronous backend service implementation",
            min_experience_months=36,
        ),
        # 4. API Design (Preferred, High)
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            display_order=3,
            category=RequirementCategory.RESPONSIBILITY,
            necessity=RequirementNecessity.PREFERRED,
            priority=RequirementPriority.HIGH,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            label="REST API Design",
            normalized_label="api design",
            rationale=None,
        ),
        # 5. Incident Response (Preferred, Medium)
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            display_order=4,
            category=RequirementCategory.EXPERIENCE,
            necessity=RequirementNecessity.PREFERRED,
            priority=RequirementPriority.MEDIUM,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            label="Production Incident Response",
            normalized_label="incident response",
            rationale=None,
        ),
        # 6. Education Degree (Must-Have -> Document Verification item)
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            display_order=5,
            category=RequirementCategory.EDUCATION,
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.MEDIUM,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            label="B.S. in Computer Science or Equivalent",
            normalized_label="b.s. computer science",
            rationale="Baseline formal engineering foundation",
        ),
        # 7. Cloud Certification (Preferred -> Document Verification item)
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            display_order=6,
            category=RequirementCategory.CERTIFICATION,
            necessity=RequirementNecessity.PREFERRED,
            priority=RequirementPriority.LOW,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            label="AWS Certified Solutions Architect",
            normalized_label="aws certification",
            rationale=None,
        ),
    ]
    db.add_all(reqs)
    db.commit()
    return job, ver, reqs


# =========================================================================
# Test Group 1: Deterministic Generation & Hash Stability
# =========================================================================

def test_deterministic_blueprint_generation_invariance(test_db):
    """Verifies identical canonical job input produces exact identical blueprint content, question order, and content hash."""
    recruiter = _seed_user(test_db, email="recruiter.det@example.com")
    job, ver, reqs = _seed_comprehensive_job(test_db, recruiter.user_id)

    snapshot1, _ = load_authoritative_job_blueprint_snapshot(
        actor=recruiter, job_id=job.job_id, expected_revision=1, db=test_db
    )
    snapshot2, _ = load_authoritative_job_blueprint_snapshot(
        actor=recruiter, job_id=job.job_id, expected_revision=1, db=test_db
    )

    builder = InterviewBlueprintBuilder()
    bp1 = builder.generate_blueprint_preview(snapshot1)
    bp2 = builder.generate_blueprint_preview(snapshot2)

    assert bp1.content_hash == bp2.content_hash
    assert bp1.input_snapshot_hash == bp2.input_snapshot_hash
    assert len(bp1.common_core_questions) == len(bp2.common_core_questions)

    # Verify questions and rubrics match exactly
    for q1, q2 in zip(bp1.common_core_questions, bp2.common_core_questions):
        assert q1.question_text == q2.question_text
        assert q1.question_intent == q2.question_intent
        assert q1.display_order == q2.display_order
        assert q1.deterministic_template_id == q2.deterministic_template_id
        assert q1.rubric.rating_anchors == q2.rubric.rating_anchors
        assert q1.rubric.competency_definition == q2.rubric.competency_definition


def test_catalogue_or_policy_version_change_alters_hash(test_db):
    """Verifies that changing a builder or catalogue version intentionally changes the deterministic hash."""
    recruiter = _seed_user(test_db, email="recruiter.ver@example.com")
    job, ver, reqs = _seed_comprehensive_job(test_db, recruiter.user_id)

    snapshot, _ = load_authoritative_job_blueprint_snapshot(
        actor=recruiter, job_id=job.job_id, expected_revision=1, db=test_db
    )
    builder = InterviewBlueprintBuilder()
    bp_standard = builder.generate_blueprint_preview(snapshot)

    # Modify snapshot policy hash
    modified_policy = PolicyVersionReference(
        policy_id="safety_policy_v1",
        policy_version="2.0.0",
        policy_hash="f" * 64,
        schema_version="1.0.0",
    )
    mod_snapshot = snapshot.model_copy(update={"policy_reference": modified_policy})
    bp_modified = builder.generate_blueprint_preview(mod_snapshot)

    assert bp_standard.content_hash != bp_modified.content_hash


# =========================================================================
# Test Group 2: Allocation, Suitability & Rubric Anchors
# =========================================================================

def test_suitability_classification_and_coverage_separation(test_db):
    """Verifies education/certification are classified as DOCUMENT_VERIFICATION and skills as INTERVIEW_ELIGIBLE."""
    recruiter = _seed_user(test_db, email="recruiter.suit@example.com")
    job, ver, reqs = _seed_comprehensive_job(test_db, recruiter.user_id)

    snapshot, _ = load_authoritative_job_blueprint_snapshot(
        actor=recruiter, job_id=job.job_id, expected_revision=1, db=test_db
    )
    builder = InterviewBlueprintBuilder()
    bp = builder.generate_blueprint_preview(snapshot)

    cov = bp.coverage_summary
    assert cov.total_requirements_count == 7
    assert cov.interview_eligible_requirements_count == 5
    assert len(cov.document_verification_requirements) == 2

    doc_labels = [d.label for d in cov.document_verification_requirements]
    assert "B.S. in Computer Science or Equivalent" in doc_labels
    assert "AWS Certified Solutions Architect" in doc_labels

    # Check that selected competencies match the 5 interview eligible items
    selected_comp_keys = [c.competency_key for c in cov.selected_competencies]
    assert "POSTGRESQL_PERFORMANCE_TUNING" in selected_comp_keys
    assert "DISTRIBUTED_SYSTEMS" in selected_comp_keys
    assert "PYTHON_CORE" in selected_comp_keys


def test_question_allocation_bounds_and_rubric_anchors(test_db):
    """Verifies 6-10 questions allocated, every scored question has anchors 1, 3, 5 and <= 2 probes."""
    recruiter = _seed_user(test_db, email="recruiter.alloc@example.com")
    job, ver, reqs = _seed_comprehensive_job(test_db, recruiter.user_id)

    snapshot, _ = load_authoritative_job_blueprint_snapshot(
        actor=recruiter, job_id=job.job_id, expected_revision=1, db=test_db
    )
    builder = InterviewBlueprintBuilder()
    bp = builder.generate_blueprint_preview(snapshot)

    # Assert 6 to 10 questions allocated
    assert 6 <= len(bp.common_core_questions) <= 10

    # Assert duration bounds (between 30 and 60 minutes)
    assert 30 <= bp.coverage_summary.total_estimated_duration_minutes <= 60

    # Assert rubric integrity for every question
    for q in bp.common_core_questions:
        assert q.layer == InterviewLayer.COMMON_CORE
        assert q.authoring_origin == AuthoringOrigin.DETERMINISTIC
        assert q.included_in_common_score is True
        assert len(q.permitted_probes) <= 2

        assert q.rubric is not None
        anchors = q.rubric.rating_anchors
        assert 1 in anchors and len(anchors[1]) >= 5
        assert 3 in anchors and len(anchors[3]) >= 5
        assert 5 in anchors and len(anchors[5]) >= 5
        assert q.rubric.sufficient_evidence_guidance
        assert q.rubric.insufficient_evidence_guidance
        assert len(q.rubric.prohibited_inferences) >= 1


def test_prohibited_topic_policy_blocks_validation(test_db):
    """Verifies that injecting a prohibited topic into a question or rubric blocks validation."""
    validator = InterviewPolicyValidator()
    recruiter = _seed_user(test_db, email="recruiter.prohib@example.com")
    job, ver, reqs = _seed_comprehensive_job(test_db, recruiter.user_id)

    snapshot, _ = load_authoritative_job_blueprint_snapshot(
        actor=recruiter, job_id=job.job_id, expected_revision=1, db=test_db
    )
    builder = InterviewBlueprintBuilder()
    bp = builder.generate_blueprint_preview(snapshot)

    # Tamper with a question to include prohibited age/medical topic
    bad_q = bp.common_core_questions[0].model_copy(
        update={"question_text": "What is your date of birth and graduation year?"}
    )
    tampered_questions = [bad_q] + list(bp.common_core_questions[1:])

    report = validator.validate_blueprint(
        questions=tampered_questions,
        snapshot=snapshot,
        coverage=bp.coverage_summary,
    )
    assert report.is_valid is False
    assert any("Prohibited topic 'AGE_AND_DOB'" in err for err in report.blocking_errors)


# =========================================================================
# Test Group 3: Authorization, Persistence & API Endpoints
# =========================================================================

def test_recruiter_preview_and_draft_persistence_api_flow(client, test_db):
    """Verifies full Recruiter preview and draft persistence flow via API."""
    recruiter = _seed_user(test_db, email="recruiter.api@example.com")
    job, ver, reqs = _seed_comprehensive_job(test_db, recruiter.user_id)
    headers = _login_user(client, email="recruiter.api@example.com")

    # 1. Preview API
    preview_url = f"/api/v1/jobs/{job.job_id}/interview-templates/preview"
    resp_prev = client.get(preview_url, headers=headers)
    assert resp_prev.status_code == status.HTTP_200_OK
    prev_data = resp_prev.json()
    assert prev_data["job_id"] == str(job.job_id)
    assert prev_data["lifecycle_status"] == "DRAFT"
    assert prev_data["validation_report"]["is_valid"] is True
    assert len(prev_data["common_core_questions"]) >= 6

    # 2. Draft Creation API
    draft_url = f"/api/v1/jobs/{job.job_id}/interview-templates/draft"
    resp_draft = client.post(draft_url, headers=headers)
    assert resp_draft.status_code == status.HTTP_201_CREATED
    draft_data = resp_draft.json()
    assert draft_data["job_id"] == str(job.job_id)
    assert draft_data["template_version"] == 1
    assert draft_data["lifecycle_status"] == "DRAFT"
    assert draft_data["content_hash"] == prev_data["content_hash"]

    # 3. Database Persistence Verification
    stmt = (
        select(JobInterviewTemplateRecord)
        .where(JobInterviewTemplateRecord.job_id == job.job_id)
    )
    saved_template = test_db.execute(stmt).scalar_one_or_none()
    assert saved_template is not None
    assert saved_template.lifecycle_status == PlanLifecycle.DRAFT
    assert len(saved_template.questions) == len(prev_data["common_core_questions"])
    assert saved_template.questions[0].rubric is not None


def test_unassigned_recruiter_and_non_ready_job_rejected(client, test_db):
    """Verifies unauthorized recruiters and unready job revisions are rejected safely."""
    recruiter_owner = _seed_user(test_db, email="owner@example.com")
    recruiter_other = _seed_user(test_db, email="other@example.com")
    job, ver, reqs = _seed_comprehensive_job(test_db, recruiter_owner.user_id)

    # Other recruiter tries preview
    headers_other = _login_user(client, email="other@example.com")
    resp_unauth = client.get(f"/api/v1/jobs/{job.job_id}/interview-templates/preview", headers=headers_other)
    assert resp_unauth.status_code == status.HTTP_404_NOT_FOUND  # Owner check raises 404

    # Draft job version rejection
    job_draft = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter_owner.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(job_draft)
    test_db.flush()
    ver_draft = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job_draft.job_id,
        version_number=1,
        revision=1,
        version_status=JobVersionStatus.DRAFT,  # DRAFT status
        title="Draft Engineer",
        created_by_user_id=recruiter_owner.user_id,
    )
    test_db.add(ver_draft)
    test_db.commit()

    headers_owner = _login_user(client, email="owner@example.com")
    resp_draft_status = client.get(
        f"/api/v1/jobs/{job_draft.job_id}/interview-templates/preview",
        headers=headers_owner,
    )
    assert resp_draft_status.status_code == status.HTTP_409_CONFLICT
    assert resp_draft_status.json()["detail"]["code"] == "INTERVIEW_JOB_NOT_READY"
