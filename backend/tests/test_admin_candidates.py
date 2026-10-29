import uuid
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models.auth import Base, UserRecord, UserRole
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.resume import ResumeArtifact, ArtifactFormat, ArtifactValidationStatus
from app.models.candidate_access import CandidateAccountBindingRecord, CandidateApplicationRecord, CandidateApplicationStatus
from app.services.security import hash_password


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


def _create_admin(db):
    admin = UserRecord(
        user_id=uuid.uuid4(),
        email="admin.candidate.test@example.com",
        display_name="Admin Test",
        password_hash=hash_password("AdminPass123!"),
        role=UserRole.ADMIN,
        is_active=True,
        must_change_password=False,
    )
    db.add(admin)
    db.commit()
    return admin


def _login_admin(client, email="admin.candidate.test@example.com", password="AdminPass123!"):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == status.HTTP_200_OK
    csrf = resp.json()["csrfToken"]
    cookie = resp.cookies.get("ai_hiring_session")
    return csrf, cookie


def test_admin_candidates_list_and_filter(client, test_db):
    admin = _create_admin(test_db)
    csrf, cookie = _login_admin(client)
    client.cookies.set("ai_hiring_session", cookie)

    # Create internal staff user
    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email="recruiter.internal@example.com",
        display_name="Recruiter Internal",
        password_hash=hash_password("RecruiterPass123!"),
        role=UserRole.RECRUITER,
        is_active=True,
        must_change_password=False,
    )
    test_db.add(recruiter)

    # Create candidate resume and identity
    art = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="sample_candidate.pdf",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    test_db.add(art)
    test_db.flush()

    ident = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=art.artifact_id,
        full_name="John Candidate",
        primary_email="john.candidate@example.com",
        identity_payload={"full_name": "John Candidate"},
    )
    test_db.add(ident)
    test_db.commit()

    # 1. Platform users should not contain candidate by default
    users_resp = client.get("/api/v1/admin/users")
    assert users_resp.status_code == status.HTTP_200_OK
    user_emails = [u["email"] for u in users_resp.json()]
    assert "admin.candidate.test@example.com" in user_emails
    assert "recruiter.internal@example.com" in user_emails
    assert "john.candidate@example.com" not in user_emails

    # 2. Candidates endpoint lists John Candidate
    cands_resp = client.get("/api/v1/admin/candidates")
    assert cands_resp.status_code == status.HTTP_200_OK
    cands = cands_resp.json()
    assert len(cands) >= 1
    cand_match = next((c for c in cands if c["email"] == "john.candidate@example.com"), None)
    assert cand_match is not None
    assert cand_match["fullName"] == "John Candidate"
    assert cand_match["isActive"] is True


def test_admin_deactivate_and_drop_candidate(client, test_db):
    admin = _create_admin(test_db)
    csrf, cookie = _login_admin(client)
    client.cookies.set("ai_hiring_session", cookie)

    art = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="delete_me.pdf",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    test_db.add(art)
    test_db.flush()

    ident = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=art.artifact_id,
        full_name="Deletable Candidate",
        primary_email="deletable@example.com",
        identity_payload={"full_name": "Deletable Candidate"},
    )
    test_db.add(ident)

    cand_user = UserRecord(
        user_id=uuid.uuid4(),
        email="deletable@example.com",
        display_name="Deletable Candidate",
        password_hash=hash_password("CandidatePass123!"),
        role=UserRole.CANDIDATE,
        is_active=True,
        must_change_password=False,
    )
    test_db.add(cand_user)
    test_db.commit()

    headers = {"X-CSRF-Token": csrf, "Origin": "http://localhost:5173"}

    # 1. Deactivate candidate
    deact_resp = client.patch(
        f"/api/v1/admin/candidates/{ident.identity_id}",
        json={"is_active": False},
        headers=headers,
    )
    assert deact_resp.status_code == status.HTTP_200_OK
    assert deact_resp.json()["isActive"] is False

    # Verify user is deactivated in db
    test_db.refresh(cand_user)
    assert cand_user.is_active is False

    # 2. Permanently drop candidate
    drop_resp = client.delete(
        f"/api/v1/admin/candidates/{ident.identity_id}",
        headers=headers,
    )
    assert drop_resp.status_code == status.HTTP_200_OK
    assert "dropped successfully" in drop_resp.json()["message"]

    # Verify records are gone
    ident_check = test_db.execute(
        select(CandidateIdentityRecord).where(CandidateIdentityRecord.identity_id == ident.identity_id)
    ).scalar_one_or_none()
    assert ident_check is None

    art_check = test_db.execute(
        select(ResumeArtifact).where(ResumeArtifact.artifact_id == art.artifact_id)
    ).scalar_one_or_none()
    assert art_check is None

    user_check = test_db.execute(
        select(UserRecord).where(UserRecord.user_id == cand_user.user_id)
    ).scalar_one_or_none()
    assert user_check is None


def test_admin_candidates_shows_exact_account_email_id(client, test_db):
    admin = _create_admin(test_db)
    csrf, cookie = _login_admin(client)
    client.cookies.set("ai_hiring_session", cookie)

    # 1. Create candidate user with exact email ID "zc@1.com"
    cand_user = UserRecord(
        user_id=uuid.uuid4(),
        email="zc@1.com",
        display_name="Zayaan Contractor",
        password_hash=hash_password("ZayaanPass123!"),
        role=UserRole.CANDIDATE,
        is_active=True,
        must_change_password=False,
    )
    test_db.add(cand_user)

    # 2. Create resume artifact and identity with extracted resume email "zayaan.contractor@example.com"
    art = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="zayaan_resume.pdf",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    test_db.add(art)
    test_db.flush()

    ident = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=art.artifact_id,
        full_name="Zayaan Contractor",
        primary_email="zayaan.contractor@example.com",
        identity_payload={"full_name": "Zayaan Contractor"},
    )
    test_db.add(ident)
    test_db.flush()

    # 3. Bind account to identity
    binding = CandidateAccountBindingRecord(
        binding_id=uuid.uuid4(),
        user_id=cand_user.user_id,
        candidate_id=ident.identity_id,
        organization_id=uuid.uuid4(),
        status="ACTIVE",
    )
    test_db.add(binding)
    test_db.commit()

    # 4. Fetch candidates via admin API
    resp = client.get("/api/v1/admin/candidates")
    assert resp.status_code == status.HTTP_200_OK
    cands = resp.json()
    zayaan = next((c for c in cands if c["fullName"] == "Zayaan Contractor"), None)
    assert zayaan is not None
    # Crucial assertion: the candidate email displayed in the admin portal must be the exact user account ID ("zc@1.com")
    assert zayaan["email"] == "zc@1.com"
    assert zayaan["accountEmail"] == "zc@1.com"
    assert zayaan["resumeEmail"] == "zayaan.contractor@example.com"
    assert zayaan["hasAccount"] is True
