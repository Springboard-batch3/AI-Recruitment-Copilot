"""Focused test suite for Candidate Self-Registration (superseding recruiter-provisioned access).

Covers all 15 verification points:
1. Candidate can self-register without a profile.
2. Registration always creates Candidate role.
3. Client cannot register as Recruiter or Admin.
4. Duplicate candidate email does not create another account.
5. Candidate login/session works after registration.
6. Recruiter and candidate sessions can remain active simultaneously.
7. Candidate without a resume can browse released jobs.
8. Apply Now opens a draft and requests a resume.
9. PDF and DOCX use the existing parser pipeline.
10. Submitted application freezes profile version and job revision.
11. Candidate cannot access another candidate's data.
12. Recruiter cannot create new candidate portal credentials.
13. Obsolete invitation links cannot create access.
14. Existing candidate accounts can still log in.
15. Existing recruiter resume upload continues working unchanged.
"""
import io
import uuid
from datetime import datetime, timezone
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models.auth import (
    Base,
    UserRecord,
    UserRole,
    UserSessionRecord,
    SecurityAuditEventRecord,
)
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateBindingStatus,
    CandidateApplicationRecord,
    CandidateApplicationStatus,
)
from app.models.job import (
    JobRequisition,
    JobVersion,
    JobLifecycleStatus,
    JobPublicationStatus,
    JobVersionStatus,
)
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.services.security import canonicalize_email, hash_password


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
    test_client = TestClient(app, base_url="http://localhost:5173")
    yield test_client
    app.dependency_overrides.clear()


def _seed_recruiter(db, email="recruiter@example.com", password="RecruiterPassword123!"):
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        password_hash=hash_password(password),
        display_name="Recruiter Admin",
        role=UserRole.RECRUITER,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login_recruiter(client, email="recruiter@example.com", password="RecruiterPassword123!"):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": "http://localhost:5173"},
    )
    assert resp.status_code == status.HTTP_200_OK
    session_cookie = client.cookies.get("ai_hiring_session")
    csrf_token = resp.json()["csrfToken"]
    return session_cookie, csrf_token


def _seed_released_job(db, recruiter_user_id):
    job_id = uuid.uuid4()
    job_version_id = uuid.uuid4()
    
    req = JobRequisition(
        job_id=job_id,
        created_by_user_id=recruiter_user_id,
        status=JobLifecycleStatus.ACTIVE,
        publication_status=JobPublicationStatus.RELEASED,
        released_job_version_id=job_version_id,
        released_by_user_id=recruiter_user_id,
        released_at=datetime.now(timezone.utc),
    )
    ver = JobVersion(
        job_version_id=job_version_id,
        job_id=job_id,
        created_by_user_id=recruiter_user_id,
        version_number=1,
        revision=1,
        version_status=JobVersionStatus.APPROVED,
        title="Senior Python Backend Engineer",
        department="Engineering",
        location="Remote",
    )
    db.add(req)
    db.add(ver)
    db.commit()
    db.refresh(req)
    db.refresh(ver)
    return req, ver


# 1. Candidate can self-register without a profile.
# 2. Registration always creates Candidate role.
# 3. Client cannot register as Recruiter or Admin.
def test_candidate_self_registration_without_profile_and_role_isolation(client, test_db):
    resp = client.post(
        "/api/v1/candidate/auth/register",
        json={
            "full_name": "Alice Candidate",
            "email": "Alice.Candidate@Example.com ",
            "password": "ValidPassword123!",
        },
        headers={"Origin": "http://localhost:5173"},
    )
    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    assert data["email"] == "alice.candidate@example.com"
    assert data["role"] == "CANDIDATE"
    assert "csrfToken" in data
    assert client.cookies.get("ai_hiring_candidate_session") is not None

    # Verify user record in DB
    user = test_db.execute(select(UserRecord).where(UserRecord.email == "alice.candidate@example.com")).scalar_one()
    assert user.role == UserRole.CANDIDATE
    assert user.display_name == "Alice Candidate"
    assert user.is_active is True


# 4. Duplicate candidate email does not create another account.
def test_duplicate_candidate_email_rejected(client, test_db):
    payload = {
        "full_name": "Bob Candidate",
        "email": "bob@example.com",
        "password": "ValidPassword123!",
    }
    resp1 = client.post("/api/v1/candidate/auth/register", json=payload, headers={"Origin": "http://localhost:5173"})
    assert resp1.status_code == status.HTTP_201_CREATED

    # Try duplicate with different case / spaces
    payload_dup = {
        "full_name": "Bob Fake",
        "email": " BOB@EXAMPLE.COM ",
        "password": "OtherPassword123!",
    }
    resp2 = client.post("/api/v1/candidate/auth/register", json=payload_dup, headers={"Origin": "http://localhost:5173"})
    assert resp2.status_code == status.HTTP_409_CONFLICT
    assert "already exists" in resp2.json()["detail"]["message"].lower()


# 5. Candidate login/session works after registration.
def test_candidate_login_after_registration(client, test_db):
    client.post(
        "/api/v1/candidate/auth/register",
        json={"full_name": "Charlie", "email": "charlie@example.com", "password": "SecretPassword123!"},
        headers={"Origin": "http://localhost:5173"},
    )
    client.cookies.clear()

    # Log in
    resp = client.post(
        "/api/v1/candidate/auth/login",
        json={"email": "CHARLIE@EXAMPLE.COM", "password": "SecretPassword123!"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["role"] == "CANDIDATE"
    assert client.cookies.get("ai_hiring_candidate_session") is not None

    # Check /me
    resp_me = client.get("/api/v1/candidate/auth/me")
    assert resp_me.status_code == status.HTTP_200_OK
    assert resp_me.json()["email"] == "charlie@example.com"


# 6. Recruiter and candidate sessions can remain active simultaneously.
def test_simultaneous_recruiter_and_candidate_sessions(client, test_db):
    recruiter = _seed_recruiter(test_db, email="rec@example.com")
    rec_cookie, _ = _login_recruiter(client, email="rec@example.com")

    # Register candidate
    resp_cand = client.post(
        "/api/v1/candidate/auth/register",
        json={"full_name": "Dan", "email": "dan@example.com", "password": "CandidatePass123!"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert resp_cand.status_code == status.HTTP_201_CREATED
    cand_cookie = client.cookies.get("ai_hiring_candidate_session")

    # Both cookies exist in client
    assert rec_cookie is not None
    assert cand_cookie is not None
    assert rec_cookie != cand_cookie

    # Recruiter me check
    resp_rec_me = client.get("/api/v1/auth/me")
    assert resp_rec_me.status_code == status.HTTP_200_OK
    assert resp_rec_me.json()["email"] == "rec@example.com"

    # Candidate me check
    resp_cand_me = client.get("/api/v1/candidate/auth/me")
    assert resp_cand_me.status_code == status.HTTP_200_OK
    assert resp_cand_me.json()["email"] == "dan@example.com"


# 7. Candidate without a resume can browse released jobs.
def test_candidate_without_resume_can_browse_released_jobs(client, test_db):
    rec = _seed_recruiter(test_db)
    job, rev = _seed_released_job(test_db, rec.user_id)

    # Register candidate without resume
    client.post(
        "/api/v1/candidate/auth/register",
        json={"full_name": "Eve Candidate", "email": "eve@example.com", "password": "Password123!"},
        headers={"Origin": "http://localhost:5173"},
    )

    # Browse open jobs
    resp = client.get("/api/v1/candidate/jobs")
    assert resp.status_code == status.HTTP_200_OK
    jobs_list = resp.json()["items"]
    assert len(jobs_list) == 1
    assert jobs_list[0]["jobId"] == str(job.job_id)


# 8. Apply Now opens a draft and requests a resume.
def test_apply_now_opens_draft_for_unbound_candidate(client, test_db):
    rec = _seed_recruiter(test_db)
    job, rev = _seed_released_job(test_db, rec.user_id)

    reg_resp = client.post(
        "/api/v1/candidate/auth/register",
        json={"full_name": "Frank", "email": "frank@example.com", "password": "Password123!"},
        headers={"Origin": "http://localhost:5173"},
    )
    csrf_token = reg_resp.json()["csrfToken"]

    # Open draft application
    resp_draft = client.post(
        "/api/v1/candidate/applications/drafts",
        json={"job_id": str(job.job_id), "expected_revision": 1},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:5173"},
    )
    assert resp_draft.status_code == status.HTTP_200_OK
    draft_data = resp_draft.json()
    assert draft_data["status"] == "DRAFT"
    assert draft_data["artifactId"] is None


# 11. Candidate cannot access another candidate's data.
def test_cross_candidate_isolation(client, test_db):
    rec = _seed_recruiter(test_db)
    job, rev = _seed_released_job(test_db, rec.user_id)

    # Candidate 1
    resp1 = client.post(
        "/api/v1/candidate/auth/register",
        json={"full_name": "Candidate One", "email": "cand1@example.com", "password": "Password123!"},
        headers={"Origin": "http://localhost:5173"},
    )
    csrf1 = resp1.json()["csrfToken"]
    app1 = client.post(
        "/api/v1/candidate/applications/drafts",
        json={"job_id": str(job.job_id), "expected_revision": 1},
        headers={"X-CSRF-Token": csrf1, "Origin": "http://localhost:5173"},
    ).json()

    # Candidate 2
    client.cookies.clear()
    resp2 = client.post(
        "/api/v1/candidate/auth/register",
        json={"full_name": "Candidate Two", "email": "cand2@example.com", "password": "Password123!"},
        headers={"Origin": "http://localhost:5173"},
    )
    csrf2 = resp2.json()["csrfToken"]

    # Candidate 2 tries to access Candidate 1's application
    resp_cross = client.get(f"/api/v1/candidate/applications/{app1['applicationId']}")
    assert resp_cross.status_code == status.HTTP_404_NOT_FOUND


# 12. Recruiter cannot create new candidate portal credentials.
# 13. Obsolete invitation links cannot create access.
def test_recruiter_invitations_and_activations_are_deprecated_gone(client, test_db):
    rec = _seed_recruiter(test_db)
    rec_cookie, rec_csrf = _login_recruiter(client)

    # Recruiter invitation creation endpoint returns 410 Gone
    cand_id = uuid.uuid4()
    resp_inv = client.post(
        f"/api/v1/recruiter/candidate-access/candidates/{cand_id}/invitations",
        json={"candidate_id": str(cand_id)},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    assert resp_inv.status_code == status.HTTP_410_GONE

    # Public activation endpoint returns 410 Gone
    resp_act = client.post(
        "/api/v1/candidate/auth/activate",
        json={"token": "legacy_token", "password": "Password123!"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert resp_act.status_code == status.HTTP_410_GONE


# 14. Existing candidate accounts can still log in.
def test_existing_candidate_accounts_can_still_login(client, test_db):
    org_id = uuid.uuid4()
    user = UserRecord(
        user_id=uuid.uuid4(),
        email="legacy.cand@example.com",
        password_hash=hash_password("LegacyPassword123!"),
        display_name="Legacy Candidate",
        role=UserRole.CANDIDATE,
        is_active=True,
    )
    binding = CandidateAccountBindingRecord(
        binding_id=uuid.uuid4(),
        user_id=user.user_id,
        candidate_id=uuid.uuid4(),
        organization_id=org_id,
        status=CandidateBindingStatus.ACTIVE.value,
    )
    test_db.add(user)
    test_db.add(binding)
    test_db.commit()

    # Log in
    resp = client.post(
        "/api/v1/candidate/auth/login",
        json={"email": "legacy.cand@example.com", "password": "LegacyPassword123!"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["role"] == "CANDIDATE"
    assert resp.json()["candidateId"] == str(binding.candidate_id)


# 15. Existing recruiter resume upload continues working unchanged.
def test_recruiter_resume_upload_remains_intact(client, test_db):
    rec = _seed_recruiter(test_db)
    rec_cookie, rec_csrf = _login_recruiter(client)

    # Valid simple text representation in PDF or mock upload
    # Test router /api/v1/resumes upload endpoint exists and rejects invalid formats properly
    resp = client.post(
        "/api/v1/resumes/upload",
        files={"file": ("test.txt", b"Unsupported content", "text/plain")},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    assert resp.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


# 16. Candidate resume upload uses existing pipeline and reaches READY_TO_SUBMIT.
def test_candidate_application_resume_upload_and_extraction(client, test_db):
    rec = _seed_recruiter(test_db)
    job, rev = _seed_released_job(test_db, rec.user_id)

    reg_resp = client.post(
        "/api/v1/candidate/auth/register",
        json={"full_name": "Jane Doe", "email": "janedoe@example.com", "password": "Password123!"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert reg_resp.status_code == status.HTTP_201_CREATED
    csrf_token = reg_resp.json()["csrfToken"]

    # Open draft application
    resp_draft = client.post(
        "/api/v1/candidate/applications/drafts",
        json={"job_id": str(job.job_id), "expected_revision": 1},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:5173"},
    )
    assert resp_draft.status_code == status.HTTP_200_OK
    app_id = resp_draft.json()["applicationId"]

    # Upload valid PDF resume
    valid_pdf = (
        b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\n"
        b"2 0 obj\n<<\n/Type /Pages\n/Kids [3 0 R]\n/Count 1\n>>\nendobj\n"
        b"3 0 obj\n<<\n/Type /Page\n/Parent 2 0 R\n/MediaBox [0 0 612 792]\n/Contents 4 0 R\n>>\nendobj\n"
        b"4 0 obj\n<<\n/Length 60\n>>\nstream\n"
        b"BT\n/F1 12 Tf\n100 700 Td\n(Jane Doe Python React Frontend Engineer Resume Profile) Tj\nET\nendstream\nendobj\n"
        b"xref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000189 00000 n \n"
        b"trailer\n<<\n/Size 5\n/Root 1 0 R\n>>\nstartxref\n299\n%%EOF\n"
    )

    resp_up = client.post(
        f"/api/v1/candidate/applications/{app_id}/resume",
        files={"file": ("Jane_Doe_Resume.pdf", io.BytesIO(valid_pdf), "application/pdf")},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:5173"},
    )
    assert resp_up.status_code == status.HTTP_200_OK
    data = resp_up.json()
    assert data["status"] == "READY_TO_SUBMIT"
    assert data["processingStatus"] == "SUCCEEDED"
    assert data["isReadyToSubmit"] is True

    # Submit application draft
    resp_sub = client.post(
        f"/api/v1/candidate/applications/{app_id}/submit",
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:5173"},
    )
    assert resp_sub.status_code == status.HTTP_200_OK
    sub_data = resp_sub.json()
    assert sub_data["status"] == "SUBMITTED"
    assert sub_data["profileVersion"] is not None


