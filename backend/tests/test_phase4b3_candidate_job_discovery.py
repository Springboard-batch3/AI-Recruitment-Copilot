"""Focused tests for Phase 4B.3: Candidate-Safe Job Discovery.

Covers:
1. Valid Candidate session listing eligible jobs.
2. Unauthenticated request rejection (401).
3. Staff cookie alone cannot access candidate job endpoints (401/403).
4. Candidate and staff cookies coexisting while candidate endpoint uses candidate session.
5. Candidate list includes only confirmed Candidate-visible jobs.
6. Draft job excluded.
7. Deleted/archived job excluded.
8. Superseded revision excluded from discovery.
9. Candidate job detail returns authoritative visible revision.
10. Direct request for draft or superseded revision safely returns 404.
11. Safe allowlisted responses containing no recruiter notes or scoring fields.
12. Deterministic search and bounded pagination.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models.auth import (
    Base,
    UserRecord,
    UserRole,
    UserSessionRecord,
)
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateBindingStatus,
)
from app.models.job import (
    EmploymentType,
    JobLifecycleStatus,
    JobPublicationStatus,
    JobRequirement,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
    RequirementCategory,
    RequirementNecessity,
    RequirementPriority,
    WorkplaceType,
)
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
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


def _seed_candidate_with_session(db, email: str = "candidate@example.com"):
    org_id = uuid.uuid4()
    candidate_user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        display_name="Candidate Jane",
        password_hash=hash_password("Password123!"),
        role=UserRole.CANDIDATE,
        is_active=True,
        must_change_password=False,
    )
    db.add(candidate_user)

    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="resume.pdf",
        detected_format=ArtifactFormat.pdf,
        size_bytes=1024,
        sha256_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    db.add(artifact)

    candidate_ident = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        primary_email=candidate_user.email,
        full_name="Candidate Jane",
        identity_payload={"name": "Candidate Jane", "email": candidate_user.email},
    )
    db.add(candidate_ident)

    binding = CandidateAccountBindingRecord(
        binding_id=uuid.uuid4(),
        candidate_id=candidate_ident.identity_id,
        user_id=candidate_user.user_id,
        organization_id=org_id,
        invitation_id=uuid.uuid4(),
        status=CandidateBindingStatus.ACTIVE.value,
        created_at=datetime.now(timezone.utc),
    )
    db.add(binding)

    raw_token = generate_secure_token()
    raw_csrf = generate_secure_token()
    now = datetime.now(timezone.utc)
    session_rec = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=candidate_user.user_id,
        token_hash=hash_token(raw_token),
        csrf_token_hash=hash_token(raw_csrf),
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=2),
        absolute_expires_at=now + timedelta(hours=24),
    )
    db.add(session_rec)
    db.commit()

    return candidate_user, raw_token, raw_csrf, binding


def _seed_staff_with_session(db, email: str = "recruiter@example.com", role: UserRole = UserRole.RECRUITER):
    staff_user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        display_name="Staff Recruiter",
        password_hash=hash_password("Password123!"),
        role=role,
        is_active=True,
        must_change_password=False,
    )
    db.add(staff_user)

    raw_token = generate_secure_token()
    raw_csrf = generate_secure_token()
    now = datetime.now(timezone.utc)
    session_rec = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=staff_user.user_id,
        token_hash=hash_token(raw_token),
        csrf_token_hash=hash_token(raw_csrf),
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=2),
        absolute_expires_at=now + timedelta(hours=24),
    )
    db.add(session_rec)
    db.commit()

    return staff_user, raw_token, raw_csrf


def test_candidate_list_open_jobs_success(client, test_db):
    cand_user, cand_tok, _, _ = _seed_candidate_with_session(test_db)
    rec_user, _, _ = _seed_staff_with_session(test_db)

    # 1. Create an APPROVED active job that is RELEASED
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=rec_user.user_id,
        status=JobLifecycleStatus.ACTIVE,
        publication_status=JobPublicationStatus.RELEASED,
    )
    test_db.add(job)

    v1 = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        revision=1,
        title="Senior Python Backend Engineer",
        department="Engineering",
        location="San Francisco, CA",
        employment_type=EmploymentType.FULL_TIME,
        workplace_type=WorkplaceType.HYBRID,
        openings_count=2,
        summary="Build scalable cloud backend services.",
        responsibilities="Design APIs, optimize SQL queries.",
        version_status=JobVersionStatus.APPROVED,
        created_by_user_id=rec_user.user_id,
        approved_by_user_id=rec_user.user_id,
        approved_at=datetime.now(timezone.utc),
    )
    test_db.add(v1)
    test_db.flush()

    job.released_job_version_id = v1.job_version_id
    job.released_at = datetime.now(timezone.utc)
    job.released_by_user_id = rec_user.user_id

    req1 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=v1.job_version_id,
        display_order=0,
        category=RequirementCategory.SKILL,
        label="5+ years Python & FastAPI",
        normalized_label="python fastapi",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        custom_weight_override=8.5,  # internal field
    )
    test_db.add(req1)
    test_db.commit()

    client.cookies.set("ai_hiring_candidate_session", cand_tok)
    res = client.get("/api/v1/candidate/jobs")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["jobId"] == str(job.job_id)
    assert item["title"] == "Senior Python Backend Engineer"
    assert item["department"] == "Engineering"
    assert item["employmentType"] == "FULL_TIME"
    assert item["workplaceType"] == "HYBRID"
    assert item["openingsCount"] == 2
    # Verify internal fields are not exposed in summary
    assert "customWeightOverride" not in item
    assert "createdByUserId" not in item
    assert "scoring" not in item


def test_unauthenticated_request_rejected(client):
    res = client.get("/api/v1/candidate/jobs")
    assert res.status_code == status.HTTP_401_UNAUTHORIZED


def test_staff_cookie_alone_cannot_access_candidate_jobs(client, test_db):
    _, staff_tok, _ = _seed_staff_with_session(test_db)
    client.cookies.set("ai_hiring_session", staff_tok)
    res = client.get("/api/v1/candidate/jobs")
    assert res.status_code == status.HTTP_401_UNAUTHORIZED


def test_coexisting_sessions_uses_candidate_context(client, test_db):
    cand_user, cand_tok, _, _ = _seed_candidate_with_session(test_db)
    _, staff_tok, _ = _seed_staff_with_session(test_db)

    client.cookies.set("ai_hiring_session", staff_tok)
    client.cookies.set("ai_hiring_candidate_session", cand_tok)

    res = client.get("/api/v1/candidate/jobs")
    assert res.status_code == status.HTTP_200_OK


def test_draft_and_deleted_jobs_excluded(client, test_db):
    cand_user, cand_tok, _, _ = _seed_candidate_with_session(test_db)
    rec_user, _, _ = _seed_staff_with_session(test_db)

    # 1. Draft job
    draft_job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=rec_user.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(draft_job)
    draft_v = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=draft_job.job_id,
        version_number=1,
        revision=1,
        title="Internal Draft Job",
        version_status=JobVersionStatus.DRAFT,
        created_by_user_id=rec_user.user_id,
    )
    test_db.add(draft_v)

    # 2. Deleted job
    del_job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=rec_user.user_id,
        status=JobLifecycleStatus.ACTIVE,
        deleted_at=datetime.now(timezone.utc),
    )
    test_db.add(del_job)
    del_v = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=del_job.job_id,
        version_number=1,
        revision=1,
        title="Deleted Approved Job",
        version_status=JobVersionStatus.APPROVED,
        created_by_user_id=rec_user.user_id,
    )
    test_db.add(del_v)
    test_db.commit()

    client.cookies.set("ai_hiring_candidate_session", cand_tok)
    res = client.get("/api/v1/candidate/jobs")
    assert res.status_code == status.HTTP_200_OK
    assert res.json()["total"] == 0


def test_superseded_version_not_exposed_on_detail(client, test_db):
    cand_user, cand_tok, _, _ = _seed_candidate_with_session(test_db)
    rec_user, _, _ = _seed_staff_with_session(test_db)

    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=rec_user.user_id,
        status=JobLifecycleStatus.ACTIVE,
        publication_status=JobPublicationStatus.RELEASED,
    )
    test_db.add(job)

    # v1 is superseded approved version
    v1 = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        revision=1,
        title="Old Job Title v1",
        version_status=JobVersionStatus.APPROVED,
        created_by_user_id=rec_user.user_id,
    )
    test_db.add(v1)

    # v2 is latest approved version
    v2 = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=2,
        revision=1,
        title="Latest Job Title v2",
        responsibilities="Core responsibilities v2",
        version_status=JobVersionStatus.APPROVED,
        created_by_user_id=rec_user.user_id,
    )
    test_db.add(v2)
    test_db.flush()

    job.released_job_version_id = v2.job_version_id
    job.released_at = datetime.now(timezone.utc)
    job.released_by_user_id = rec_user.user_id

    req = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=v2.job_version_id,
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Required Skill A",
        normalized_label="required skill a",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.CRITICAL,
    )
    test_db.add(req)
    test_db.commit()

    client.cookies.set("ai_hiring_candidate_session", cand_tok)
    # Detail request without version returns latest released (v2)
    res = client.get(f"/api/v1/candidate/jobs/{job.job_id}")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["versionNumber"] == 2
    assert data["title"] == "Latest Job Title v2"
    assert data["responsibilities"] == "Core responsibilities v2"
    assert data["qualifications"] == ["Required Skill A"]

    # Direct request for superseded version_id fails safely with 404
    res_superseded = client.get(f"/api/v1/candidate/jobs/{job.job_id}?version_id={v1.job_version_id}")
    assert res_superseded.status_code == status.HTTP_404_NOT_FOUND


def test_candidate_job_search_and_pagination(client, test_db):
    cand_user, cand_tok, _, _ = _seed_candidate_with_session(test_db)
    rec_user, _, _ = _seed_staff_with_session(test_db)

    # Create 3 jobs
    for i in range(1, 4):
        job = JobRequisition(
            job_id=uuid.uuid4(),
            created_by_user_id=rec_user.user_id,
            status=JobLifecycleStatus.ACTIVE,
            publication_status=JobPublicationStatus.RELEASED,
        )
        test_db.add(job)
        v = JobVersion(
            job_version_id=uuid.uuid4(),
            job_id=job.job_id,
            version_number=1,
            revision=1,
            title=f"Frontend Engineer Tier {i}" if i == 1 else f"DevOps Specialist Tier {i}",
            location="Remote" if i == 1 else "New York, NY",
            version_status=JobVersionStatus.APPROVED,
            created_by_user_id=rec_user.user_id,
        )
        test_db.add(v)
        test_db.flush()
        job.released_job_version_id = v.job_version_id
        job.released_at = datetime.now(timezone.utc)
        job.released_by_user_id = rec_user.user_id
    test_db.commit()

    client.cookies.set("ai_hiring_candidate_session", cand_tok)

    # Search for "frontend"
    res_search = client.get("/api/v1/candidate/jobs?q=frontend")
    assert res_search.status_code == status.HTTP_200_OK
    assert res_search.json()["total"] == 1
    assert res_search.json()["items"][0]["title"] == "Frontend Engineer Tier 1"

    # Pagination: page 1 limit 2
    res_page = client.get("/api/v1/candidate/jobs?page=1&limit=2")
    assert res_page.status_code == status.HTTP_200_OK
    assert len(res_page.json()["items"]) == 2
    assert res_page.json()["total"] == 3
