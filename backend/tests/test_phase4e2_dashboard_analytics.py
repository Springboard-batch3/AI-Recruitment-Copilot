import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models.auth import Base, UserRecord, UserRole
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.candidate_access import (
    CandidateApplicationRecord,
    CandidateApplicationStatus,
    CandidateInterviewAnswerRecord,
    CandidateInterviewEvaluationRecord,
    CandidateInterviewSessionRecord,
    CandidatePracticeReleaseRecord,
    RecruiterReviewState,
)
from app.models.extraction import BlockType, ExtractionBlock, ExtractionMethod, ResumeExtraction
from app.models.job import JobLifecycleStatus, JobPublicationStatus, JobRequisition, JobVersion, JobVersionStatus
from app.models.matching import MatchResultRecord, MatchRunLifecycleStatus, MatchRunRecord
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.services.dashboard_session_service import _sign_token_payload


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
    yield TestClient(app)
    app.dependency_overrides.clear()


def create_dash_token_for_org(db, org_id: uuid.UUID, role: str = "RECRUITER") -> str:
    settings = get_settings()
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=f"user-{uuid.uuid4()}@example.com",
        display_name="Recruiter User",
        password_hash="dummy_hash",
        role=UserRole.RECRUITER if role == "RECRUITER" else UserRole.ADMIN,
        is_active=True,
    )
    db.add(user)
    db.commit()

    payload = {
        "sub": str(user.user_id),
        "org_id": str(org_id),
        "role": role,
        "purpose": "RECRUITER_STREAMLIT_ANALYTICS",
        "exp": int(datetime.now(timezone.utc).timestamp()) + 1800,
        "jti": str(uuid.uuid4()),
    }
    return _sign_token_payload(payload, settings.dashboard_token_secret)


# --- Analytics Isolation & Lifecycle Meaning Tests ---

def test_organization_data_isolation(client, test_db):
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()

    token_a = create_dash_token_for_org(test_db, org_a)
    token_b = create_dash_token_for_org(test_db, org_b)

    recruiter_user_id = uuid.uuid4()
    recruiter = UserRecord(
        user_id=recruiter_user_id,
        email="recruiter-creator@example.com",
        display_name="Job Creator",
        password_hash="dummy_hash",
        role=UserRole.RECRUITER,
        is_active=True,
    )
    test_db.add(recruiter)
    test_db.commit()

    # Add job requisition & version
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter_user_id,
        status=JobLifecycleStatus.ACTIVE,
        publication_status=JobPublicationStatus.RELEASED,
    )
    job_ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        version_status=JobVersionStatus.APPROVED,
        title="Senior Python Engineer",
        revision=1,
        created_by_user_id=recruiter_user_id,
    )
    job.released_job_version_id = job_ver.job_version_id
    test_db.add(job)
    test_db.add(job_ver)

    # Add 2 applications for Org A (1 SUBMITTED, 1 DRAFT)
    app_a1 = CandidateApplicationRecord(
        application_id=uuid.uuid4(),
        organization_id=org_a,
        job_id=job.job_id,
        job_version_id=job_ver.job_version_id,
        job_revision=1,
        status=CandidateApplicationStatus.SUBMITTED.value,
        recruiter_review_state=RecruiterReviewState.RECEIVED.value,
    )
    app_a2_draft = CandidateApplicationRecord(
        application_id=uuid.uuid4(),
        organization_id=org_a,
        job_id=job.job_id,
        job_version_id=job_ver.job_version_id,
        job_revision=1,
        status=CandidateApplicationStatus.DRAFT.value,
        recruiter_review_state=RecruiterReviewState.RECEIVED.value,
    )

    # Add 5 submitted applications for Org B
    for _ in range(5):
        app_b = CandidateApplicationRecord(
            application_id=uuid.uuid4(),
            organization_id=org_b,
            job_id=job.job_id,
            job_version_id=job_ver.job_version_id,
            job_revision=1,
            status=CandidateApplicationStatus.SUBMITTED.value,
            recruiter_review_state=RecruiterReviewState.RECEIVED.value,
        )
        test_db.add(app_b)

    test_db.add(app_a1)
    test_db.add(app_a2_draft)
    test_db.commit()

    # Query overview as Org A
    res_a = client.get("/api/v1/dashboard/overview", headers={"Authorization": f"Bearer {token_a}"})
    assert res_a.status_code == status.HTTP_200_OK
    data_a = res_a.json()
    # Org A sees exactly 1 submitted application (draft application is excluded)
    assert data_a["submitted_applications"] == 1
    assert data_a["awaiting_review"] == 1

    # Query overview as Org B
    res_b = client.get("/api/v1/dashboard/overview", headers={"Authorization": f"Bearer {token_b}"})
    assert res_b.status_code == status.HTTP_200_OK
    data_b = res_b.json()
    assert data_b["submitted_applications"] == 5


def test_text_and_audio_interviews_remain_separate_and_score_not_zero_when_missing(client, test_db):
    org_id = uuid.uuid4()
    token = create_dash_token_for_org(test_db, org_id)

    recruiter_user_id = uuid.uuid4()
    recruiter = UserRecord(
        user_id=recruiter_user_id,
        email="recruiter-interviews@example.com",
        display_name="Job Creator",
        password_hash="dummy_hash",
        role=UserRole.RECRUITER,
        is_active=True,
    )
    test_db.add(recruiter)
    test_db.commit()

    # Create dummy artifact & identity
    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="alice_resume.pdf",
        declared_mime_type="application/pdf",
        detected_format=ArtifactFormat.pdf,
        sha256_hash="0" * 64,
        size_bytes=1000,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    ident = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        full_name="Alice Candidate",
        identity_payload={},
    )
    test_db.add(artifact)
    test_db.add(ident)

    # Job & Version
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter_user_id,
        status=JobLifecycleStatus.ACTIVE,
        publication_status=JobPublicationStatus.RELEASED,
    )
    job_ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        version_status=JobVersionStatus.APPROVED,
        title="Backend Engineer",
        revision=1,
        created_by_user_id=recruiter_user_id,
    )
    test_db.add(job)
    test_db.add(job_ver)

    # Release 1: Text
    rel_text = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        organization_id=org_id,
        candidate_id=ident.identity_id,
        job_id=job.job_id,
        job_version_id=job_ver.job_version_id,
        job_revision=1,
        interview_mode="TEXT",
        status="RELEASED",
        released_by_user_id=recruiter_user_id,
    )
    # Release 2: Audio
    rel_audio = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        organization_id=org_id,
        candidate_id=ident.identity_id,
        job_id=job.job_id,
        job_version_id=job_ver.job_version_id,
        job_revision=1,
        interview_mode="AUDIO",
        status="RELEASED",
        released_by_user_id=recruiter_user_id,
    )
    test_db.add(rel_text)
    test_db.add(rel_audio)

    # Session 1: Text - Completed & Evaluated with score 82.5
    sess_text = CandidateInterviewSessionRecord(
        session_id=uuid.uuid4(),
        organization_id=org_id,
        release_id=rel_text.release_id,
        candidate_id=ident.identity_id,
        job_id=job.job_id,
        job_version_id=job_ver.job_version_id,
        job_revision=1,
        snapshot_hash="1" * 64,
        questions_snapshot=[],
        status="SUBMITTED",
        interview_mode="TEXT",
    )
    eval_text = CandidateInterviewEvaluationRecord(
        evaluation_id=uuid.uuid4(),
        organization_id=org_id,
        session_id=sess_text.session_id,
        job_id=job.job_id,
        job_version_id=job_ver.job_version_id,
        candidate_id=ident.identity_id,
        status="FINALIZED",
        aggregate_score=82.5,
        aggregate_summary={"dimension_scores": {"technical_depth": 85.0, "communication": 80.0}},
    )
    test_db.add(sess_text)
    test_db.add(eval_text)

    # Session 2: Audio - In Progress, NO Evaluation yet
    sess_audio = CandidateInterviewSessionRecord(
        session_id=uuid.uuid4(),
        organization_id=org_id,
        release_id=rel_audio.release_id,
        candidate_id=ident.identity_id,
        job_id=job.job_id,
        job_version_id=job_ver.job_version_id,
        job_revision=1,
        snapshot_hash="2" * 64,
        questions_snapshot=[],
        status="IN_PROGRESS",
        interview_mode="AUDIO",
    )
    test_db.add(sess_audio)
    test_db.commit()

    # Query all interviews
    res = client.get("/api/v1/dashboard/interviews", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == status.HTTP_200_OK
    items = res.json()["interviews"]
    assert len(items) == 2

    # Verify Text Session
    text_item = next(i for i in items if i["interview_mode"] == "TEXT")
    assert text_item["session_status"] == "SUBMITTED"
    assert text_item["evaluation_status"] == "FINALIZED"
    assert text_item["persisted_overall_score"] == 82.5
    assert text_item["dimension_scores"] == {"technical_depth": 85.0, "communication": 80.0}

    # Verify Audio Session - missing score is null, NOT 0.0
    audio_item = next(i for i in items if i["interview_mode"] == "AUDIO")
    assert audio_item["session_status"] == "IN_PROGRESS"
    assert audio_item["evaluation_status"] == "UNAVAILABLE"
    assert audio_item["persisted_overall_score"] is None
    assert "transcript" in audio_item["scoring_notes"].lower()

    # Filter by TEXT only
    res_text_only = client.get("/api/v1/dashboard/interviews?interview_mode=TEXT", headers={"Authorization": f"Bearer {token}"})
    assert len(res_text_only.json()["interviews"]) == 1

    # Filter by AUDIO only
    res_audio_only = client.get("/api/v1/dashboard/interviews?interview_mode=AUDIO", headers={"Authorization": f"Bearer {token}"})
    assert len(res_audio_only.json()["interviews"]) == 1


def test_readiness_contains_no_filesystem_paths_or_secrets(client, test_db):
    org_id = uuid.uuid4()
    token = create_dash_token_for_org(test_db, org_id)

    res = client.get("/api/v1/dashboard/readiness", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["api_status"] == "AVAILABLE"
    assert data["database_status"] == "AVAILABLE"

    # Leakage checks
    raw_str = res.text
    forbidden_tokens = ["/Users", "/home", "/bin", "whisper_cpp", "piper", "secret", "password", "postgresql://"]
    for ft in forbidden_tokens:
        assert ft.lower() not in raw_str.lower()
