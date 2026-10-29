import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.database import get_db
from app.main import app
from app.models.auth import Base, UserRecord, UserRole, UserSessionRecord
from app.models.dashboard import DashboardTicketRecord
from app.services.dashboard_session_service import _sign_token_payload, _verify_token_payload
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
    yield TestClient(app)
    app.dependency_overrides.clear()


def create_user_and_login(
    client,
    db,
    role: UserRole = UserRole.RECRUITER,
    email: str = "recruiter@example.com",
    password: str = "Password123!",
):
    encoded_hash = hash_password(password)
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        display_name="Test User",
        password_hash=encoded_hash,
        role=role,
        is_active=True,
        must_change_password=False,
        failed_login_count=0,
    )
    db.add(user)
    db.commit()

    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == status.HTTP_200_OK
    csrf_token = resp.json()["csrfToken"]
    return user, csrf_token


# --- Session Security Tests ---

def test_recruiter_can_create_ticket(client, test_db):
    user, csrf_token = create_user_and_login(client, test_db, role=UserRole.RECRUITER, email="recruiter@example.com")

    response = client.post(
        "/api/v1/dashboard/session",
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:5173"},
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert "ticket" in data
    assert "launch_url" in data
    assert data["ticket"] in data["launch_url"]
    assert data["expires_in_seconds"] == 120

    # Ensure raw ticket is NOT in database, only SHA-256 hash
    ticket_digest = hash_token(data["ticket"])
    rec = test_db.query(DashboardTicketRecord).filter_by(ticket_hash=ticket_digest).first()
    assert rec is not None
    assert rec.user_id == user.user_id
    assert rec.role == "RECRUITER"
    assert rec.consumed_at is None


def test_admin_can_create_ticket(client, test_db):
    user, csrf_token = create_user_and_login(client, test_db, role=UserRole.ADMIN, email="admin@example.com")

    response = client.post(
        "/api/v1/dashboard/session",
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:5173"},
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert "ticket" in data


def test_candidate_cannot_create_ticket(client, test_db):
    user, csrf_token = create_user_and_login(client, test_db, role=UserRole.CANDIDATE, email="candidate@example.com")

    response = client.post(
        "/api/v1/dashboard/session",
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:5173"},
    )
    # Rejects Candidate with 403 Forbidden
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_unauthenticated_request_fails(client):
    response = client.post(
        "/api/v1/dashboard/session",
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_valid_ticket_exchanges_once_and_second_exchange_fails(client, test_db):
    user, csrf_token = create_user_and_login(client, test_db, role=UserRole.RECRUITER, email="recruiter2@example.com")

    res = client.post(
        "/api/v1/dashboard/session",
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:5173"},
    )
    assert res.status_code == status.HTTP_201_CREATED
    ticket = res.json()["ticket"]

    # First exchange succeeds
    exchange_res = client.post("/api/v1/dashboard/session/exchange", json={"ticket": ticket})
    assert exchange_res.status_code == status.HTTP_200_OK
    ex_data = exchange_res.json()
    assert "access_token" in ex_data
    assert ex_data["token_type"] == "bearer"
    assert ex_data["role"] == "RECRUITER"

    # Verify ticket is marked consumed atomically
    ticket_digest = hash_token(ticket)
    rec = test_db.query(DashboardTicketRecord).filter_by(ticket_hash=ticket_digest).first()
    assert rec.consumed_at is not None

    # Second exchange MUST fail
    second_res = client.post("/api/v1/dashboard/session/exchange", json={"ticket": ticket})
    assert second_res.status_code == status.HTTP_401_UNAUTHORIZED
    assert second_res.json()["detail"]["code"] == "TICKET_CONSUMED"


def test_expired_ticket_fails_exchange(client, test_db):
    user, _ = create_user_and_login(client, test_db, role=UserRole.RECRUITER, email="recruiter3@example.com")
    now = datetime.now(timezone.utc)
    raw_ticket = generate_secure_token()
    ticket_digest = hash_token(raw_ticket)

    # Insert already expired ticket
    expired_ticket = DashboardTicketRecord(
        id=uuid.uuid4(),
        ticket_hash=ticket_digest,
        user_id=user.user_id,
        organization_id=uuid.uuid4(),
        role="RECRUITER",
        expires_at=now - timedelta(seconds=10),
        consumed_at=None,
        created_at=now - timedelta(seconds=120),
    )
    test_db.add(expired_ticket)
    test_db.commit()

    res = client.post("/api/v1/dashboard/session/exchange", json={"ticket": raw_ticket})
    assert res.status_code == status.HTTP_401_UNAUTHORIZED
    assert res.json()["detail"]["code"] == "TICKET_EXPIRED"


def test_dashboard_token_rejects_wrong_purpose_or_expired(client, test_db):
    settings = get_settings()
    now_ts = int(datetime.now(timezone.utc).timestamp())
    user, _ = create_user_and_login(client, test_db, role=UserRole.RECRUITER, email="recruiter4@example.com")

    # Wrong purpose token
    bad_payload = {
        "sub": str(user.user_id),
        "org_id": str(uuid.uuid4()),
        "role": "RECRUITER",
        "purpose": "WRONG_PURPOSE",
        "exp": now_ts + 1800,
    }
    bad_token = _sign_token_payload(bad_payload, settings.dashboard_token_secret)

    res = client.get("/api/v1/dashboard/overview", headers={"Authorization": f"Bearer {bad_token}"})
    assert res.status_code == status.HTTP_401_UNAUTHORIZED

    # Expired token
    expired_payload = {
        "sub": str(user.user_id),
        "org_id": str(uuid.uuid4()),
        "role": "RECRUITER",
        "purpose": "RECRUITER_STREAMLIT_ANALYTICS",
        "exp": now_ts - 60,
    }
    expired_token = _sign_token_payload(expired_payload, settings.dashboard_token_secret)

    res2 = client.get("/api/v1/dashboard/overview", headers={"Authorization": f"Bearer {expired_token}"})
    assert res2.status_code == status.HTTP_401_UNAUTHORIZED
    assert res2.json()["detail"]["code"] == "TOKEN_EXPIRED"
