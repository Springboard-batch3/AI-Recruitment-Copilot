import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.database import get_db
from app.main import app
from app.models.auth import (
    Base,
    SecurityAuditEventRecord,
    UserRecord,
    UserRole,
    UserSessionRecord,
)
from app.models.candidate import CandidateIdentityRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateBindingStatus,
    CandidateInvitationRecord,
)
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.services.security import (
    canonicalize_email,
    hash_password,
    hash_token,
    verify_password,
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
    yield TestClient(app, base_url="http://testserver")
    app.dependency_overrides.clear()


def _seed_user(db, email: str, role: UserRole, password: str = "Password123!") -> UserRecord:
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        display_name=f"{role.value} User",
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        must_change_password=False,
        failed_login_count=0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _seed_candidate_identity(db, full_name: str = "Jane Candidate", email: str = "jane.candidate@example.com") -> CandidateIdentityRecord:
    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="resume.pdf",
        detected_format=ArtifactFormat.pdf,
        size_bytes=1024,
        sha256_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    db.add(artifact)
    db.commit()

    identity = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        full_name=full_name,
        primary_email=email,
        primary_phone="+1234567890",
        identity_payload={"skills": ["Python", "React"]},
    )
    db.add(identity)
    db.commit()
    db.refresh(identity)
    return identity


def _login_recruiter(client, db, email: str = "recruiter@company.test", password: str = "Password123!"):
    user = _seed_user(db, email, UserRole.RECRUITER, password)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == status.HTTP_200_OK
    return user, resp.cookies.get("ai_hiring_session"), resp.json()["csrfToken"]


def test_recruiter_creates_invitation_success(client, test_db):
    recruiter, rec_cookie, rec_csrf = _login_recruiter(client, test_db)
    cand = _seed_candidate_identity(test_db)

    client.cookies.set("ai_hiring_session", rec_cookie)
    resp = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={
            "candidate_id": str(cand.identity_id),
            "valid_hours": 24,
        },
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()

    assert data["candidateId"] == str(cand.identity_id)
    assert "rawToken" in data
    assert len(data["rawToken"]) >= 32
    assert "#token=" in data["activationUrl"]

    # Verify only hash is stored in DB
    inv_id = uuid.UUID(data["invitationId"])
    db_inv = test_db.execute(
        select(CandidateInvitationRecord).where(CandidateInvitationRecord.invitation_id == inv_id)
    ).scalar_one()
    assert db_inv.token_hash == hash_token(data["rawToken"])
    assert db_inv.token_hash != data["rawToken"]


def test_unauthorized_user_cannot_create_invitation(client, test_db):
    cand = _seed_candidate_identity(test_db)

    # 1. Unauthenticated
    resp_unauth = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={"candidate_id": str(cand.identity_id)},
    )
    assert resp_unauth.status_code == status.HTTP_401_UNAUTHORIZED

    # 2. Reviewer role denied
    reviewer = _seed_user(test_db, "reviewer@company.test", UserRole.REVIEWER)
    resp_login = client.post(
        "/api/v1/auth/login",
        json={"email": "reviewer@company.test", "password": "Password123!"},
    )
    client.cookies.set("ai_hiring_session", resp_login.cookies.get("ai_hiring_session"))
    resp_denied = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={"candidate_id": str(cand.identity_id)},
        headers={"X-CSRF-Token": resp_login.json()["csrfToken"], "Origin": "http://localhost:5173"},
    )
    assert resp_denied.status_code == status.HTTP_403_FORBIDDEN


def test_invitation_for_nonexistent_candidate_rejected(client, test_db):
    recruiter, rec_cookie, rec_csrf = _login_recruiter(client, test_db)
    client.cookies.set("ai_hiring_session", rec_cookie)

    fake_id = str(uuid.uuid4())
    resp = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={"candidate_id": fake_id},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert resp.json()["detail"]["code"] == "CANDIDATE_NOT_FOUND"


def test_recruiter_can_revoke_invitation(client, test_db):
    recruiter, rec_cookie, rec_csrf = _login_recruiter(client, test_db)
    cand = _seed_candidate_identity(test_db)
    client.cookies.set("ai_hiring_session", rec_cookie)

    # Create invitation
    resp_create = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={"candidate_id": str(cand.identity_id)},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    inv_id = resp_create.json()["invitationId"]
    raw_token = resp_create.json()["rawToken"]

    # Revoke invitation
    resp_revoke = client.post(
        f"/api/v1/recruiter/candidate-invitations/{inv_id}/revoke",
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    assert resp_revoke.status_code == status.HTTP_200_OK
    assert resp_revoke.json()["status"] == "REVOKED"

    # Try activating with revoked invitation -> must fail
    resp_act = client.post(
        "/api/v1/candidate/auth/activate",
        json={
            "token": raw_token,
            "password": "NewCandidatePassword123!",
            "password_confirmation": "NewCandidatePassword123!",
        },
    )
    assert resp_act.status_code == status.HTTP_400_BAD_REQUEST
    assert resp_act.json()["detail"]["code"] == "INVALID_OR_EXPIRED_INVITATION"


def test_candidate_account_activation_lifecycle(client, test_db):
    recruiter, rec_cookie, rec_csrf = _login_recruiter(client, test_db)
    cand = _seed_candidate_identity(test_db, full_name="Alice Smith", email="alice.smith@example.com")
    client.cookies.set("ai_hiring_session", rec_cookie)

    # 1. Recruiter creates invitation
    resp_create = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={"candidate_id": str(cand.identity_id)},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    raw_token = resp_create.json()["rawToken"]

    # 2. Candidate activates account
    client.cookies.clear()
    resp_activate = client.post(
        "/api/v1/candidate/auth/activate",
        json={
            "token": raw_token,
            "password": "CandidatePassword123!",
            "password_confirmation": "CandidatePassword123!",
        },
    )
    assert resp_activate.status_code == status.HTTP_200_OK
    act_data = resp_activate.json()

    assert act_data["email"] == "alice.smith@example.com"
    assert act_data["displayName"] == "Alice Smith"
    assert act_data["role"] == "CANDIDATE"
    assert act_data["candidateId"] == str(cand.identity_id)
    assert act_data["bindingStatus"] == "ACTIVE"

    cand_cookie = resp_activate.cookies.get("ai_hiring_candidate_session")
    assert cand_cookie is not None
    cand_csrf = act_data["csrfToken"]

    # Verify cookie attributes
    set_cookie_hdr = resp_activate.headers.get("set-cookie", "")
    assert "ai_hiring_candidate_session=" in set_cookie_hdr
    assert "httponly" in set_cookie_hdr.lower()
    assert "samesite=lax" in set_cookie_hdr.lower()
    assert "domain=" not in set_cookie_hdr.lower()

    # Verify password was hashed with scrypt
    user = test_db.execute(select(UserRecord).where(UserRecord.email == "alice.smith@example.com")).scalar_one()
    assert verify_password("CandidatePassword123!", user.password_hash)

    # 3. GET /api/v1/candidate/auth/me
    client.cookies.set("ai_hiring_candidate_session", cand_cookie)
    resp_me = client.get("/api/v1/candidate/auth/me")
    assert resp_me.status_code == status.HTTP_200_OK
    me_data = resp_me.json()
    assert me_data["role"] == "CANDIDATE"
    assert me_data["email"] == "alice.smith@example.com"
    assert "password_hash" not in me_data

    # 4. Single-use: Reusing the same token must fail
    resp_reuse = client.post(
        "/api/v1/candidate/auth/activate",
        json={
            "token": raw_token,
            "password": "AnotherPassword123!",
            "password_confirmation": "AnotherPassword123!",
        },
    )
    assert resp_reuse.status_code == status.HTTP_400_BAD_REQUEST
    assert resp_reuse.json()["detail"]["code"] == "INVALID_OR_EXPIRED_INVITATION"


def test_candidate_login_and_logout_lifecycle(client, test_db):
    recruiter, rec_cookie, rec_csrf = _login_recruiter(client, test_db)
    cand = _seed_candidate_identity(test_db, full_name="Bob Jones", email="bob.jones@example.com")
    client.cookies.set("ai_hiring_session", rec_cookie)

    resp_create = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={"candidate_id": str(cand.identity_id)},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    raw_token = resp_create.json()["rawToken"]

    # Activate
    client.cookies.clear()
    client.post(
        "/api/v1/candidate/auth/activate",
        json={
            "token": raw_token,
            "password": "BobSecurePassword123!",
            "password_confirmation": "BobSecurePassword123!",
        },
    )

    # 1. Candidate Login with wrong password -> Generic error
    resp_bad_pw = client.post(
        "/api/v1/candidate/auth/login",
        json={"email": "bob.jones@example.com", "password": "WrongPassword!"},
    )
    assert resp_bad_pw.status_code == status.HTTP_401_UNAUTHORIZED
    assert resp_bad_pw.json()["detail"]["code"] == "INVALID_CREDENTIALS"

    # 2. Recruiter trying to log in via Candidate endpoint -> Rejected with generic error
    resp_rec_on_cand = client.post(
        "/api/v1/candidate/auth/login",
        json={"email": "recruiter@company.test", "password": "Password123!"},
    )
    assert resp_rec_on_cand.status_code == status.HTTP_401_UNAUTHORIZED

    # 3. Candidate Login with valid password
    resp_login = client.post(
        "/api/v1/candidate/auth/login",
        json={"email": "bob.jones@example.com", "password": "BobSecurePassword123!"},
    )
    assert resp_login.status_code == status.HTTP_200_OK
    cand_cookie = resp_login.cookies.get("ai_hiring_candidate_session")
    cand_csrf = resp_login.json()["csrfToken"]

    # 4. Candidate Logout with CSRF
    client.cookies.set("ai_hiring_candidate_session", cand_cookie)
    resp_logout = client.post(
        "/api/v1/candidate/auth/logout",
        headers={"X-CSRF-Token": cand_csrf, "Origin": "http://localhost:5173"},
    )
    assert resp_logout.status_code == status.HTTP_204_NO_CONTENT

    # Session is now revoked
    resp_me_after = client.get("/api/v1/candidate/auth/me")
    assert resp_me_after.status_code == status.HTTP_401_UNAUTHORIZED


def test_deny_by_default_candidate_role_enforcement(client, test_db):
    """
    Ensures that an authenticated Candidate is strictly denied from accessing
    Admin, Recruiter, ATS, Jobs, Resumes, Match, and AI interview routes.
    """
    recruiter, rec_cookie, rec_csrf = _login_recruiter(client, test_db)
    cand = _seed_candidate_identity(test_db, full_name="Charlie Candidate", email="charlie@example.com")
    client.cookies.set("ai_hiring_session", rec_cookie)

    resp_create = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={"candidate_id": str(cand.identity_id)},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    raw_token = resp_create.json()["rawToken"]

    # Activate candidate
    client.cookies.clear()
    resp_act = client.post(
        "/api/v1/candidate/auth/activate",
        json={
            "token": raw_token,
            "password": "CharliePassword123!",
            "password_confirmation": "CharliePassword123!",
        },
    )
    cand_cookie = resp_act.cookies.get("ai_hiring_candidate_session")
    cand_csrf = resp_act.json()["csrfToken"]

    # Candidate attempts accessing Recruiter / Admin endpoints
    # 1. Admin users endpoint
    client.cookies.set("ai_hiring_candidate_session", cand_cookie)
    resp_admin = client.get("/api/v1/admin/users")
    assert resp_admin.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    # 2. Resumes / candidate directory endpoint
    resp_resumes = client.get("/api/v1/resumes")
    assert resp_resumes.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    # 3. Jobs endpoint
    resp_jobs = client.get("/api/v1/jobs")
    assert resp_jobs.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    # 4. ATS connections endpoint
    resp_ats = client.get("/api/v1/ats/connections")
    assert resp_ats.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    # 5. AI interviews endpoint
    resp_ai = client.get("/api/v1/ai-interviews/templates")
    assert resp_ai.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)


def test_suspended_candidate_binding_denies_access(client, test_db):
    recruiter, rec_cookie, rec_csrf = _login_recruiter(client, test_db)
    cand = _seed_candidate_identity(test_db, full_name="Suspended User", email="suspended@example.com")
    client.cookies.set("ai_hiring_session", rec_cookie)

    resp_create = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={"candidate_id": str(cand.identity_id)},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    raw_token = resp_create.json()["rawToken"]

    # Activate
    client.cookies.clear()
    resp_act = client.post(
        "/api/v1/candidate/auth/activate",
        json={
            "token": raw_token,
            "password": "SuspendedPassword123!",
            "password_confirmation": "SuspendedPassword123!",
        },
    )
    cand_cookie = resp_act.cookies.get("ai_hiring_candidate_session")

    # Suspend binding in database
    binding = test_db.execute(
        select(CandidateAccountBindingRecord).where(CandidateAccountBindingRecord.candidate_id == cand.identity_id)
    ).scalar_one()
    binding.status = CandidateBindingStatus.SUSPENDED.value
    test_db.commit()

    # Access /candidate/auth/me -> Must return 403 FORBIDDEN
    client.cookies.set("ai_hiring_candidate_session", cand_cookie)
    resp_me = client.get("/api/v1/candidate/auth/me")
    assert resp_me.status_code == status.HTTP_403_FORBIDDEN
    assert resp_me.json()["detail"]["code"] == "CANDIDATE_ACCESS_SUSPENDED"


def test_simultaneous_recruiter_and_candidate_sessions_isolated(client, test_db):
    """
    Verifies that simultaneous Recruiter and Candidate sessions exist independently
    using separate cookies, and logging out one does not invalidate the other.
    """
    recruiter, rec_cookie, rec_csrf = _login_recruiter(client, test_db)
    cand = _seed_candidate_identity(test_db, full_name="David Davis", email="david@example.com")

    client.cookies.set("ai_hiring_session", rec_cookie)
    resp_create = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={"candidate_id": str(cand.identity_id)},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    raw_token = resp_create.json()["rawToken"]

    # Candidate activates
    resp_act = client.post(
        "/api/v1/candidate/auth/activate",
        json={
            "token": raw_token,
            "password": "DavidPassword123!",
            "password_confirmation": "DavidPassword123!",
        },
    )
    cand_cookie = resp_act.cookies.get("ai_hiring_candidate_session")
    cand_csrf = resp_act.json()["csrfToken"]

    # Both cookies are present simultaneously
    client.cookies.set("ai_hiring_session", rec_cookie)
    client.cookies.set("ai_hiring_candidate_session", cand_cookie)

    # Recruiter me check
    resp_rec_me = client.get("/api/v1/auth/me")
    assert resp_rec_me.status_code == status.HTTP_200_OK
    assert resp_rec_me.json()["role"] == "RECRUITER"

    # Candidate me check
    resp_cand_me = client.get("/api/v1/candidate/auth/me")
    assert resp_cand_me.status_code == status.HTTP_200_OK
    assert resp_cand_me.json()["role"] == "CANDIDATE"
    cand_fresh_csrf = resp_cand_me.json()["csrfToken"]

    # Candidate logs out
    resp_logout_cand = client.post(
        "/api/v1/candidate/auth/logout",
        headers={"X-CSRF-Token": cand_fresh_csrf, "Origin": "http://localhost:5173"},
    )
    assert resp_logout_cand.status_code == status.HTTP_204_NO_CONTENT

    # Recruiter session remains active and valid!
    resp_rec_me_after = client.get("/api/v1/auth/me")
    assert resp_rec_me_after.status_code == status.HTTP_200_OK
    assert resp_rec_me_after.json()["role"] == "RECRUITER"


def test_expired_invitation_rejected(client, test_db):
    recruiter, rec_cookie, rec_csrf = _login_recruiter(client, test_db)
    cand = _seed_candidate_identity(test_db, full_name="Expired User", email="expired@example.com")
    client.cookies.set("ai_hiring_session", rec_cookie)

    resp_create = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={"candidate_id": str(cand.identity_id), "valid_hours": 1},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    raw_token = resp_create.json()["rawToken"]
    inv_id = uuid.UUID(resp_create.json()["invitationId"])

    # Fast forward expiration in DB
    inv = test_db.execute(
        select(CandidateInvitationRecord).where(CandidateInvitationRecord.invitation_id == inv_id)
    ).scalar_one()
    inv.expires_at = datetime.now(timezone.utc) - timedelta(hours=2)
    test_db.commit()

    # Attempt activation
    resp_act = client.post(
        "/api/v1/candidate/auth/activate",
        json={
            "token": raw_token,
            "password": "Password123!",
            "password_confirmation": "Password123!",
        },
    )
    assert resp_act.status_code == status.HTTP_400_BAD_REQUEST
    assert resp_act.json()["detail"]["code"] == "INVALID_OR_EXPIRED_INVITATION"


def test_invitation_cooldown_active_rejected(client, test_db):
    recruiter, rec_cookie, rec_csrf = _login_recruiter(client, test_db)
    cand = _seed_candidate_identity(test_db, full_name="Cooldown User", email="cooldown@example.com")
    client.cookies.set("ai_hiring_session", rec_cookie)

    # First invitation
    resp1 = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={"candidate_id": str(cand.identity_id)},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    assert resp1.status_code == status.HTTP_201_CREATED

    # Second invitation immediately after -> must trigger 409 Conflict cooldown
    resp2 = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={"candidate_id": str(cand.identity_id)},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    assert resp2.status_code == status.HTTP_409_CONFLICT
    assert resp2.json()["detail"]["code"] == "INVITATION_COOLDOWN_ACTIVE"


def test_list_candidate_invitations_safe_summary(client, test_db):
    recruiter, rec_cookie, rec_csrf = _login_recruiter(client, test_db)
    cand = _seed_candidate_identity(test_db, full_name="List User", email="list@example.com")
    client.cookies.set("ai_hiring_session", rec_cookie)

    resp_create = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={"candidate_id": str(cand.identity_id)},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    assert resp_create.status_code == status.HTTP_201_CREATED

    # List summary
    resp_list = client.get(f"/api/v1/recruiter/candidate-invitations/{cand.identity_id}")
    assert resp_list.status_code == status.HTTP_200_OK
    summaries = resp_list.json()
    assert len(summaries) >= 1
    assert summaries[0]["status"] == "ACTIVE"
    assert "tokenHash" not in summaries[0]
    assert "rawToken" not in summaries[0]


def test_candidate_activation_mass_assignment_rejected(client, test_db):
    recruiter, rec_cookie, rec_csrf = _login_recruiter(client, test_db)
    cand = _seed_candidate_identity(test_db, full_name="Mass Assignment", email="mass@example.com")
    client.cookies.set("ai_hiring_session", rec_cookie)

    resp_create = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={"candidate_id": str(cand.identity_id)},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    raw_token = resp_create.json()["rawToken"]

    # Attempt mass assignment of role, organization, etc.
    resp_mass = client.post(
        "/api/v1/candidate/auth/activate",
        json={
            "token": raw_token,
            "password": "Password123!",
            "password_confirmation": "Password123!",
            "role": "ADMIN",
            "organization_id": str(uuid.uuid4()),
            "is_active": True,
        },
    )
    assert resp_mass.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_candidate_csrf_failure_blocks_logout(client, test_db):
    recruiter, rec_cookie, rec_csrf = _login_recruiter(client, test_db)
    cand = _seed_candidate_identity(test_db, full_name="CSRF Test", email="csrf@example.com")
    client.cookies.set("ai_hiring_session", rec_cookie)

    resp_create = client.post(
        "/api/v1/recruiter/candidate-invitations",
        json={"candidate_id": str(cand.identity_id)},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    raw_token = resp_create.json()["rawToken"]

    # Activate
    client.cookies.clear()
    resp_act = client.post(
        "/api/v1/candidate/auth/activate",
        json={
            "token": raw_token,
            "password": "Password123!",
            "password_confirmation": "Password123!",
        },
    )
    cand_cookie = resp_act.cookies.get("ai_hiring_candidate_session")

    # 1. Missing CSRF token
    client.cookies.set("ai_hiring_candidate_session", cand_cookie)
    resp_no_csrf = client.post("/api/v1/candidate/auth/logout")
    assert resp_no_csrf.status_code == status.HTTP_403_FORBIDDEN
    assert resp_no_csrf.json()["detail"]["code"] == "CSRF_DENIED"

    # 2. Invalid CSRF token
    resp_bad_csrf = client.post(
        "/api/v1/candidate/auth/logout",
        headers={"X-CSRF-Token": "invalid_fake_csrf_token", "Origin": "http://localhost:5173"},
    )
    assert resp_bad_csrf.status_code == status.HTTP_403_FORBIDDEN
    assert resp_bad_csrf.json()["detail"]["code"] == "CSRF_DENIED"


def test_revoke_candidate_access_endpoint(client, test_db):
    recruiter, rec_cookie, rec_csrf = _login_recruiter(client, test_db)
    cand = _seed_candidate_identity(test_db, full_name="Revoke Me", email="revokeme@example.com")
    client.cookies.set("ai_hiring_session", rec_cookie)

    # 1. Create invitation
    resp_create = client.post(
        "/api/v1/recruiter/candidate-access/candidates/" + str(cand.identity_id) + "/invitations",
        json={"candidate_id": str(cand.identity_id)},
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    assert resp_create.status_code == status.HTTP_201_CREATED
    raw_token = resp_create.json()["rawToken"]

    # 2. Activate candidate account
    client.cookies.clear()
    resp_act = client.post(
        "/api/v1/candidate/auth/activate",
        json={
            "token": raw_token,
            "password": "Password123!",
            "password_confirmation": "Password123!",
        },
    )
    assert resp_act.status_code == status.HTTP_200_OK

    # 3. Check access status as recruiter -> ACTIVE
    client.cookies.set("ai_hiring_session", rec_cookie)
    resp_status = client.get(
        f"/api/v1/recruiter/candidate-access/candidates/{cand.identity_id}"
    )
    assert resp_status.status_code == status.HTTP_200_OK
    assert resp_status.json()["accountStatus"] == "ACTIVE"
    assert resp_status.json()["hasActiveAccount"] is True

    # 4. Revoke candidate access
    resp_revoke = client.post(
        f"/api/v1/recruiter/candidate-access/candidates/{cand.identity_id}/revoke",
        headers={"X-CSRF-Token": rec_csrf, "Origin": "http://localhost:5173"},
    )
    assert resp_revoke.status_code == status.HTTP_200_OK
    assert resp_revoke.json()["accountStatus"] == "SUSPENDED"
    assert resp_revoke.json()["hasActiveAccount"] is False

    # 5. Candidate login is now forbidden/denied
    client.cookies.clear()
    resp_login = client.post(
        "/api/v1/candidate/auth/login",
        json={"email": "revokeme@example.com", "password": "Password123!"},
    )
    assert resp_login.status_code == status.HTTP_401_UNAUTHORIZED

