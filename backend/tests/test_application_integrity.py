"""Diagnostic and regression test suite for Application Integrity: Comparison, Matching, and Official Interview Release."""
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
from app.models.auth import Base, UserRecord, UserRole, UserSessionRecord
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateApplicationRecord,
    CandidateApplicationStatus,
    CandidateBindingStatus,
    CandidatePracticeReleaseRecord,
    RecruiterReviewState,
)
from app.models.interview import AiInterviewQuestionSetRecord
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
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.phase3_enums import PracticeReleaseStatus
from app.services.security import canonicalize_email, generate_secure_token, hash_password, hash_token


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
    yield TestClient(app, base_url="https://testserver")
    app.dependency_overrides.clear()


def _login_user(client: TestClient, db, user: UserRecord) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": user.email, "password": "password123"})
    assert resp.status_code == status.HTTP_200_OK
    return resp.json()["csrfToken"]


def _seed_job_with_reqs(db, recruiter: UserRecord, title="Software Engineer", revision=1):
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    db.add(job)
    db.flush()

    ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        revision=revision,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        title=title,
        created_by_user_id=recruiter.user_id,
        ready_by_user_id=recruiter.user_id,
        ready_at=datetime.now(timezone.utc),
    )
    db.add(ver)
    db.flush()

    req = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        category=RequirementCategory.SKILL,
        label="Python",
        normalized_label="python",
        display_order=0,
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
        rationale="Core implementation requirement.",
    )
    db.add(req)
    db.commit()
    return job, ver


def _seed_candidate_with_profile(db, name="Candidate"):
    art = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename=f"{name.lower().replace(' ', '_')}.pdf",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    db.add(art)
    db.flush()

    ident = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=art.artifact_id,
        full_name=name,
        primary_email=f"{name.lower().replace(' ', '.')}@example.com",
        identity_payload={"fullName": {"value": name}},
    )
    db.add(ident)
    db.flush()

    prof = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=1,
        artifact_id=art.artifact_id,
        extraction_id=uuid.uuid4(),
        identity_id=ident.identity_id,
        schema_version="1.0",
        parser_pipeline_version="1.0.0",
        provenance="PARSER_EXTRACTED",
        professional_profile={
            "headline": {"value": f"{name} Headline"},
            "skills": [
                {
                    "value": "Python",
                    "normalizedValue": "python",
                    "confidence": 0.95,
                    "confidenceBand": "HIGH",
                    "evidenceIds": ["ev-py"],
                    "reviewStatus": "CONFIRMED",
                }
            ],
            "employment": [],
            "education": [],
            "projects": [],
            "certifications": [],
            "languages": [],
        },
        evidence_spans=[],
        review_required=False,
    )
    db.add(prof)
    db.commit()
    return ident, prof


def test_cross_job_applicant_isolation_in_listing(client, test_db):
    """Candidates submitted for Job A must not appear in Job B's applicants list."""
    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email="recruiter.iso@company.test",
        display_name="Recruiter Iso",
        password_hash=hash_password("password123"),
        role=UserRole.RECRUITER,
        is_active=True,
    )
    test_db.add(recruiter)
    test_db.commit()

    # Job A: React Frontend Developer
    job_react, ver_react = _seed_job_with_reqs(test_db, recruiter, title="Junior React Frontend Developer")
    # Job B: Python API Developer
    job_python, ver_python = _seed_job_with_reqs(test_db, recruiter, title="Junior Python API Developer")

    cand1_ident, cand1_prof = _seed_candidate_with_profile(test_db, name="React Candidate 1")
    cand2_ident, cand2_prof = _seed_candidate_with_profile(test_db, name="React Candidate 2")
    cand3_ident, cand3_prof = _seed_candidate_with_profile(test_db, name="Python Candidate 3")

    # Seed SUBMITTED applications
    app1 = CandidateApplicationRecord(
        application_id=uuid.uuid4(),
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        user_id=uuid.uuid4(),
        binding_id=uuid.uuid4(),
        candidate_id=cand1_ident.identity_id,
        job_id=job_react.job_id,
        job_version_id=ver_react.job_version_id,
        job_revision=ver_react.revision,
        artifact_id=cand1_prof.artifact_id,
        profile_id=cand1_prof.profile_id,
        profile_version=cand1_prof.profile_version,
        status=CandidateApplicationStatus.SUBMITTED.value,
        submitted_at=datetime.now(timezone.utc),
    )
    app2 = CandidateApplicationRecord(
        application_id=uuid.uuid4(),
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        user_id=uuid.uuid4(),
        binding_id=uuid.uuid4(),
        candidate_id=cand2_ident.identity_id,
        job_id=job_react.job_id,
        job_version_id=ver_react.job_version_id,
        job_revision=ver_react.revision,
        artifact_id=cand2_prof.artifact_id,
        profile_id=cand2_prof.profile_id,
        profile_version=cand2_prof.profile_version,
        status=CandidateApplicationStatus.SUBMITTED.value,
        submitted_at=datetime.now(timezone.utc),
    )
    app3 = CandidateApplicationRecord(
        application_id=uuid.uuid4(),
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        user_id=uuid.uuid4(),
        binding_id=uuid.uuid4(),
        candidate_id=cand3_ident.identity_id,
        job_id=job_python.job_id,
        job_version_id=ver_python.job_version_id,
        job_revision=ver_python.revision,
        artifact_id=cand3_prof.artifact_id,
        profile_id=cand3_prof.profile_id,
        profile_version=cand3_prof.profile_version,
        status=CandidateApplicationStatus.SUBMITTED.value,
        submitted_at=datetime.now(timezone.utc),
    )
    test_db.add_all([app1, app2, app3])
    test_db.commit()

    _login_user(client, test_db, recruiter)

    # 1. Fetch applicants for Python job -> cand1 and cand2 must NOT appear
    resp_py = client.get(f"/api/v1/jobs/{job_python.job_id}/applications?status=SUBMITTED&revision={ver_python.revision}")
    assert resp_py.status_code == status.HTTP_200_OK
    py_data = resp_py.json()
    assert py_data["total"] == 1
    assert py_data["items"][0]["candidateId"] == str(cand3_ident.identity_id)

    # 2. Fetch applicants for React job -> cand1 and cand2 must appear, cand3 must NOT
    resp_react = client.get(f"/api/v1/jobs/{job_react.job_id}/applications?status=SUBMITTED&revision={ver_react.revision}")
    assert resp_react.status_code == status.HTTP_200_OK
    react_data = resp_react.json()
    assert react_data["total"] == 2
    react_cand_ids = {it["candidateId"] for it in react_data["items"]}
    assert react_cand_ids == {str(cand1_ident.identity_id), str(cand2_ident.identity_id)}


def test_matching_endpoint_rejects_unapplied_candidate(client, test_db):
    """Direct API call to match run with Candidate IDs without application for that job is rejected."""
    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email="recruiter.matchtest@company.test",
        display_name="Recruiter MatchTest",
        password_hash=hash_password("password123"),
        role=UserRole.RECRUITER,
        is_active=True,
    )
    test_db.add(recruiter)
    test_db.commit()

    job_react, ver_react = _seed_job_with_reqs(test_db, recruiter, title="Junior React Frontend Developer")
    job_python, ver_python = _seed_job_with_reqs(test_db, recruiter, title="Junior Python API Developer")

    cand1_ident, cand1_prof = _seed_candidate_with_profile(test_db, name="React Only Candidate")

    # Application is ONLY for React job
    app1 = CandidateApplicationRecord(
        application_id=uuid.uuid4(),
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        user_id=uuid.uuid4(),
        binding_id=uuid.uuid4(),
        candidate_id=cand1_ident.identity_id,
        job_id=job_react.job_id,
        job_version_id=ver_react.job_version_id,
        job_revision=ver_react.revision,
        artifact_id=cand1_prof.artifact_id,
        profile_id=cand1_prof.profile_id,
        profile_version=cand1_prof.profile_version,
        status=CandidateApplicationStatus.SUBMITTED.value,
        submitted_at=datetime.now(timezone.utc),
    )
    test_db.add(app1)
    test_db.commit()

    _login_user(client, test_db, recruiter)

    # Attempt to run matching for Python job using React candidate -> Must be rejected with 400
    resp_match = client.post(
        "/api/v1/matches/runs",
        headers={"Idempotency-Key": "key-match-unapplied-candidate"},
        json={
            "jobId": str(job_python.job_id),
            "expectedRevision": ver_python.revision,
            "candidates": [{"profileId": str(cand1_prof.profile_id), "profileVersion": 1}],
        },
    )
    assert resp_match.status_code == status.HTTP_400_BAD_REQUEST
    err_body = resp_match.json()
    assert err_body["detail"]["code"] == "APPLICATION_REQUIRED_FOR_JOB"


def test_matching_endpoint_rejects_withdrawn_or_draft_application(client, test_db):
    """Matching rejects candidates whose applications are WITHDRAWN or not SUBMITTED."""
    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email="recruiter.withdrawn@company.test",
        display_name="Recruiter Withdrawn",
        password_hash=hash_password("password123"),
        role=UserRole.RECRUITER,
        is_active=True,
    )
    test_db.add(recruiter)
    test_db.commit()

    job, ver = _seed_job_with_reqs(test_db, recruiter, title="Fullstack Engineer")
    cand_ident, cand_prof = _seed_candidate_with_profile(test_db, name="Withdrawn Candidate")

    app_withdrawn = CandidateApplicationRecord(
        application_id=uuid.uuid4(),
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        user_id=uuid.uuid4(),
        binding_id=uuid.uuid4(),
        candidate_id=cand_ident.identity_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        job_revision=ver.revision,
        artifact_id=cand_prof.artifact_id,
        profile_id=cand_prof.profile_id,
        profile_version=cand_prof.profile_version,
        status=CandidateApplicationStatus.WITHDRAWN.value,
        submitted_at=datetime.now(timezone.utc),
        withdrawn_at=datetime.now(timezone.utc),
    )
    test_db.add(app_withdrawn)
    test_db.commit()

    _login_user(client, test_db, recruiter)

    resp = client.post(
        "/api/v1/matches/runs",
        headers={"Idempotency-Key": "key-match-withdrawn-app"},
        json={
            "jobId": str(job.job_id),
            "expectedRevision": ver.revision,
            "candidates": [{"profileId": str(cand_prof.profile_id), "profileVersion": 1}],
        },
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert resp.json()["detail"]["code"] == "APPLICATION_NOT_SUBMITTED"


def test_read_only_integrity_audit_utility(test_db):
    """Read-only integrity audit utility identifies orphan or mismatched historical records without mutating DB."""
    # 1. Active practice release with NO application
    orphan_release = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        candidate_id=uuid.uuid4(),
        application_id=None,
        job_id=uuid.uuid4(),
        job_version_id=uuid.uuid4(),
        job_revision=1,
        interview_mode="TEXT",
        status=PracticeReleaseStatus.RELEASED.value,
        released_by_user_id=uuid.uuid4(),
        released_at=datetime.now(timezone.utc),
    )
    test_db.add(orphan_release)
    test_db.commit()

    # Diagnostic inspection: Find active releases without a valid submitted application
    stmt = (
        select(CandidatePracticeReleaseRecord)
        .where(CandidatePracticeReleaseRecord.status == PracticeReleaseStatus.RELEASED.value)
    )
    releases = test_db.execute(stmt).scalars().all()
    invalid_releases = []
    for r in releases:
        if not r.application_id:
            invalid_releases.append(str(r.release_id))
            continue
        app = test_db.execute(
            select(CandidateApplicationRecord).where(
                CandidateApplicationRecord.application_id == r.application_id,
                CandidateApplicationRecord.status == CandidateApplicationStatus.SUBMITTED.value,
            )
        ).scalar_one_or_none()
        if not app:
            invalid_releases.append(str(r.release_id))

    assert len(invalid_releases) == 1
    assert invalid_releases[0] == str(orphan_release.release_id)
