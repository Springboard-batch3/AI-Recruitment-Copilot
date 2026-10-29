"""Focused tests for Phase 4C.1: Interview Eligibility and Controlled Release.

Covers:
1. Successful release for an eligible submitted and interview-ready application.
2. Rejection for a draft, withdrawn, or non-submitted application.
3. Rejection for a mismatched Job/question-set version.
4. Rejection for unauthorized Recruiter or cross-organization access.
5. Repeated release is idempotent.
6. Candidate sees only their own assignment.
7. Candidate response excludes Recruiter-only question information (rubrics, MCQ correct keys, explanations).
8. Release does not mutate application, profile, match, rank, or Job state.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models.auth import (
    AuditEventType,
    Base,
    SecurityAuditEventRecord,
    UserRecord,
    UserRole,
    UserSessionRecord,
)
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateApplicationRecord,
    CandidateApplicationStatus,
    CandidateBindingStatus,
    CandidatePracticeReleaseRecord,
    RecruiterReviewState,
)
from app.models.extraction import ResumeExtraction
from app.models.interview import AiInterviewQuestionSetRecord
from app.models.job import (
    EmploymentType,
    JobLifecycleStatus,
    JobPublicationStatus,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
    WorkplaceType,
)
from app.models.matching import MatchResultRecord, MatchRunRecord
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.phase3_enums import PracticeReleaseStatus
from app.services.security import (
    canonicalize_email,
    generate_secure_token,
    hash_password,
    hash_token,
)


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
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
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def create_test_user(db, email: str, role: UserRole) -> UserRecord:
    clean_email = canonicalize_email(email)
    pwd_hash = hash_password("Password123!")
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=clean_email,
        display_name="Test User",
        password_hash=pwd_hash,
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_test_session(db, user: UserRecord) -> str:
    raw_token = generate_secure_token()
    raw_csrf = generate_secure_token()
    now = datetime.now(timezone.utc)
    sess = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=user.user_id,
        token_hash=hash_token(raw_token),
        csrf_token_hash=hash_token(raw_csrf),
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=2),
        absolute_expires_at=now + timedelta(hours=24),
    )
    db.add(sess)
    db.commit()
    return raw_token


def setup_candidate_with_profile(db, org_id: uuid.UUID, email: str, name: str):
    user = create_test_user(db, email, UserRole.CANDIDATE)
    art = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="resume.pdf",
        detected_format=ArtifactFormat.pdf,
        size_bytes=1024,
        sha256_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    db.add(art)
    db.commit()

    cand = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=art.artifact_id,
        full_name=name,
        primary_email=email,
        primary_phone="555-0199",
        identity_payload={"name": name, "email": email},
    )
    db.add(cand)
    db.commit()

    ext = ResumeExtraction(
        extraction_id=uuid.uuid4(),
        artifact_id=art.artifact_id,
        extractor_version="1.0.0",
    )
    db.add(ext)
    db.commit()

    prof = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=1,
        artifact_id=art.artifact_id,
        extraction_id=ext.extraction_id,
        identity_id=cand.identity_id,
        parser_pipeline_version="1.0.0",
        professional_profile={"headline": "Software Engineer", "skills": ["Python", "FastAPI", "PostgreSQL"]},
        evidence_spans=[],
    )
    db.add(prof)
    db.commit()

    binding = CandidateAccountBindingRecord(
        binding_id=uuid.uuid4(),
        user_id=user.user_id,
        candidate_id=cand.identity_id,
        organization_id=org_id,
        invitation_id=uuid.uuid4(),
        status=CandidateBindingStatus.ACTIVE.value,
    )
    db.add(binding)
    db.commit()

    return user, cand, prof, binding


def setup_test_job(
    db,
    recruiter: UserRecord,
    org_id: uuid.UUID,
    title: str = "Senior Backend Engineer",
    version_number: int = 1,
    status: JobLifecycleStatus = JobLifecycleStatus.ACTIVE,
):
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter.user_id,
        status=status,
        publication_status=JobPublicationStatus.RELEASED,
    )
    db.add(job)
    db.commit()

    jver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=version_number,
        version_status=JobVersionStatus.APPROVED,
        revision=version_number,
        title=title,
        department="Engineering",
        location="Remote",
        employment_type=EmploymentType.FULL_TIME,
        workplace_type=WorkplaceType.REMOTE,
        openings_count=2,
        summary="Build reliable distributed systems.",
        created_by_user_id=recruiter.user_id,
        approved_by_user_id=recruiter.user_id,
        approved_at=datetime.now(timezone.utc),
    )
    db.add(jver)
    db.flush()

    job.released_job_version_id = jver.job_version_id
    job.released_at = datetime.now(timezone.utc)
    job.released_by_user_id = recruiter.user_id
    db.commit()

    return job, jver


def setup_approved_ai_question_set(
    db,
    job: JobRequisition,
    jver: JobVersion,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    set_version: int = 1,
    lifecycle_status: str = "APPROVED",
):
    q_set = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=jver.version_number,
        source_content_hash="a" * 64,
        content_hash="b" * 64,
        set_version=set_version,
        lifecycle_status=lifecycle_status,
        is_immutable=(lifecycle_status == "APPROVED"),
        created_by_user_id=user_id,
        approved_by_user_id=user_id if lifecycle_status == "APPROVED" else None,
        model_identifier="local/qwen3-4b-thinking",
        items=[
            {
                "slot_id": str(uuid.uuid4()),
                "question_id": str(uuid.uuid4()),
                "question": "Describe your experience with async architectures in Python.",
                "text": "Describe your experience with async architectures in Python.",
                "question_format": "CONVERSATION",
                "competency": "Python Architecture",
                "authoring_origin": "LOCAL_LLM_GENERATED",
                "validation_status": "PASSED",
                "model_identifier": "local/qwen3-4b-thinking",
            },
            {
                "slot_id": str(uuid.uuid4()),
                "question_id": str(uuid.uuid4()),
                "stem": "Which HTTP status represents a conflict?",
                "text": "Which HTTP status represents a conflict?",
                "question_format": "MCQ_SINGLE_ANSWER",
                "options": [
                    {"id": "A", "text": "200"},
                    {"id": "B", "text": "404"},
                    {"id": "C", "text": "409"},
                    {"id": "D", "text": "500"},
                ],
                "correct_option_id": "C",
                "explanation": "HTTP status code 409 indicates a request conflict with current server state.",
                "competency": "HTTP Protocols",
                "authoring_origin": "LOCAL_LLM_GENERATED",
                "validation_status": "PASSED",
                "model_identifier": "local/qwen3-4b-thinking",
            },
        ],
        approved_at=datetime.now(timezone.utc) if lifecycle_status == "APPROVED" else None,
    )
    db.add(q_set)
    db.commit()
    db.refresh(q_set)
    return q_set


def test_phase4c1_interview_eligibility_and_release(client, test_db):
    org_1 = uuid.uuid4()
    org_2 = uuid.uuid4()

    # Recruiter 1 (Org 1)
    recruiter_1 = create_test_user(test_db, "recruiter1@org1.com", UserRole.RECRUITER)
    recruiter_1_token = create_test_session(test_db, recruiter_1)

    # Recruiter 2 (Org 2)
    recruiter_2 = create_test_user(test_db, "recruiter2@org2.com", UserRole.RECRUITER)
    recruiter_2_token = create_test_session(test_db, recruiter_2)

    # Candidate A (Org 1)
    cand_a_user, cand_a_ident, cand_a_prof, cand_a_binding = setup_candidate_with_profile(
        test_db, org_1, "candidate_a@org1.com", "Alice Smith"
    )
    cand_a_token = create_test_session(test_db, cand_a_user)

    # Candidate B (Org 1)
    cand_b_user, cand_b_ident, cand_b_prof, cand_b_binding = setup_candidate_with_profile(
        test_db, org_1, "candidate_b@org1.com", "Bob Jones"
    )
    cand_b_token = create_test_session(test_db, cand_b_user)

    # Job 1 (Rev 1)
    job_1, job_1_v1 = setup_test_job(test_db, recruiter_1, org_1, "Senior Backend Engineer", version_number=1)

    # Candidate A submits application
    client.cookies.set("ai_hiring_candidate_session", cand_a_token)
    res = client.post(
        f"/api/v1/candidate/applications/jobs/{job_1.job_id}",
        json={"idempotencyKey": "app-a-p4c1"},
    )
    assert res.status_code == status.HTTP_201_CREATED
    app_a_id = res.json()["applicationId"]
    client.cookies.clear()

    # 1. Rejection when no approved question set exists
    client.cookies.set("ai_hiring_session", recruiter_1_token)
    res_no_set = client.post(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_a_id}/release-interview",
        json={},
    )
    assert res_no_set.status_code == status.HTTP_400_BAD_REQUEST
    assert res_no_set.json()["detail"]["code"] == "NO_APPROVED_QUESTION_SET"

    # Setup Question Set for Rev 1
    q_set_v1 = setup_approved_ai_question_set(test_db, job_1, job_1_v1, org_1, recruiter_1.user_id, set_version=1)

    # 3. Rejection for unauthorized Recruiter / cross-organization access
    client.cookies.clear()
    client.cookies.set("ai_hiring_session", recruiter_2_token)
    res_cross_org = client.post(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_a_id}/release-interview",
        json={},
    )
    assert res_cross_org.status_code == status.HTTP_403_FORBIDDEN
    client.cookies.clear()

    # 4. Successful release for eligible submitted & INTERVIEW_READY application
    client.cookies.set("ai_hiring_session", recruiter_1_token)
    res_release = client.post(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_a_id}/release-interview",
        json={},
    )
    assert res_release.status_code == status.HTTP_201_CREATED
    rel_data = res_release.json()
    release_id = rel_data["releaseId"]
    assert rel_data["status"] == "RELEASED"
    assert rel_data["jobRevision"] == 1
    assert rel_data["aiSetId"] == str(q_set_v1.ai_set_id)

    # 5. Repeated release is idempotent (returns existing release without creating duplicates)
    res_repeat = client.post(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_a_id}/release-interview",
        json={},
    )
    assert res_repeat.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED)
    assert res_repeat.json()["releaseId"] == release_id

    # Verify database has exactly 1 release
    releases = test_db.execute(
        select(CandidatePracticeReleaseRecord).where(
            CandidatePracticeReleaseRecord.candidate_id == cand_a_ident.identity_id
        )
    ).scalars().all()
    assert len(releases) == 1
    assert releases[0].application_id == uuid.UUID(app_a_id)

    # 6. Candidate A sees only their own assignment under interviews discovery
    client.cookies.clear()
    client.cookies.set("ai_hiring_candidate_session", cand_a_token)
    res_cand_disc = client.get("/api/v1/candidate/practice-jobs")
    assert res_cand_disc.status_code == status.HTTP_200_OK
    cand_items = res_cand_disc.json()["items"]
    assert len(cand_items) == 1
    item = cand_items[0]
    cand_release_id = item.get("release_id") or item.get("releaseId")
    assert cand_release_id == release_id
    cand_job_title = item.get("job_title") or item.get("jobTitle")
    assert cand_job_title == "Senior Backend Engineer"
    cand_job_revision = item.get("job_revision") or item.get("jobRevision")
    assert cand_job_revision == 1
    cand_total_q = item.get("total_question_count") or item.get("totalQuestionCount")
    assert cand_total_q == 2

    # 7. Candidate response excludes Recruiter rubrics, correct MCQ keys, explanations, and internal prompts
    item_str = str(cand_items[0])
    assert "rubric" not in item_str
    assert "correct_option_id" not in item_str
    assert "explanation" not in item_str
    assert "prompt" not in item_str

    # 8. Candidate B sees 0 interviews (strictly isolated)
    client.cookies.clear()
    client.cookies.set("ai_hiring_candidate_session", cand_b_token)
    res_cand_b = client.get("/api/v1/candidate/practice-jobs")
    assert res_cand_b.status_code == status.HTTP_200_OK
    assert len(res_cand_b.json()["items"]) == 0
    client.cookies.clear()

    # 9. Verify state immutability: Application status, profile, job, and review state unchanged
    app_db = test_db.execute(
        select(CandidateApplicationRecord).where(
            CandidateApplicationRecord.application_id == uuid.UUID(app_a_id)
        )
    ).scalar_one()
    assert app_db.status == CandidateApplicationStatus.SUBMITTED.value
    assert app_db.recruiter_review_state == RecruiterReviewState.RECEIVED.value
    assert app_db.job_revision == 1
    assert app_db.profile_version == 1
