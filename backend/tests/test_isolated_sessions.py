import uuid
from datetime import datetime, timezone

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.database import get_db
from app.main import app
from app.models.auth import (
    Base,
    UserRecord,
    UserRole,
    UserSessionRecord,
)
from app.services.security import (
    canonicalize_email,
    hash_password,
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


def test_allowed_frontend_origins_settings():
    settings = Settings(
        database_url="postgresql+psycopg://dummy",
        frontend_origin="http://localhost:5173",
        allowed_frontend_origins="http://localhost:5173,http://admin.localhost:5173,http://recruiter.localhost:5173",
    )
    origins = settings.get_allowed_frontend_origins()
    assert "http://localhost:5173" in origins
    assert "http://admin.localhost:5173" in origins
    assert "http://recruiter.localhost:5173" in origins

    # Reject wildcard in allowed_frontend_origins
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+psycopg://dummy",
            allowed_frontend_origins="http://admin.localhost:5173,*",
        )


def test_host_only_cookie_attributes_on_login(client, test_db):
    _seed_user(test_db, "admin@company.test", UserRole.ADMIN)

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@company.test", "password": "Password123!"},
    )
    assert resp.status_code == status.HTTP_200_OK

    set_cookie_header = resp.headers.get("set-cookie", "")
    assert "ai_hiring_session=" in set_cookie_header
    assert "HttpOnly" in set_cookie_header or "httponly" in set_cookie_header.lower()
    assert "path=/" in set_cookie_header.lower()
    assert "samesite=lax" in set_cookie_header.lower()
    # Critical: Domain attribute must be omitted for host-only cookies
    assert "domain=" not in set_cookie_header.lower()


def test_multi_origin_csrf_and_origin_validation(client, test_db):
    admin_user = _seed_user(test_db, "admin.csrf@company.test", UserRole.ADMIN)
    settings = get_settings()

    # Log in
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "admin.csrf@company.test", "password": "Password123!"},
    )
    csrf_token = login_resp.json()["csrfToken"]

    # 1. Allowed origin: admin.localhost
    resp_admin = client.post(
        "/api/v1/admin/users",
        json={
            "email": "new.user@company.test",
            "display_name": "New User",
            "role": "RECRUITER",
            "temporary_password": "TempPassword123!",
        },
        headers={
            "X-CSRF-Token": csrf_token,
            "Origin": "http://admin.localhost:5173",
        },
    )
    assert resp_admin.status_code == status.HTTP_201_CREATED

    # 2. Allowed origin: recruiter.localhost
    resp_logout_recruiter_origin = client.post(
        "/api/v1/auth/logout",
        headers={
            "X-CSRF-Token": csrf_token,
            "Origin": "http://recruiter.localhost:5173",
        },
    )
    assert resp_logout_recruiter_origin.status_code == status.HTTP_204_NO_CONTENT

    # 3. Disallowed untrusted origin: evil.com
    # Log in again
    login_resp2 = client.post(
        "/api/v1/auth/login",
        json={"email": "admin.csrf@company.test", "password": "Password123!"},
    )
    csrf_token2 = login_resp2.json()["csrfToken"]

    resp_evil = client.post(
        "/api/v1/auth/logout",
        headers={
            "X-CSRF-Token": csrf_token2,
            "Origin": "http://malicious-attacker.com",
        },
    )
    assert resp_evil.status_code == status.HTTP_403_FORBIDDEN
    assert resp_evil.json()["detail"]["code"] == "CSRF_DENIED"


def test_simultaneous_isolated_sessions_no_cross_revocation(client, test_db):
    """
    Simulates two independent browser sessions (Admin and Recruiter).
    Validates that logging out or performing actions in one does not affect the other.
    """
    admin_user = _seed_user(test_db, "admin.isolated@company.test", UserRole.ADMIN)
    recruiter_user = _seed_user(test_db, "recruiter.isolated@company.test", UserRole.RECRUITER)

    # Session 1: Admin
    resp_admin_login = client.post(
        "/api/v1/auth/login",
        json={"email": "admin.isolated@company.test", "password": "Password123!"},
    )
    assert resp_admin_login.status_code == status.HTTP_200_OK
    admin_cookie = resp_admin_login.cookies.get("ai_hiring_session")
    admin_csrf = resp_admin_login.json()["csrfToken"]

    # Session 2: Recruiter
    resp_rec_login = client.post(
        "/api/v1/auth/login",
        json={"email": "recruiter.isolated@company.test", "password": "Password123!"},
    )
    assert resp_rec_login.status_code == status.HTTP_200_OK
    rec_cookie = resp_rec_login.cookies.get("ai_hiring_session")
    rec_csrf = resp_rec_login.json()["csrfToken"]

    # Both cookies are distinct tokens
    assert admin_cookie != rec_cookie

    # Admin session verifies /api/v1/auth/me
    client.cookies.set("ai_hiring_session", admin_cookie)
    me_admin = client.get("/api/v1/auth/me")
    assert me_admin.status_code == status.HTTP_200_OK
    assert me_admin.json()["email"] == "admin.isolated@company.test"
    assert me_admin.json()["role"] == "ADMIN"

    # Recruiter session verifies /api/v1/auth/me
    client.cookies.set("ai_hiring_session", rec_cookie)
    me_rec = client.get("/api/v1/auth/me")
    assert me_rec.status_code == status.HTTP_200_OK
    assert me_rec.json()["email"] == "recruiter.isolated@company.test"
    assert me_rec.json()["role"] == "RECRUITER"

    # Recruiter logs out
    rec_fresh_csrf = me_rec.json()["csrfToken"]
    resp_rec_logout = client.post(
        "/api/v1/auth/logout",
        headers={
            "X-CSRF-Token": rec_fresh_csrf,
            "Origin": "http://recruiter.localhost:5173",
        },
    )
    assert resp_rec_logout.status_code == status.HTTP_204_NO_CONTENT

    # Recruiter session is now revoked
    me_rec_after = client.get("/api/v1/auth/me")
    assert me_rec_after.status_code == status.HTTP_401_UNAUTHORIZED

    # Admin session remains completely valid and active!
    client.cookies.set("ai_hiring_session", admin_cookie)
    me_admin_after = client.get("/api/v1/auth/me")
    assert me_admin_after.status_code == status.HTTP_200_OK
    assert me_admin_after.json()["email"] == "admin.isolated@company.test"
