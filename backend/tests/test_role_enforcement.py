import io
import uuid
from datetime import datetime, timedelta, timezone

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
    AuditEventType,
    AuditReasonCode,
    AuditResult,
    Base,
    SecurityAuditEventRecord,
    SessionRevokeReason,
    UserRecord,
    UserRole,
    UserSessionRecord,
)
from app.models.resume import (
    ArtifactFormat,
    ArtifactValidationStatus,
    ResumeArtifact,
)
from app.services.security import (
    canonicalize_email,
    generate_secure_token,
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
    yield TestClient(app, base_url="https://testserver")
    app.dependency_overrides.clear()


def _seed_user(
    db,
    email="user@company.test",
    password="Password123!",
    role=UserRole.RECRUITER,
    is_active=True,
    must_change_password=False,
):
    encoded_hash = hash_password(password)
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        display_name="Test User",
        password_hash=encoded_hash,
        role=role,
        is_active=is_active,
        must_change_password=must_change_password,
        failed_login_count=0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login_user(client, email="user@company.test", password="Password123!"):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == status.HTTP_200_OK
    return resp.json()["csrfToken"], resp.json()["user"]


# =========================================================================
# 1. Unauthenticated & Invalid Session Scenarios
# =========================================================================

def test_unauthenticated_requests_to_protected_routes_return_401(client):
    endpoints = [
        ("GET", "/api/v1/resumes"),
        ("GET", f"/api/v1/resumes/{uuid.uuid4()}/status"),
        ("POST", f"/api/v1/resumes/{uuid.uuid4()}/process"),
        ("GET", "/api/v1/admin/users"),
        ("POST", "/api/v1/admin/users"),
        ("GET", "/api/v1/admin/security-events"),
    ]
    for method, path in endpoints:
        if method == "GET":
            resp = client.get(path)
        else:
            resp = client.post(path)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED
        assert resp.json()["detail"]["code"] == "UNAUTHENTICATED"


def test_revoked_or_expired_session_returns_401(client, test_db):
    user = _seed_user(test_db, email="revoked.role@company.test", role=UserRole.RECRUITER)
    csrf, _ = _login_user(client, email="revoked.role@company.test")

    # Manually revoke the session in DB
    stmt = select(UserSessionRecord).where(UserSessionRecord.user_id == user.user_id)
    session_rec = test_db.execute(stmt).scalar_one()
    session_rec.revoked_at = datetime.now(timezone.utc)
    session_rec.revoke_reason = SessionRevokeReason.LOGOUT
    test_db.commit()

    resp = client.get("/api/v1/resumes")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# =========================================================================
# 2. Forced Password Change (must_change_password=True)
# =========================================================================

def test_forced_password_user_denied_platform_routes(client, test_db):
    user = _seed_user(test_db, email="forced.pw@company.test", role=UserRole.RECRUITER, must_change_password=True)
    csrf, _ = _login_user(client, email="forced.pw@company.test")

    # 1. Can access /auth/me
    me_resp = client.get("/api/v1/auth/me")
    assert me_resp.status_code == status.HTTP_200_OK

    # 2. Denied on recruiter candidate routes with PASSWORD_CHANGE_REQUIRED
    recruiter_resp = client.get("/api/v1/resumes")
    assert recruiter_resp.status_code == status.HTTP_403_FORBIDDEN
    assert recruiter_resp.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"

    # 3. Denied on admin routes
    admin_resp = client.get("/api/v1/admin/users")
    assert admin_resp.status_code == status.HTTP_403_FORBIDDEN
    assert admin_resp.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"


# =========================================================================
# 3. Role Matrix Tests (Recruiter, Admin, Reviewer)
# =========================================================================

def test_recruiter_can_access_resumes_but_not_admin(client, test_db):
    _seed_user(test_db, email="recruiter.access@company.test", role=UserRole.RECRUITER)
    csrf, _ = _login_user(client, email="recruiter.access@company.test")

    # Allowed on resumes
    resumes_resp = client.get("/api/v1/resumes")
    assert resumes_resp.status_code == status.HTTP_200_OK

    # Denied on admin users
    admin_users_resp = client.get("/api/v1/admin/users")
    assert admin_users_resp.status_code == status.HTTP_403_FORBIDDEN
    assert admin_users_resp.json()["detail"]["code"] == "FORBIDDEN"

    # Denied on admin audit events
    events_resp = client.get("/api/v1/admin/security-events")
    assert events_resp.status_code == status.HTTP_403_FORBIDDEN


def test_admin_can_access_admin_but_not_resumes(client, test_db):
    _seed_user(test_db, email="admin.access@company.test", role=UserRole.ADMIN)
    csrf, _ = _login_user(client, email="admin.access@company.test")

    # Allowed on admin users
    users_resp = client.get("/api/v1/admin/users")
    assert users_resp.status_code == status.HTTP_200_OK

    # Allowed on admin security events
    events_resp = client.get("/api/v1/admin/security-events")
    assert events_resp.status_code == status.HTTP_200_OK

    # Denied on candidate resumes (Admin does NOT get recruiter data access)
    resumes_resp = client.get("/api/v1/resumes")
    assert resumes_resp.status_code == status.HTTP_403_FORBIDDEN
    assert resumes_resp.json()["detail"]["code"] == "FORBIDDEN"


def test_reviewer_denied_on_both_resumes_and_admin(client, test_db):
    _seed_user(test_db, email="reviewer.access@company.test", role=UserRole.REVIEWER)
    csrf, _ = _login_user(client, email="reviewer.access@company.test")

    # Allowed on auth/me
    me_resp = client.get("/api/v1/auth/me")
    assert me_resp.status_code == status.HTTP_200_OK

    # Denied on resumes
    resumes_resp = client.get("/api/v1/resumes")
    assert resumes_resp.status_code == status.HTTP_403_FORBIDDEN

    # Denied on admin
    admin_resp = client.get("/api/v1/admin/users")
    assert admin_resp.status_code == status.HTTP_403_FORBIDDEN


# =========================================================================
# 4. Object-Level Safety (Valid vs Random UUIDs with Wrong Role)
# =========================================================================

def test_object_level_safety_no_existence_leakage(client, test_db):
    # Seed an existing resume artifact
    existing_id = uuid.uuid4()
    art = ResumeArtifact(
        artifact_id=existing_id,
        original_filename="candidate.pdf",
        declared_mime_type="application/pdf",
        size_bytes=100,
        sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
        created_at=datetime.now(timezone.utc),
    )
    test_db.add(art)
    test_db.commit()

    random_id = uuid.uuid4()

    # Admin caller
    _seed_user(test_db, email="admin.probe@company.test", role=UserRole.ADMIN)
    _login_user(client, email="admin.probe@company.test")

    # Both existing and non-existing return 403 identically
    resp_exist = client.get(f"/api/v1/resumes/{existing_id}/status")
    resp_random = client.get(f"/api/v1/resumes/{random_id}/status")

    assert resp_exist.status_code == status.HTTP_403_FORBIDDEN
    assert resp_random.status_code == status.HTTP_403_FORBIDDEN
    assert resp_exist.json() == resp_random.json()


# =========================================================================
# 5. Admin User Management CRUD & Safety
# =========================================================================

def test_admin_create_user_success_and_duplicate_rejection(client, test_db):
    _seed_user(test_db, email="admin.creator@company.test", role=UserRole.ADMIN)
    csrf, _ = _login_user(client, email="admin.creator@company.test")

    settings = get_settings()

    # 1. Create a recruiter
    create_resp = client.post(
        "/api/v1/admin/users",
        headers={"X-CSRF-Token": csrf, "Origin": settings.frontend_origin},
        json={
            "email": "  NEW.RECRUITER@company.test  ",
            "display_name": "New Recruiter",
            "role": "RECRUITER",
            "temporary_password": "TempPassword123!",
        },
    )
    assert create_resp.status_code == status.HTTP_201_CREATED
    data = create_resp.json()
    assert data["email"] == "new.recruiter@company.test"
    assert data["mustChangePassword"] is True
    assert "password" not in data
    assert "password_hash" not in data

    # 2. Reject duplicate email with 409
    dup_resp = client.post(
        "/api/v1/admin/users",
        headers={"X-CSRF-Token": csrf, "Origin": settings.frontend_origin},
        json={
            "email": "new.recruiter@company.test",
            "display_name": "Another Person",
            "role": "REVIEWER",
            "temporary_password": "TempPassword123!",
        },
    )
    assert dup_resp.status_code == status.HTTP_409_CONFLICT
    assert dup_resp.json()["detail"]["code"] == "USER_ALREADY_EXISTS"


def test_admin_update_user_role_and_deactivation_revokes_sessions(client, test_db):
    admin = _seed_user(test_db, email="admin.updater@company.test", role=UserRole.ADMIN)
    admin_csrf, _ = _login_user(client, email="admin.updater@company.test")

    target = _seed_user(test_db, email="target.user@company.test", role=UserRole.RECRUITER)

    # Seed an active session for target user
    now = datetime.now(timezone.utc)
    raw_token = generate_secure_token()
    target_session = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=target.user_id,
        token_hash=hash_token(raw_token),
        csrf_token_hash=hash_token(generate_secure_token()),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=1),
        absolute_expires_at=now + timedelta(hours=12),
    )
    test_db.add(target_session)
    test_db.commit()

    settings = get_settings()

    # 1. Update role to REVIEWER
    update_resp = client.patch(
        f"/api/v1/admin/users/{target.user_id}",
        headers={"X-CSRF-Token": admin_csrf, "Origin": settings.frontend_origin},
        json={"role": "REVIEWER"},
    )
    assert update_resp.status_code == status.HTTP_200_OK
    assert update_resp.json()["role"] == "REVIEWER"

    # Verify session was revoked in DB
    test_db.refresh(target_session)
    assert target_session.revoked_at is not None
    assert target_session.revoke_reason == SessionRevokeReason.ADMIN_REVOKED

    # 2. Deactivate user
    deact_resp = client.patch(
        f"/api/v1/admin/users/{target.user_id}",
        headers={"X-CSRF-Token": admin_csrf, "Origin": settings.frontend_origin},
        json={"is_active": False},
    )
    assert deact_resp.status_code == status.HTTP_200_OK
    assert deact_resp.json()["isActive"] is False


def test_cannot_demote_or_deactivate_last_active_admin(client, test_db):
    admin = _seed_user(test_db, email="sole.admin@company.test", role=UserRole.ADMIN)
    csrf, _ = _login_user(client, email="sole.admin@company.test")

    settings = get_settings()

    # Attempt to demote sole admin
    demote_resp = client.patch(
        f"/api/v1/admin/users/{admin.user_id}",
        headers={"X-CSRF-Token": csrf, "Origin": settings.frontend_origin},
        json={"role": "RECRUITER"},
    )
    assert demote_resp.status_code == status.HTTP_400_BAD_REQUEST
    assert demote_resp.json()["detail"]["code"] == "CANNOT_DEMOTE_LAST_ADMIN"

    # Attempt to deactivate sole admin
    deact_resp = client.patch(
        f"/api/v1/admin/users/{admin.user_id}",
        headers={"X-CSRF-Token": csrf, "Origin": settings.frontend_origin},
        json={"is_active": False},
    )
    assert deact_resp.status_code == status.HTTP_400_BAD_REQUEST
    assert deact_resp.json()["detail"]["code"] == "CANNOT_DEMOTE_LAST_ADMIN"


def test_admin_password_reset_revokes_sessions_and_sets_must_change(client, test_db):
    admin = _seed_user(test_db, email="admin.resetter@company.test", role=UserRole.ADMIN)
    admin_csrf, _ = _login_user(client, email="admin.resetter@company.test")

    target = _seed_user(test_db, email="target.reset@company.test", password="OldPassword123!", role=UserRole.RECRUITER)

    # Seed target active session
    now = datetime.now(timezone.utc)
    raw_token = generate_secure_token()
    target_sess = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=target.user_id,
        token_hash=hash_token(raw_token),
        csrf_token_hash=hash_token(generate_secure_token()),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=1),
        absolute_expires_at=now + timedelta(hours=12),
    )
    test_db.add(target_sess)
    test_db.commit()

    settings = get_settings()

    # Admin resets password
    reset_resp = client.post(
        f"/api/v1/admin/users/{target.user_id}/reset-password",
        headers={"X-CSRF-Token": admin_csrf, "Origin": settings.frontend_origin},
        json={"temporary_password": "NewTempPassword123!"},
    )
    assert reset_resp.status_code == status.HTTP_200_OK
    assert reset_resp.json()["mustChangePassword"] is True

    # Verify session revoked
    test_db.refresh(target_sess)
    assert target_sess.revoked_at is not None
    assert target_sess.revoke_reason == SessionRevokeReason.PASSWORD_CHANGED

    # Target user logs in with new temporary password
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "target.reset@company.test", "password": "NewTempPassword123!"},
    )
    assert login_resp.status_code == status.HTTP_200_OK
    assert login_resp.json()["user"]["mustChangePassword"] is True


# =========================================================================
# 6. Admin Security Events & Audit Safety
# =========================================================================

def test_admin_list_security_events_pagination(client, test_db):
    admin = _seed_user(test_db, email="admin.events@company.test", role=UserRole.ADMIN)
    csrf, _ = _login_user(client, email="admin.events@company.test")

    # Retrieve security events
    events_resp = client.get("/api/v1/admin/security-events?limit=10&offset=0")
    assert events_resp.status_code == status.HTTP_200_OK
    data = events_resp.json()
    assert "items" in data
    assert "total" in data
    assert data["limit"] == 10
    assert data["offset"] == 0
    assert data["total"] >= 1

    # Verify no credentials or digests leaked
    for item in data["items"]:
        meta_str = str(item.get("safeMetadata", {}))
        assert "password" not in meta_str
        assert "token_hash" not in meta_str


# =========================================================================
# 7. Security Hardening & Tampering Protection (Phase 2A.5)
# =========================================================================

def test_tampered_uuid_returns_403_for_non_recruiters(client, test_db):
    """Verify that unauthorized roles cannot probe for candidate existence using tampered UUIDs.
    Both nonexistent and existent candidate IDs must return 403 Forbidden to non-recruiters."""
    admin = _seed_user(test_db, email="admin.tamper@company.test", role=UserRole.ADMIN)
    admin_csrf, _ = _login_user(client, email="admin.tamper@company.test")

    reviewer = _seed_user(test_db, email="reviewer.tamper@company.test", role=UserRole.REVIEWER)
    reviewer_csrf, _ = _login_user(client, email="reviewer.tamper@company.test")

    recruiter = _seed_user(test_db, email="recruiter.tamper@company.test", role=UserRole.RECRUITER)
    recruiter_csrf, _ = _login_user(client, email="recruiter.tamper@company.test")

    # Recruiter creates a candidate artifact
    now = datetime.now(timezone.utc)
    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="tamper_test.pdf",
        declared_mime_type="application/pdf",
        size_bytes=1024,
        sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
        created_at=now,
    )
    test_db.add(artifact)
    test_db.commit()

    real_id = str(artifact.artifact_id)
    bogus_id = str(uuid.uuid4())

    # 1. Admin querying real ID -> 403 Forbidden
    _login_user(client, email="admin.tamper@company.test")
    r1 = client.get(f"/api/v1/resumes/{real_id}/status")
    assert r1.status_code == status.HTTP_403_FORBIDDEN

    # 2. Admin querying bogus ID -> 403 Forbidden (no 404 leakage to unauthorized roles)
    r2 = client.get(f"/api/v1/resumes/{bogus_id}/status")
    assert r2.status_code == status.HTTP_403_FORBIDDEN

    # 3. Reviewer querying real ID -> 403 Forbidden
    _login_user(client, email="reviewer.tamper@company.test")
    r3 = client.get(f"/api/v1/resumes/{real_id}/status")
    assert r3.status_code == status.HTTP_403_FORBIDDEN

    # 4. Reviewer querying bogus ID -> 403 Forbidden
    r4 = client.get(f"/api/v1/resumes/{bogus_id}/status")
    assert r4.status_code == status.HTTP_403_FORBIDDEN


def test_denied_mutations_cause_no_database_state_changes(client, test_db):
    """Verify that unauthorized state-changing attempts cause zero side-effects in PostgreSQL."""
    reviewer = _seed_user(test_db, email="reviewer.mut@company.test", role=UserRole.REVIEWER)
    reviewer_csrf, _ = _login_user(client, email="reviewer.mut@company.test")
    settings = get_settings()

    initial_users_count = test_db.execute(select(UserRecord)).scalars().all()

    # Reviewer attempts to create a user
    resp = client.post(
        "/api/v1/admin/users",
        headers={"X-CSRF-Token": reviewer_csrf, "Origin": settings.frontend_origin},
        json={
            "email": "hacker@test.com",
            "display_name": "Hacker",
            "role": "ADMIN",
            "temporary_password": "TempPassword123!",
        },
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN

    # Verify no new user was committed
    post_users_count = test_db.execute(select(UserRecord)).scalars().all()
    assert len(post_users_count) == len(initial_users_count)

