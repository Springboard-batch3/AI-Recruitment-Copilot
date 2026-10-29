import uuid
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import _record_audit_event
from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models.base import Base
from app.models.auth import (
    AuditEventType,
    AuditReasonCode,
    AuditResult,
    SecurityAuditEventRecord,
    UserRecord,
    UserRole,
    UserSessionRecord,
)
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.job import (
    JobLifecycleStatus,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
)
from app.models.resume import ResumeArtifact
from app.schemas.phase3_enums import (
    PrivacyReviewStatus,
    ProviderCompatibilityStatus,
    ProviderEgressClass,
    ProviderOperation,
    ProviderType,
)
from app.schemas.provider_admin import (
    ActivateProviderProfileRequest,
    CreateProviderProfileRequest,
    DisableProviderProfileRequest,
    UpdateProviderProfileRequest,
)
from app.services.provider_registry import get_provider_registry
from app.services.security import hash_password, hash_token


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
def auth_tokens(auth_db):
    """Creates test Admin and Recruiter users with active sessions."""
    now = datetime.now(timezone.utc)

    admin_user = UserRecord(
        user_id=uuid.uuid4(),
        email=f"admin_{uuid.uuid4().hex[:6]}@example.com",
        display_name="Test Admin",
        password_hash=hash_password("SecurePassword123!"),
        role=UserRole.ADMIN,
        is_active=True,
        must_change_password=False,
    )
    auth_db.add(admin_user)

    admin_raw_token = f"admin_token_{uuid.uuid4().hex}"
    admin_csrf = f"csrf_{uuid.uuid4().hex}"
    admin_session = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=admin_user.user_id,
        token_hash=hash_token(admin_raw_token),
        csrf_token_hash=hash_token(admin_csrf),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        absolute_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    auth_db.add(admin_session)

    recruiter_user = UserRecord(
        user_id=uuid.uuid4(),
        email=f"recruiter_{uuid.uuid4().hex[:6]}@example.com",
        display_name="Test Recruiter",
        password_hash=hash_password("SecurePassword123!"),
        role=UserRole.RECRUITER,
        is_active=True,
        must_change_password=False,
    )
    auth_db.add(recruiter_user)

    recruiter_raw_token = f"recruiter_token_{uuid.uuid4().hex}"
    recruiter_csrf = f"csrf_{uuid.uuid4().hex}"
    recruiter_session = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=recruiter_user.user_id,
        token_hash=hash_token(recruiter_raw_token),
        csrf_token_hash=hash_token(recruiter_csrf),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        absolute_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    auth_db.add(recruiter_session)

    auth_db.commit()

    return {
        "admin_user": admin_user,
        "admin_token": admin_raw_token,
        "admin_csrf": admin_csrf,
        "recruiter_user": recruiter_user,
        "recruiter_token": recruiter_raw_token,
        "recruiter_csrf": recruiter_csrf,
    }


@pytest.fixture
def client(auth_db):
    def override_get_db():
        try:
            yield auth_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, base_url="http://localhost:8000") as c:
        yield c
    app.dependency_overrides.clear()


def test_unauthenticated_provider_command_denied(client):
    """Unauthenticated requests to admin provider endpoints must return 401."""
    resp = client.get("/api/v1/admin/provider-profiles")
    assert resp.status_code == 401


def test_recruiter_provider_configuration_denied(client, auth_tokens):
    """Recruiters cannot configure or access Admin provider profiles (returns 403)."""
    client.cookies.set("ai_hiring_session", auth_tokens["recruiter_token"])
    headers = {
        "X-CSRF-Token": auth_tokens["recruiter_csrf"],
        "Origin": "http://localhost:5173",
    }
    resp = client.get("/api/v1/admin/provider-profiles", headers=headers)
    assert resp.status_code == 403


def test_admin_can_list_and_create_provider_profile(client, auth_tokens, auth_db):
    """Admin can list and create provider profiles with safe sanitization and audit logging."""
    client.cookies.set("ai_hiring_session", auth_tokens["admin_token"])
    headers = {
        "X-CSRF-Token": auth_tokens["admin_csrf"],
        "Origin": "http://localhost:5173",
    }

    # List initial
    resp = client.get("/api/v1/admin/provider-profiles", headers=headers)
    assert resp.status_code == 200
    profiles = resp.json()
    assert isinstance(profiles, list)

    # Create new profile
    create_payload = {
        "display_name": "Staging Local Qwen",
        "provider_type": "LOCAL_OPENAI_COMPATIBLE",
        "base_url": "http://127.0.0.1:8080/v1",
        "model_identifier": "qwen2.5-7b-instruct",
        "connect_timeout_seconds": 4.0,
        "request_timeout_seconds": 25.0,
        "max_input_tokens": 4096,
        "max_output_tokens": 512,
        "egress_class": "LOCAL_ONLY",
    }
    create_resp = client.post("/api/v1/admin/provider-profiles", json=create_payload, headers=headers)
    assert create_resp.status_code == 201
    created = create_resp.json()
    assert created["display_name"] == "Staging Local Qwen"
    assert created["enabled"] is False
    assert created["compatibility_status"] == "NOT_TESTED"
    assert created["version"] == 1
    assert "secret_status" in created

    # Verify audit event was created
    audit = auth_db.query(SecurityAuditEventRecord).filter(
        SecurityAuditEventRecord.event_type == AuditEventType.PROVIDER_PROFILE_CREATED
    ).first()
    assert audit is not None
    assert audit.result == AuditResult.SUCCESS


def test_mass_assignment_fields_rejected(client, auth_tokens):
    """Sending prohibited/mass-assignment fields to create must be rejected with 422."""
    client.cookies.set("ai_hiring_session", auth_tokens["admin_token"])
    headers = {
        "X-CSRF-Token": auth_tokens["admin_csrf"],
        "Origin": "http://localhost:5173",
    }
    payload = {
        "display_name": "Hacked Profile",
        "provider_type": "LOCAL_OPENAI_COMPATIBLE",
        "base_url": "http://127.0.0.1:8080/v1",
        "egress_class": "LOCAL_ONLY",
        "enabled": True,  # Prohibited mass-assignment
        "resolved_secret": "sk-leak",  # Prohibited
    }
    resp = client.post("/api/v1/admin/provider-profiles", json=payload, headers=headers)
    assert resp.status_code == 422


def test_optimistic_versioning_and_stale_update_rejected(client, auth_tokens):
    """Updating with a stale version must return 409 Conflict."""
    client.cookies.set("ai_hiring_session", auth_tokens["admin_token"])
    headers = {
        "X-CSRF-Token": auth_tokens["admin_csrf"],
        "Origin": "http://localhost:5173",
    }

    create_payload = {
        "display_name": "Test Optimistic",
        "provider_type": "LOCAL_OPENAI_COMPATIBLE",
        "base_url": "http://127.0.0.1:8080/v1",
        "egress_class": "LOCAL_ONLY",
    }
    create_resp = client.post("/api/v1/admin/provider-profiles", json=create_payload, headers=headers)
    assert create_resp.status_code == 201
    profile_id = create_resp.json()["profile_id"]

    # Valid update with version=1
    update_resp = client.put(
        f"/api/v1/admin/provider-profiles/{profile_id}",
        json={"version": 1, "display_name": "Test Optimistic V2"},
        headers=headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["version"] == 2

    # Stale update with version=1 again must fail with 409
    stale_resp = client.put(
        f"/api/v1/admin/provider-profiles/{profile_id}",
        json={"version": 1, "display_name": "Stale Update"},
        headers=headers,
    )
    assert stale_resp.status_code == 409


def test_remote_activation_requires_explicit_confirmation(client, auth_tokens, monkeypatch):
    """Activating an approved remote provider requires confirm_remote_egress=True."""
    client.cookies.set("ai_hiring_session", auth_tokens["admin_token"])
    headers = {
        "X-CSRF-Token": auth_tokens["admin_csrf"],
        "Origin": "http://localhost:5173",
    }

    monkeypatch.setenv("LLM_OPENAI_TEST_KEY", "test-secret-key-12345")

    create_payload = {
        "display_name": "Remote OpenAI Integration",
        "provider_type": "REMOTE_OPENAI_COMPATIBLE",
        "base_url": "https://api.openai.com/v1",
        "model_identifier": "gpt-4o-mini",
        "secret_ref": "env:LLM_OPENAI_TEST_KEY",
        "egress_class": "APPROVED_REMOTE",
    }
    create_resp = client.post("/api/v1/admin/provider-profiles", json=create_payload, headers=headers)
    assert create_resp.status_code == 201
    profile_id = create_resp.json()["profile_id"]

    # Update profile in registry to HEALTHY for activation
    from app.services.provider_registry import get_provider_registry
    registry = get_provider_registry()
    p = registry.get_profile(uuid.UUID(profile_id))
    if p:
        registry._profiles[p.profile_id] = p.model_copy(update={"compatibility_status": ProviderCompatibilityStatus.HEALTHY})

    # Activation without confirmation fails
    act_resp = client.post(
        f"/api/v1/admin/provider-profiles/{profile_id}/activate",
        json={"version": 1, "confirm_remote_egress": False},
        headers=headers,
    )
    assert act_resp.status_code == 400
    assert "Explicit confirmation" in act_resp.json()["detail"]

    # Activation with confirmation succeeds
    act_resp_ok = client.post(
        f"/api/v1/admin/provider-profiles/{profile_id}/activate",
        json={"version": 1, "confirm_remote_egress": True, "reason": "Production rollout"},
        headers=headers,
    )
    assert act_resp_ok.status_code == 200
    assert act_resp_ok.json()["enabled"] is True
    assert act_resp_ok.json()["compatibility_status"] == "HEALTHY"


def test_recruiter_ai_capability_safe_read_only(client, auth_tokens):
    """Authenticated recruiters receive limited AI capability info without secret/URL exposure."""
    client.cookies.set("ai_hiring_session", auth_tokens["recruiter_token"])
    headers = {"Origin": "http://localhost:5173"}

    resp = client.get("/api/v1/jobs/ai-capability", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "ai_enabled" in data
    assert "provider_class" in data
    assert "availability_label" in data
    assert "egress_class" in data
    # Ensure sensitive fields are not in response
    assert "base_url" not in data
    assert "secret_ref" not in data
    assert "secret" not in data


def test_admin_cannot_access_recruiter_interview_context(client, auth_tokens, auth_db):
    """Admin role alone must not grant candidate interview context access (returns 403)."""
    now = datetime.now(timezone.utc)

    # Create job owned by recruiter
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=auth_tokens["recruiter_user"].user_id,
        status=JobLifecycleStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    auth_db.add(job)
    job_ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        created_by_user_id=auth_tokens["recruiter_user"].user_id,
        version_number=1,
        title="Senior Platform Engineer",
        version_status=JobVersionStatus.APPROVED,
        created_at=now,
        updated_at=now,
    )
    auth_db.add(job_ver)

    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="alice_resume.pdf",
        sha256_hash="a" * 64,
        size_bytes=1024,
        declared_mime_type="application/pdf",
        created_at=now,
    )
    auth_db.add(artifact)

    cand_identity = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        full_name="Alice Smith",
        primary_email="alice@example.com",
        identity_payload={"fullName": {"value": "Alice Smith"}},
    )
    auth_db.add(cand_identity)
    auth_db.flush()

    cand_profile = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        identity_id=cand_identity.identity_id,
        extraction_id=uuid.uuid4(),
        profile_version=1,
        schema_version="1.0",
        parser_pipeline_version="1.0.0",
        provenance="PARSER_EXTRACTED",
        professional_profile={},
        created_at=now,
    )
    auth_db.add(cand_profile)
    auth_db.commit()

    # Admin attempt
    client.cookies.set("ai_hiring_session", auth_tokens["admin_token"])
    headers = {"Origin": "http://localhost:5173"}
    admin_resp = client.get(
        f"/api/v1/jobs/{job.job_id}/candidates/{artifact.artifact_id}/interview-context",
        headers=headers,
    )
    assert admin_resp.status_code == 403

    # Authorized Recruiter attempt
    client.cookies.set("ai_hiring_session", auth_tokens["recruiter_token"])
    recruiter_resp = client.get(
        f"/api/v1/jobs/{job.job_id}/candidates/{artifact.artifact_id}/interview-context",
        headers=headers,
    )
    assert recruiter_resp.status_code == 200
    recruiter_data = recruiter_resp.json()
    assert recruiter_data["candidate_name"] == "Alice Smith"
    assert recruiter_data["job_title"] == "Senior Platform Engineer"
    assert recruiter_data["interview_plan_state"] == "EMPTY_PLAN"
