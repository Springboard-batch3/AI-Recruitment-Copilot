"""Comprehensive regression tests for Phase 4B.2:
Candidate Authorization Kernel, Strict Session Namespace Separation,
Deny-by-default access policies, Ownership-scoped Resource Loaders,
and Coexisting independent sessions.
"""
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
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateBindingStatus,
    CandidatePracticeReleaseRecord,
    CandidatePracticeSessionRecord,
    JobPracticeCatalogRecord,
)
from app.models.job import JobRequisition, JobVersion, JobVersionStatus, JobLifecycleStatus
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.phase3_enums import PracticeReleaseStatus
from app.services.candidate_authorization_kernel import (
    CandidatePrincipal,
    authorize_access_practice_release,
    authorize_access_practice_session,
    authorize_view_own_profile,
    load_scoped_candidate_profile,
    load_scoped_candidate_release,
    load_scoped_candidate_session,
)
from app.services.security import (
    canonicalize_email,
    generate_secure_token,
    hash_password,
    hash_token,
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


def _seed_candidate_environment(db, email: str = "candidate.a@example.com", org_id: uuid.UUID = None):
    org_id = org_id or uuid.uuid4()
    candidate_user = _seed_user(db, email=email, role=UserRole.CANDIDATE)

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
        full_name="Candidate Person",
        primary_email=email,
        primary_phone="+1234567890",
        identity_payload={"name": "Candidate Person", "email": email},
    )
    db.add(identity)
    db.commit()

    binding = CandidateAccountBindingRecord(
        binding_id=uuid.uuid4(),
        user_id=candidate_user.user_id,
        candidate_id=identity.identity_id,
        organization_id=org_id,
        invitation_id=uuid.uuid4(),
        status=CandidateBindingStatus.ACTIVE.value,
    )
    db.add(binding)
    db.commit()

    # Create dummy job & version
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=candidate_user.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    db.add(job)
    db.commit()

    job_ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        title="Software Engineer",
        version_status=JobVersionStatus.APPROVED,
        created_by_user_id=candidate_user.user_id,
    )
    db.add(job_ver)
    db.commit()

    return candidate_user, identity, binding, job, job_ver


def _create_session(db, user_id: uuid.UUID, csrf_token: str = None) -> tuple[str, str]:
    raw_session = generate_secure_token()
    raw_csrf = csrf_token or generate_secure_token()
    now = datetime.now(timezone.utc)
    session = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=user_id,
        token_hash=hash_token(raw_session),
        csrf_token_hash=hash_token(raw_csrf),
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=2),
        absolute_expires_at=now + timedelta(hours=24),
    )
    db.add(session)
    db.commit()
    return raw_session, raw_csrf


# =============================================================================
# 1. Strict Session Namespace & Cookie Isolation Tests
# =============================================================================

def test_candidate_route_accepts_valid_candidate_session(client, test_db):
    user, identity, binding, job, job_ver = _seed_candidate_environment(test_db)
    cand_token, cand_csrf = _create_session(test_db, user.user_id)

    client.cookies.set("ai_hiring_candidate_session", cand_token)
    resp = client.get("/api/v1/candidate/auth/me")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["userId"] == str(user.user_id)
    assert data["candidateId"] == str(identity.identity_id)
    assert data["role"] == "CANDIDATE"


def test_staff_cookie_alone_cannot_authenticate_candidate_route(client, test_db):
    recruiter = _seed_user(test_db, "recruiter@org.test", UserRole.RECRUITER)
    staff_token, staff_csrf = _create_session(test_db, recruiter.user_id)

    # Set staff cookie only
    client.cookies.set("ai_hiring_session", staff_token)
    resp = client.get("/api/v1/candidate/auth/me")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_candidate_cookie_alone_cannot_authenticate_staff_route(client, test_db):
    user, identity, binding, job, job_ver = _seed_candidate_environment(test_db)
    cand_token, cand_csrf = _create_session(test_db, user.user_id)

    # Set candidate cookie only
    client.cookies.set("ai_hiring_candidate_session", cand_token)
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    resp_jobs = client.get("/api/v1/jobs")
    assert resp_jobs.status_code == status.HTTP_401_UNAUTHORIZED


def test_simultaneous_cookies_deterministically_isolated(client, test_db):
    recruiter = _seed_user(test_db, "recruiter@org.test", UserRole.RECRUITER)
    staff_token, staff_csrf = _create_session(test_db, recruiter.user_id)

    cand_user, cand_id, cand_binding, job, job_ver = _seed_candidate_environment(test_db, "candidate@org.test")
    cand_token, cand_csrf = _create_session(test_db, cand_user.user_id)

    # Set both cookies concurrently in the client
    client.cookies.set("ai_hiring_session", staff_token)
    client.cookies.set("ai_hiring_candidate_session", cand_token)

    # 1. Staff route resolves strictly recruiter
    resp_staff = client.get("/api/v1/auth/me")
    assert resp_staff.status_code == status.HTTP_200_OK
    assert resp_staff.json()["userId"] == str(recruiter.user_id)
    assert resp_staff.json()["role"] == "RECRUITER"

    # 2. Candidate route resolves strictly candidate
    resp_cand = client.get("/api/v1/candidate/auth/me")
    assert resp_cand.status_code == status.HTTP_200_OK
    assert resp_cand.json()["userId"] == str(cand_user.user_id)
    assert resp_cand.json()["role"] == "CANDIDATE"


def test_candidate_logout_preserves_staff_session(client, test_db):
    recruiter = _seed_user(test_db, "recruiter@org.test", UserRole.RECRUITER)
    staff_token, staff_csrf = _create_session(test_db, recruiter.user_id)

    cand_user, cand_id, cand_binding, job, job_ver = _seed_candidate_environment(test_db, "candidate@org.test")
    cand_token, cand_csrf = _create_session(test_db, cand_user.user_id)

    client.cookies.set("ai_hiring_session", staff_token)
    client.cookies.set("ai_hiring_candidate_session", cand_token)

    # Logout as candidate
    resp_logout = client.post(
        "/api/v1/candidate/auth/logout",
        headers={"X-CSRF-Token": cand_csrf, "Origin": "http://localhost:5173"},
    )
    assert resp_logout.status_code == status.HTTP_204_NO_CONTENT

    # Candidate route now unauthorized
    resp_cand = client.get("/api/v1/candidate/auth/me")
    assert resp_cand.status_code == status.HTTP_401_UNAUTHORIZED

    # Staff session remains active
    resp_staff = client.get("/api/v1/auth/me")
    assert resp_staff.status_code == status.HTTP_200_OK
    assert resp_staff.json()["userId"] == str(recruiter.user_id)


def test_staff_logout_preserves_candidate_session(client, test_db):
    recruiter = _seed_user(test_db, "recruiter@org.test", UserRole.RECRUITER)
    staff_token, staff_csrf = _create_session(test_db, recruiter.user_id)

    cand_user, cand_id, cand_binding, job, job_ver = _seed_candidate_environment(test_db, "candidate@org.test")
    cand_token, cand_csrf = _create_session(test_db, cand_user.user_id)

    client.cookies.set("ai_hiring_session", staff_token)
    client.cookies.set("ai_hiring_candidate_session", cand_token)

    # Logout as staff
    resp_logout = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": staff_csrf, "Origin": "http://localhost:5173"},
    )
    assert resp_logout.status_code == status.HTTP_204_NO_CONTENT

    # Staff route now unauthorized
    resp_staff = client.get("/api/v1/auth/me")
    assert resp_staff.status_code == status.HTTP_401_UNAUTHORIZED

    # Candidate session remains active
    resp_cand = client.get("/api/v1/candidate/auth/me")
    assert resp_cand.status_code == status.HTTP_200_OK
    assert resp_cand.json()["userId"] == str(cand_user.user_id)


# =============================================================================
# 2. Candidate Authorization Kernel & Policy Tests
# =============================================================================

def test_cross_candidate_resource_access_denied_with_safe_404(test_db):
    # Candidate A
    cand_a_user, cand_a_id, cand_a_bind, job, job_ver = _seed_candidate_environment(test_db, "cand_a@org.test")
    session_a_token, _ = _create_session(test_db, cand_a_user.user_id)
    session_a = test_db.execute(select(UserSessionRecord).where(UserSessionRecord.user_id == cand_a_user.user_id)).scalar_one()
    principal_a = CandidatePrincipal.from_context(cand_a_user, session_a, cand_a_bind)

    # Candidate B
    cand_b_user, cand_b_id, cand_b_bind, _, _ = _seed_candidate_environment(test_db, "cand_b@org.test")

    # Release for Candidate B
    release_b = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        organization_id=cand_b_bind.organization_id,
        candidate_id=cand_b_id.identity_id,
        job_id=job.job_id,
        job_version_id=job_ver.job_version_id,
        job_revision=1,
        status=PracticeReleaseStatus.RELEASED.value,
        released_by_user_id=uuid.uuid4(),
    )
    test_db.add(release_b)
    test_db.commit()

    # Candidate A attempts to load Candidate B's release -> must return 404 (anti-enumeration)
    with pytest.raises(Exception) as exc_info:
        load_scoped_candidate_release(principal=principal_a, release_id=release_b.release_id, db=test_db)
    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


def test_revoked_release_denied_with_403(test_db):
    cand_user, cand_id, cand_bind, job, job_ver = _seed_candidate_environment(test_db, "cand@org.test")
    session_token, _ = _create_session(test_db, cand_user.user_id)
    session = test_db.execute(select(UserSessionRecord).where(UserSessionRecord.user_id == cand_user.user_id)).scalar_one()
    principal = CandidatePrincipal.from_context(cand_user, session, cand_bind)

    revoked_release = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        organization_id=cand_bind.organization_id,
        candidate_id=cand_id.identity_id,
        job_id=job.job_id,
        job_version_id=job_ver.job_version_id,
        job_revision=1,
        status=PracticeReleaseStatus.REVOKED.value,
        released_by_user_id=uuid.uuid4(),
    )
    test_db.add(revoked_release)
    test_db.commit()

    with pytest.raises(Exception) as exc_info:
        load_scoped_candidate_release(principal=principal, release_id=revoked_release.release_id, db=test_db)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail["code"] == "RELEASE_REVOKED"


def test_suspended_candidate_binding_rejected(client, test_db):
    cand_user, cand_id, cand_bind, job, job_ver = _seed_candidate_environment(test_db, "suspended@org.test")
    cand_bind.status = CandidateBindingStatus.SUSPENDED.value
    test_db.commit()

    token, csrf = _create_session(test_db, cand_user.user_id)
    client.cookies.set("ai_hiring_candidate_session", token)

    resp = client.get("/api/v1/candidate/auth/me")
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert resp.json()["detail"]["code"] == "CANDIDATE_ACCESS_SUSPENDED"


def test_candidate_cannot_access_recruiter_report_endpoint(client, test_db):
    cand_user, cand_id, cand_bind, job, job_ver = _seed_candidate_environment(test_db, "cand_rep@org.test")
    cand_token, cand_csrf = _create_session(test_db, cand_user.user_id)
    client.cookies.set("ai_hiring_candidate_session", cand_token)

    fake_session_id = uuid.uuid4()
    # Attempting to call recruiter report route with candidate session must be rejected (401 - no staff cookie)
    resp = client.get(f"/api/v1/recruiter/candidate-practice/sessions/{fake_session_id}/report")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_candidate_cannot_access_admin_endpoints(client, test_db):
    cand_user, cand_id, cand_bind, job, job_ver = _seed_candidate_environment(test_db, "cand_admin@org.test")
    cand_token, cand_csrf = _create_session(test_db, cand_user.user_id)
    client.cookies.set("ai_hiring_candidate_session", cand_token)

    resp = client.get("/api/v1/admin/users")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_candidate_can_access_own_profile_via_me_profile(client, test_db):
    cand_user, cand_id, cand_bind, job, job_ver = _seed_candidate_environment(test_db, "cand_prof@org.test")
    cand_token, cand_csrf = _create_session(test_db, cand_user.user_id)
    client.cookies.set("ai_hiring_candidate_session", cand_token)

    from app.models.extraction import ResumeExtraction, BlockType, ExtractionMethod
    from app.schemas.extraction import ExtractionBlockData
    from app.services.profile_assembly import assemble_candidate_profile, persist_candidate_profile
    from decimal import Decimal

    extraction = ResumeExtraction(
        extraction_id=uuid.uuid4(),
        artifact_id=cand_id.artifact_id,
        extractor_version="1.0.0",
        review_required=False,
    )
    test_db.add(extraction)
    test_db.commit()

    b0 = ExtractionBlockData(
        block_id=uuid.uuid4(),
        sequence_index=0,
        page_number=1,
        structural_location=None,
        block_type=BlockType.PARAGRAPH,
        raw_text="Jane Doe\njane.doe@example.com | +1 555-0199 | linkedin.com/in/janedoe | San Francisco, CA",
        normalized_text="Jane Doe\njane.doe@example.com | +1 555-0199 | linkedin.com/in/janedoe | San Francisco, CA",
        extraction_method=ExtractionMethod.PDF_TEXT,
        confidence=Decimal("1.0"),
        review_required=False,
    )
    b1 = ExtractionBlockData(
        block_id=uuid.uuid4(),
        sequence_index=1,
        page_number=1,
        structural_location=None,
        block_type=BlockType.PARAGRAPH,
        raw_text="Summary\nSenior Software Engineer with 8 years of backend distributed systems experience.",
        normalized_text="Summary\nSenior Software Engineer with 8 years of backend distributed systems experience.",
        extraction_method=ExtractionMethod.PDF_TEXT,
        confidence=Decimal("1.0"),
        review_required=False,
    )

    assembled = assemble_candidate_profile([b0, b1], str(cand_id.artifact_id))
    profile_rec = persist_candidate_profile(
        session=test_db,
        artifact_id=cand_id.artifact_id,
        extraction_id=extraction.extraction_id,
        assembled_profile=assembled,
    )
    # Link to candidate identity
    profile_rec.identity_id = cand_id.identity_id
    test_db.commit()

    resp = client.get("/api/v1/candidate/auth/me/profile")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["candidateId"] == str(cand_id.identity_id)
    assert data["identity"]["fullName"]["value"] == "Jane Doe"


def test_candidate_mutation_without_csrf_rejected(client, test_db):
    cand_user, cand_id, cand_bind, job, job_ver = _seed_candidate_environment(test_db, "cand_csrf@org.test")
    cand_token, cand_csrf = _create_session(test_db, cand_user.user_id)
    client.cookies.set("ai_hiring_candidate_session", cand_token)

    # Missing X-CSRF-Token header on POST
    resp = client.post("/api/v1/candidate/auth/logout")
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_legacy_release_with_null_application_id_authorized(test_db):
    cand_user, cand_id, cand_bind, job, job_ver = _seed_candidate_environment(test_db, "legacy_cand@org.test")
    session_token, _ = _create_session(test_db, cand_user.user_id)
    session = test_db.execute(select(UserSessionRecord).where(UserSessionRecord.user_id == cand_user.user_id)).scalar_one()
    principal = CandidatePrincipal.from_context(cand_user, session, cand_bind)

    # Legacy release created without application_id
    legacy_release = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        organization_id=cand_bind.organization_id,
        candidate_id=cand_id.identity_id,
        job_id=job.job_id,
        job_version_id=job_ver.job_version_id,
        job_revision=1,
        application_id=None,
        status=PracticeReleaseStatus.RELEASED.value,
        released_by_user_id=uuid.uuid4(),
    )
    test_db.add(legacy_release)
    test_db.commit()

    loaded = load_scoped_candidate_release(principal=principal, release_id=legacy_release.release_id, db=test_db)
    assert loaded.release_id == legacy_release.release_id
    assert loaded.application_id is None

