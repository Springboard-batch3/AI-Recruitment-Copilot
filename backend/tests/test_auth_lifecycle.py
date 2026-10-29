import io
import os
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
from app.services.security import (
    canonicalize_email,
    generate_secure_token,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
)


@pytest.fixture
def auth_db():
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
def client(auth_db):
    def override_get_db():
        try:
            yield auth_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app, base_url="https://testserver")
    app.dependency_overrides.clear()


def _seed_test_user(
    db,
    email="test.user@company.test",
    password="Password123!",
    role=UserRole.RECRUITER,
    is_active=True,
    must_change_password=False,
    failed_login_count=0,
    locked_until=None,
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
        failed_login_count=failed_login_count,
        locked_until=locked_until,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# =========================================================================
# 1. Password Scrypt Tests
# =========================================================================

def test_password_scrypt_verification_and_salts():
    password = "MyComplexPassword123!"
    hash1 = hash_password(password)
    hash2 = hash_password(password)

    # Different salts produce different hashes
    assert hash1 != hash2
    assert verify_password(password, hash1) is True
    assert verify_password(password, hash2) is True
    assert verify_password("WrongPassword123!", hash1) is False
    assert verify_password("", hash1) is False
    assert verify_password(password, "malformed_hash") is False


def test_needs_rehash_detection():
    old_hash = hash_password("Pass123!", n=8192, r=8, p=1, dklen=32)
    current_hash = hash_password("Pass123!", n=16384, r=8, p=1, dklen=32)

    assert needs_rehash(old_hash, target_n=16384, target_r=8, target_p=1, target_dklen=32) is True
    assert needs_rehash(current_hash, target_n=16384, target_r=8, target_p=1, target_dklen=32) is False
    assert needs_rehash("corrupted_hash") is True


# =========================================================================
# 2. Login Lifecycle Tests
# =========================================================================

def test_login_success_sets_httponly_cookie(client, auth_db):
    user = _seed_test_user(auth_db, email="admin.login@company.test", password="SuperPassword123!")

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "  ADMIN.LOGIN@company.test ", "password": "SuperPassword123!"},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "user" in data
    assert "csrfToken" in data
    assert data["user"]["email"] == "admin.login@company.test"
    assert data["user"]["userId"] == str(user.user_id)

    # Verify session cookie
    cookies = response.cookies
    settings = get_settings()
    assert settings.session_cookie_name in cookies
    raw_token = cookies[settings.session_cookie_name]
    assert len(raw_token) > 20

    # Verify token digests only in DB
    digest = hash_token(raw_token)
    stmt = select(UserSessionRecord).where(UserSessionRecord.user_id == user.user_id)
    session_rec = auth_db.execute(stmt).scalar_one()
    assert session_rec.token_hash == digest
    assert session_rec.token_hash != raw_token
    assert session_rec.csrf_token_hash == hash_token(data["csrfToken"])


def test_login_generic_error_for_unknown_email_wrong_password_inactive_and_cooldown(client, auth_db):
    # Seed active user
    _seed_test_user(auth_db, email="active@company.test", password="CorrectPassword123!")

    # Seed inactive user
    _seed_test_user(auth_db, email="inactive@company.test", password="CorrectPassword123!", is_active=False)

    # Seed locked user
    future = datetime.now(timezone.utc) + timedelta(minutes=15)
    _seed_test_user(auth_db, email="locked@company.test", password="CorrectPassword123!", locked_until=future)

    cases = [
        ("unknown@company.test", "AnyPassword123!"),
        ("active@company.test", "WrongPassword123!"),
        ("inactive@company.test", "CorrectPassword123!"),
        ("locked@company.test", "CorrectPassword123!"),
    ]

    for email, pwd in cases:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": pwd},
        )
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED
        detail = resp.json().get("detail", {})
        assert detail.get("code") == "INVALID_CREDENTIALS"
        assert detail.get("message") == "Unable to sign in with the provided credentials."


def test_login_failed_attempts_increment_and_cooldown(client, auth_db):
    user = _seed_test_user(auth_db, email="fail.count@company.test", password="RealPassword123!")

    settings = get_settings()
    # Perform failures up to threshold
    for i in range(settings.login_failure_threshold):
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "fail.count@company.test", "password": "WrongPassword123!"},
        )
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    auth_db.refresh(user)
    assert user.failed_login_count == settings.login_failure_threshold
    assert user.locked_until is not None

    # Even with correct password, login is denied during cooldown
    resp_locked = client.post(
        "/api/v1/auth/login",
        json={"email": "fail.count@company.test", "password": "RealPassword123!"},
    )
    assert resp_locked.status_code == status.HTTP_401_UNAUTHORIZED
    assert resp_locked.json()["detail"]["code"] == "INVALID_CREDENTIALS"


def test_login_success_resets_failed_count(client, auth_db):
    user = _seed_test_user(auth_db, email="reset.fail@company.test", password="RealPassword123!", failed_login_count=3)

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "reset.fail@company.test", "password": "RealPassword123!"},
    )
    assert resp.status_code == status.HTTP_200_OK

    auth_db.refresh(user)
    assert user.failed_login_count == 0
    assert user.locked_until is None
    assert user.last_login_at is not None


# =========================================================================
# 3. Session Validation and /auth/me Tests
# =========================================================================

def test_auth_me_success_with_valid_session(client, auth_db):
    user = _seed_test_user(auth_db, email="me.user@company.test", password="RealPassword123!", must_change_password=True)

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "me.user@company.test", "password": "RealPassword123!"},
    )
    assert login_resp.status_code == status.HTTP_200_OK

    me_resp = client.get("/api/v1/auth/me")
    assert me_resp.status_code == status.HTTP_200_OK
    data = me_resp.json()
    assert data["email"] == "me.user@company.test"
    assert data["role"] == "RECRUITER"
    assert data["mustChangePassword"] is True
    assert "csrfToken" in data
    assert "idleExpiresAt" in data
    assert "absoluteExpiresAt" in data

    # Verify no sensitive hashes are exposed
    keys = set(data.keys())
    assert {"password", "password_hash", "token_hash", "csrf_token_hash"}.isdisjoint(keys)


def test_auth_me_unauthenticated_failures(client, auth_db):
    # 1. Missing cookie
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED
    assert resp.json()["detail"]["code"] == "UNAUTHENTICATED"

    # 2. Invalid cookie
    client.cookies.set("ai_hiring_session", "completely_fake_token")
    resp2 = client.get("/api/v1/auth/me")
    assert resp2.status_code == status.HTTP_401_UNAUTHORIZED

    # 3. Revoked session
    user = _seed_test_user(auth_db, email="revoked.sess@company.test", password="Pass123!")
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "revoked.sess@company.test", "password": "Pass123!"},
    )
    assert login_resp.status_code == status.HTTP_200_OK

    stmt = select(UserSessionRecord).where(UserSessionRecord.user_id == user.user_id)
    session_rec = auth_db.execute(stmt).scalar_one()
    session_rec.revoked_at = datetime.now(timezone.utc)
    session_rec.revoke_reason = SessionRevokeReason.ADMIN_REVOKED
    auth_db.commit()

    resp3 = client.get("/api/v1/auth/me")
    assert resp3.status_code == status.HTTP_401_UNAUTHORIZED


def test_auth_me_expired_sessions(client, auth_db):
    user = _seed_test_user(auth_db, email="exp.user@company.test", password="Pass123!")
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "exp.user@company.test", "password": "Pass123!"},
    )
    assert login_resp.status_code == status.HTTP_200_OK

    stmt = select(UserSessionRecord).where(UserSessionRecord.user_id == user.user_id)
    session_rec = auth_db.execute(stmt).scalar_one()

    # Make idle expired
    session_rec.idle_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    auth_db.commit()

    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    auth_db.refresh(session_rec)
    assert session_rec.revoked_at is not None
    assert session_rec.revoke_reason == SessionRevokeReason.EXPIRED_IDLE


# =========================================================================
# 4. CSRF and Origin Protection Tests
# =========================================================================

def test_csrf_and_origin_validation(client, auth_db):
    user = _seed_test_user(auth_db, email="csrf.user@company.test", password="Pass123!")
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "csrf.user@company.test", "password": "Pass123!"},
    )
    csrf_token = login_resp.json()["csrfToken"]

    # 1. Missing CSRF header on POST /logout
    resp_no_csrf = client.post("/api/v1/auth/logout")
    assert resp_no_csrf.status_code == status.HTTP_403_FORBIDDEN
    assert resp_no_csrf.json()["detail"]["code"] == "CSRF_DENIED"

    # 2. Invalid CSRF token
    resp_bad_csrf = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "invalid_csrf_token"})
    assert resp_bad_csrf.status_code == status.HTTP_403_FORBIDDEN
    assert resp_bad_csrf.json()["detail"]["code"] == "CSRF_DENIED"

    # 3. Invalid Origin header
    resp_bad_origin = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf_token, "Origin": "https://malicious-site.test"},
    )
    assert resp_bad_origin.status_code == status.HTTP_403_FORBIDDEN
    assert resp_bad_origin.json()["detail"]["code"] == "CSRF_DENIED"

    # 4. Valid CSRF and matching Origin
    settings = get_settings()
    resp_valid = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf_token, "Origin": settings.frontend_origin},
    )
    assert resp_valid.status_code == status.HTTP_204_NO_CONTENT


# =========================================================================
# 5. Logout Lifecycle Tests
# =========================================================================

def test_logout_revokes_session_and_clears_cookie(client, auth_db):
    user = _seed_test_user(auth_db, email="logout.user@company.test", password="Pass123!")
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "logout.user@company.test", "password": "Pass123!"},
    )
    csrf_token = login_resp.json()["csrfToken"]

    # Execute logout
    logout_resp = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_token})
    assert logout_resp.status_code == status.HTTP_204_NO_CONTENT

    # Verify session row is revoked
    stmt = select(UserSessionRecord).where(UserSessionRecord.user_id == user.user_id)
    session_rec = auth_db.execute(stmt).scalar_one()
    assert session_rec.revoked_at is not None
    assert session_rec.revoke_reason == SessionRevokeReason.LOGOUT

    # Subsequent /auth/me fails even if old cookie is presented
    client.cookies.set(get_settings().session_cookie_name, login_resp.cookies[get_settings().session_cookie_name])
    resp_after = client.get("/api/v1/auth/me")
    assert resp_after.status_code == status.HTTP_401_UNAUTHORIZED


# =========================================================================
# 6. Change Password Lifecycle Tests
# =========================================================================

def test_change_password_success_and_session_rotation(client, auth_db):
    user = _seed_test_user(auth_db, email="change.pw@company.test", password="OldPassword123!", must_change_password=True)

    # Login first session
    login1 = client.post(
        "/api/v1/auth/login",
        json={"email": "change.pw@company.test", "password": "OldPassword123!"},
    )
    csrf1 = login1.json()["csrfToken"]
    old_cookie1 = login1.cookies[get_settings().session_cookie_name]

    # Create a secondary session simulating another browser
    now = datetime.now(timezone.utc)
    raw_token2 = generate_secure_token()
    session2 = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=user.user_id,
        token_hash=hash_token(raw_token2),
        csrf_token_hash=hash_token(generate_secure_token()),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=1),
        absolute_expires_at=now + timedelta(hours=12),
    )
    auth_db.add(session2)
    auth_db.commit()

    # Change password
    change_resp = client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": csrf1},
        json={
            "current_password": "OldPassword123!",
            "new_password": "BrandNewPassword123!",
            "new_password_confirmation": "BrandNewPassword123!",
        },
    )
    assert change_resp.status_code == status.HTTP_200_OK
    data = change_resp.json()
    assert data["user"]["mustChangePassword"] is False

    # 1. Verify user's must_change_password is False and password updated in DB
    auth_db.refresh(user)
    assert user.must_change_password is False
    assert verify_password("BrandNewPassword123!", user.password_hash) is True
    assert verify_password("OldPassword123!", user.password_hash) is False

    # 2. Verify second session was revoked with PASSWORD_CHANGED
    auth_db.refresh(session2)
    assert session2.revoked_at is not None
    assert session2.revoke_reason == SessionRevokeReason.PASSWORD_CHANGED

    # 3. Verify old session token from first session no longer works
    client.cookies.set(get_settings().session_cookie_name, old_cookie1)
    resp_old = client.get("/api/v1/auth/me")
    assert resp_old.status_code == status.HTTP_401_UNAUTHORIZED

    # 4. Verify new session cookie works
    client.cookies.set(get_settings().session_cookie_name, change_resp.cookies[get_settings().session_cookie_name])
    resp_new = client.get("/api/v1/auth/me")
    assert resp_new.status_code == status.HTTP_200_OK
    assert resp_new.json()["email"] == "change.pw@company.test"


def test_change_password_validation_and_failures(client, auth_db):
    user = _seed_test_user(auth_db, email="change.fail@company.test", password="OldPassword123!")
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "change.fail@company.test", "password": "OldPassword123!"},
    )
    csrf = login.json()["csrfToken"]

    # 1. Wrong current password
    resp1 = client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": "WrongPassword123!",
            "new_password": "NewSecretPass123!",
            "new_password_confirmation": "NewSecretPass123!",
        },
    )
    assert resp1.status_code == status.HTTP_400_BAD_REQUEST
    assert resp1.json()["detail"]["code"] == "INVALID_CURRENT_PASSWORD"

    # 2. Password mismatch confirmation
    resp2 = client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": "OldPassword123!",
            "new_password": "NewSecretPass123!",
            "new_password_confirmation": "DifferentPass123!",
        },
    )
    assert resp2.status_code == 422

    # 3. Password reuse rejection
    resp3 = client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": "OldPassword123!",
            "new_password": "OldPassword123!",
            "new_password_confirmation": "OldPassword123!",
        },
    )
    assert resp3.status_code == status.HTTP_400_BAD_REQUEST
    assert resp3.json()["detail"]["code"] == "PASSWORD_REUSE_FORBIDDEN"


# =========================================================================
# 7. Audit Events & Zero Leakage Tests
# =========================================================================

def test_audit_events_created_for_auth_lifecycle(client, auth_db):
    user = _seed_test_user(auth_db, email="audit.user@company.test", password="RealPassword123!")

    # 1. Successful login audit event
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "audit.user@company.test", "password": "RealPassword123!"},
    )
    csrf = login_resp.json()["csrfToken"]

    stmt_login = select(SecurityAuditEventRecord).where(
        SecurityAuditEventRecord.actor_user_id == user.user_id,
        SecurityAuditEventRecord.event_type == AuditEventType.LOGIN_ATTEMPT,
        SecurityAuditEventRecord.result == AuditResult.SUCCESS,
    )
    assert auth_db.execute(stmt_login).scalar_one_or_none() is not None

    # 2. Logout audit event
    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    stmt_logout = select(SecurityAuditEventRecord).where(
        SecurityAuditEventRecord.actor_user_id == user.user_id,
        SecurityAuditEventRecord.event_type == AuditEventType.LOGOUT,
        SecurityAuditEventRecord.result == AuditResult.SUCCESS,
    )
    assert auth_db.execute(stmt_logout).scalar_one_or_none() is not None

    # 3. Verify audit events contain NO passwords or tokens in safe_metadata
    all_events = auth_db.execute(select(SecurityAuditEventRecord)).scalars().all()
    for ev in all_events:
        meta_str = str(ev.safe_metadata)
        assert "RealPassword123!" not in meta_str
        assert csrf not in meta_str


def test_session_token_collision_handling(auth_db, monkeypatch):
    import app.api.auth as auth_mod

    # First attempt generates duplicate, second generates unique
    original_generate = auth_mod.generate_secure_token
    token_cycle = ["fixed_collision_token", "fixed_collision_token", "unique_token_1", "unique_token_2"]
    
    def fake_token():
        if token_cycle:
            return token_cycle.pop(0)
        return original_generate()

    # Pre-insert existing session with fixed_collision_token
    user = _seed_test_user(auth_db, email="collision@company.test", password="Pass123!")
    now = datetime.now(timezone.utc)
    sess_existing = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=user.user_id,
        token_hash=hash_token("fixed_collision_token"),
        csrf_token_hash=hash_token("existing_csrf"),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=1),
        absolute_expires_at=now + timedelta(hours=12),
    )
    auth_db.add(sess_existing)
    auth_db.commit()

    monkeypatch.setattr(auth_mod, "generate_secure_token", fake_token)

    sess, raw_sess, raw_csrf = auth_mod._create_db_session_with_collision_handling(
        db=auth_db,
        user_id=user.user_id,
        settings=get_settings(),
        now=now,
    )
    assert sess is not None
    assert raw_sess == "unique_token_1"

