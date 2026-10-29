import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models.auth import (
    Base,
    UserRecord,
    UserRole,
)
from app.models.job import (
    EmploymentType,
    JobLifecycleStatus,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
    WorkplaceType,
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
# 1. Model & Migration Tests
# =========================================================================

def test_job_models_and_database_constraints(test_db):
    recruiter = _seed_user(test_db, email="recruiter.m@company.test", role=UserRole.RECRUITER)
    now = datetime.now(timezone.utc)

    # Valid JobRequisition and JobVersion
    req = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter.user_id,
        status=JobLifecycleStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    test_db.add(req)
    test_db.commit()

    ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=req.job_id,
        version_number=1,
        version_status=JobVersionStatus.DRAFT,
        revision=1,
        title="Senior Backend Engineer",
        department="Engineering",
        location="San Francisco, CA",
        employment_type=EmploymentType.FULL_TIME,
        workplace_type=WorkplaceType.HYBRID,
        openings_count=2,
        created_by_user_id=recruiter.user_id,
        created_at=now,
        updated_at=now,
    )
    test_db.add(ver)
    test_db.commit()

    # Constraint check: version_number >= 1
    ver_invalid_vnum = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=req.job_id,
        version_number=0,
        version_status=JobVersionStatus.DRAFT,
        revision=1,
        title="Invalid Version Number",
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(ver_invalid_vnum)
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()

    # Constraint check: revision >= 1
    ver_invalid_rev = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=req.job_id,
        version_number=2,
        version_status=JobVersionStatus.DRAFT,
        revision=0,
        title="Invalid Revision",
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(ver_invalid_rev)
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()

    # Constraint check: openings_count > 0
    ver_invalid_openings = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=req.job_id,
        version_number=2,
        version_status=JobVersionStatus.DRAFT,
        revision=1,
        title="Invalid Openings",
        openings_count=0,
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(ver_invalid_openings)
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()

    # Constraint check: unique (job_id, version_number)
    ver_duplicate = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=req.job_id,
        version_number=1,  # Duplicate version 1
        version_status=JobVersionStatus.DRAFT,
        revision=1,
        title="Duplicate Version",
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(ver_duplicate)
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()


def test_migration_upgrade_and_downgrade_isolated(capsys):
    from alembic import command
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parent.parent
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    alembic_cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg://dummy:dummy@localhost:5432/dummy_isolated_test")

    # Verify offline upgrade SQL generation from e1a05c742301 to a5b1e62c4d99
    command.upgrade(alembic_cfg, "e1a05c742301:a5b1e62c4d99", sql=True)
    captured_upgrade = capsys.readouterr()
    assert "CREATE TABLE job_requisitions" in captured_upgrade.out
    assert "CREATE TABLE job_versions" in captured_upgrade.out
    assert "ck_job_versions_version_number_positive" in captured_upgrade.out
    assert "uq_job_versions_job_id_version_number" in captured_upgrade.out

    # Verify offline downgrade SQL generation from a5b1e62c4d99 back to e1a05c742301
    command.downgrade(alembic_cfg, "a5b1e62c4d99:e1a05c742301", sql=True)
    captured_downgrade = capsys.readouterr()
    assert "DROP TABLE job_versions" in captured_downgrade.out
    assert "DROP TABLE job_requisitions" in captured_downgrade.out


# =========================================================================
# 2. Creation Tests (POST /api/v1/jobs)
# =========================================================================

def test_recruiter_creates_valid_job(client, test_db):
    recruiter = _seed_user(test_db, email="recruiter1@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter1@company.test")

    payload = {
        "title": "Lead Software Engineer",
        "department": "Infrastructure",
        "location": "Remote",
        "employmentType": "FULL_TIME",
        "workplaceType": "REMOTE",
        "openingsCount": 3,
        "internalReference": "REQ-2026-001",
        "summary": "Looking for an experienced lead software engineer.",
        "responsibilities": "Design distributed systems and mentor junior engineers.",
        "jobDescriptionText": "Full text of the job description goes here.",
    }

    resp = client.post("/api/v1/jobs", json=payload)
    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()

    assert "jobId" in data
    assert data["status"] == "ACTIVE"
    assert data["createdByUserId"] == str(recruiter.user_id)

    v = data["currentVersion"]
    assert v["versionNumber"] == 1
    assert v["versionStatus"] == "DRAFT"
    assert v["revision"] == 1
    assert v["title"] == "Lead Software Engineer"
    assert v["department"] == "Infrastructure"
    assert v["location"] == "Remote"
    assert v["employmentType"] == "FULL_TIME"
    assert v["workplaceType"] == "REMOTE"
    assert v["openingsCount"] == 3
    assert v["internalReference"] == "REQ-2026-001"
    assert v["summary"] == "Looking for an experienced lead software engineer."
    assert v["responsibilities"] == "Design distributed systems and mentor junior engineers."
    assert v["jobDescriptionText"] == "Full text of the job description goes here."
    assert v["createdByUserId"] == str(recruiter.user_id)
    assert v["approvedByUserId"] is None


def test_admin_can_create_job(client, test_db):
    admin = _seed_user(test_db, email="admin1@company.test", role=UserRole.ADMIN)
    _login_user(client, email="admin1@company.test")

    payload = {"title": "Staff Architect"}
    resp = client.post("/api/v1/jobs", json=payload)
    assert resp.status_code == status.HTTP_201_CREATED
    assert resp.json()["createdByUserId"] == str(admin.user_id)


def test_reviewer_and_unauthenticated_forbidden(client, test_db):
    # Unauthenticated
    resp_unauth = client.post("/api/v1/jobs", json={"title": "Unauthorized Job"})
    assert resp_unauth.status_code == status.HTTP_401_UNAUTHORIZED

    # Reviewer (Candidate role equivalent in auth system)
    _seed_user(test_db, email="reviewer1@company.test", role=UserRole.REVIEWER)
    _login_user(client, email="reviewer1@company.test")

    resp_forbidden = client.post("/api/v1/jobs", json={"title": "Forbidden Job"})
    assert resp_forbidden.status_code == status.HTTP_403_FORBIDDEN
    assert resp_forbidden.json()["detail"]["code"] == "FORBIDDEN"


def test_must_change_password_user_blocked(client, test_db):
    _seed_user(test_db, email="pw_change@company.test", role=UserRole.RECRUITER, must_change_password=True)
    _login_user(client, email="pw_change@company.test")

    resp = client.post("/api/v1/jobs", json={"title": "Blocked Job"})
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert resp.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"


def test_job_validation_rejections(client, test_db):
    _seed_user(test_db, email="val.recruiter@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="val.recruiter@company.test")

    # Missing title
    r1 = client.post("/api/v1/jobs", json={})
    assert r1.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Blank / whitespace-only title
    r2 = client.post("/api/v1/jobs", json={"title": "   "})
    assert r2.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Invalid enum
    r3 = client.post("/api/v1/jobs", json={"title": "Engineer", "employmentType": "INVALID_TYPE"})
    assert r3.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Invalid openings_count (<= 0)
    r4 = client.post("/api/v1/jobs", json={"title": "Engineer", "openingsCount": 0})
    assert r4.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Oversized text (> 10000 chars for summary)
    r5 = client.post("/api/v1/jobs", json={"title": "Engineer", "summary": "x" * 10001})
    assert r5.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_creator_identity_taken_from_auth_not_request(client, test_db):
    recruiter = _seed_user(test_db, email="recruiter.identity@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.identity@company.test")

    fake_user_id = str(uuid.uuid4())
    # Extra field rejected or ignored, actor always authenticated user
    resp = client.post("/api/v1/jobs", json={"title": "Test Engineer", "createdByUserId": fake_user_id})
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY  # StrictSchema forbids extra fields


def test_idempotent_job_creation(client, test_db):
    recruiter = _seed_user(test_db, email="idem.recruiter@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="idem.recruiter@company.test")

    payload = {"title": "Principal Architect", "department": "Platform"}
    headers = {"Idempotency-Key": "job-key-12345"}

    # 1. First creation
    resp1 = client.post("/api/v1/jobs", json=payload, headers=headers)
    assert resp1.status_code == status.HTTP_201_CREATED
    job_id_1 = resp1.json()["jobId"]

    # 2. Identical retry with same key
    resp2 = client.post("/api/v1/jobs", json=payload, headers=headers)
    assert resp2.status_code == status.HTTP_201_CREATED
    assert resp2.json()["jobId"] == job_id_1
    assert resp2.json()["currentVersion"]["versionNumber"] == 1

    # Verify only 1 requisition was created in database
    stmt = select(JobRequisition)
    reqs = test_db.execute(stmt).scalars().all()
    assert len(reqs) == 1

    # 3. Same key with differing payload -> 409 Conflict
    diff_payload = {"title": "Senior Architect", "department": "Platform"}
    resp3 = client.post("/api/v1/jobs", json=diff_payload, headers=headers)
    assert resp3.status_code == status.HTTP_409_CONFLICT
    assert resp3.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


# =========================================================================
# 3. List & Read Tests (GET /api/v1/jobs, GET /api/v1/jobs/{job_id})
# =========================================================================

def test_recruiter_lists_only_own_jobs_and_admin_lists_all(client, test_db):
    recruiter_a = _seed_user(test_db, email="recruiter.a@company.test", role=UserRole.RECRUITER)
    recruiter_b = _seed_user(test_db, email="recruiter.b@company.test", role=UserRole.RECRUITER)
    admin = _seed_user(test_db, email="admin.list@company.test", role=UserRole.ADMIN)

    # Recruiter A creates 2 jobs
    _login_user(client, email="recruiter.a@company.test")
    client.post("/api/v1/jobs", json={"title": "Job A1"})
    client.post("/api/v1/jobs", json={"title": "Job A2"})

    # Recruiter B creates 1 job
    _login_user(client, email="recruiter.b@company.test")
    client.post("/api/v1/jobs", json={"title": "Job B1"})

    # Recruiter A listing
    _login_user(client, email="recruiter.a@company.test")
    resp_a = client.get("/api/v1/jobs")
    assert resp_a.status_code == status.HTTP_200_OK
    jobs_a = resp_a.json()
    assert len(jobs_a) == 2
    titles_a = {j["currentVersion"]["title"] for j in jobs_a}
    assert titles_a == {"Job A1", "Job A2"}
    # Verify summary excludes heavy text fields
    assert "jobDescriptionText" not in jobs_a[0]["currentVersion"]
    assert "responsibilities" not in jobs_a[0]["currentVersion"]

    # Recruiter B listing
    _login_user(client, email="recruiter.b@company.test")
    resp_b = client.get("/api/v1/jobs")
    assert resp_b.status_code == status.HTTP_200_OK
    jobs_b = resp_b.json()
    assert len(jobs_b) == 1
    assert jobs_b[0]["currentVersion"]["title"] == "Job B1"

    # Admin listing
    _login_user(client, email="admin.list@company.test")
    resp_admin = client.get("/api/v1/jobs")
    assert resp_admin.status_code == status.HTTP_200_OK
    jobs_admin = resp_admin.json()
    assert len(jobs_admin) == 3


def test_job_detail_and_cross_owner_isolation(client, test_db):
    recruiter_a = _seed_user(test_db, email="recruiter.owner@company.test", role=UserRole.RECRUITER)
    recruiter_b = _seed_user(test_db, email="recruiter.intruder@company.test", role=UserRole.RECRUITER)

    # Recruiter A creates a job
    _login_user(client, email="recruiter.owner@company.test")
    create_resp = client.post(
        "/api/v1/jobs",
        json={"title": "Secret Project Lead", "summary": "Top secret role"},
    )
    job_id = create_resp.json()["jobId"]

    # Recruiter A can read detail
    read_resp = client.get(f"/api/v1/jobs/{job_id}")
    assert read_resp.status_code == status.HTTP_200_OK
    assert read_resp.json()["currentVersion"]["title"] == "Secret Project Lead"
    assert read_resp.json()["currentVersion"]["summary"] == "Top secret role"

    # Recruiter B attempts to read Recruiter A's job -> 404 Concealed
    _login_user(client, email="recruiter.intruder@company.test")
    intrude_resp = client.get(f"/api/v1/jobs/{job_id}")
    assert intrude_resp.status_code == status.HTTP_404_NOT_FOUND
    assert intrude_resp.json()["detail"]["code"] == "JOB_NOT_FOUND"


# =========================================================================
# 4. Draft Update Tests (PATCH /api/v1/jobs/{job_id}/draft)
# =========================================================================

def test_update_draft_success_and_revision_increment(client, test_db):
    _seed_user(test_db, email="recruiter.update@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.update@company.test")

    # Create job with revision 1
    create_resp = client.post(
        "/api/v1/jobs",
        json={"title": "Initial Title", "department": "Sales"},
    )
    job_id = create_resp.json()["jobId"]
    assert create_resp.json()["currentVersion"]["revision"] == 1

    # 1. Update draft with expected_revision=1
    patch_resp = client.patch(
        f"/api/v1/jobs/{job_id}/draft",
        json={"expectedRevision": 1, "title": "Updated Title", "openingsCount": 5},
    )
    assert patch_resp.status_code == status.HTTP_200_OK
    v = patch_resp.json()["currentVersion"]
    assert v["revision"] == 2
    assert v["title"] == "Updated Title"
    assert v["department"] == "Sales"  # Preserved
    assert v["openingsCount"] == 5

    # 2. Next update with expected_revision=2
    patch_resp_2 = client.patch(
        f"/api/v1/jobs/{job_id}/draft",
        json={"expectedRevision": 2, "location": "Austin, TX"},
    )
    assert patch_resp_2.status_code == status.HTTP_200_OK
    v2 = patch_resp_2.json()["currentVersion"]
    assert v2["revision"] == 3
    assert v2["location"] == "Austin, TX"


def test_stale_revision_conflict(client, test_db):
    _seed_user(test_db, email="recruiter.conflict@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.conflict@company.test")

    create_resp = client.post("/api/v1/jobs", json={"title": "DevOps Engineer"})
    job_id = create_resp.json()["jobId"]

    # 1. Update to revision 2
    client.patch(
        f"/api/v1/jobs/{job_id}/draft",
        json={"expectedRevision": 1, "department": "Operations"},
    )

    # 2. Attempt update using stale revision 1 -> 409 Conflict
    stale_resp = client.patch(
        f"/api/v1/jobs/{job_id}/draft",
        json={"expectedRevision": 1, "title": "Stale Title"},
    )
    assert stale_resp.status_code == status.HTTP_409_CONFLICT
    assert stale_resp.json()["detail"]["code"] == "REVISION_CONFLICT"


def test_unauthorized_and_cross_owner_update_blocked(client, test_db):
    _seed_user(test_db, email="recruiter.main@company.test", role=UserRole.RECRUITER)
    _seed_user(test_db, email="recruiter.other@company.test", role=UserRole.RECRUITER)

    # Recruiter Main creates job
    _login_user(client, email="recruiter.main@company.test")
    create_resp = client.post("/api/v1/jobs", json={"title": "Data Scientist"})
    job_id = create_resp.json()["jobId"]

    # Recruiter Other tries to patch Recruiter Main's job -> 404 Concealed
    _login_user(client, email="recruiter.other@company.test")
    patch_resp = client.patch(
        f"/api/v1/jobs/{job_id}/draft",
        json={"expectedRevision": 1, "title": "Hacked Title"},
    )
    assert patch_resp.status_code == status.HTTP_404_NOT_FOUND

    # Candidate / Reviewer tries to patch -> 403 Forbidden
    _seed_user(test_db, email="candidate.patch@company.test", role=UserRole.REVIEWER)
    _login_user(client, email="candidate.patch@company.test")
    patch_resp_cand = client.patch(
        f"/api/v1/jobs/{job_id}/draft",
        json={"expectedRevision": 1, "title": "Candidate Title"},
    )
    assert patch_resp_cand.status_code == status.HTTP_403_FORBIDDEN


def test_update_non_draft_version_rejected(client, test_db):
    recruiter = _seed_user(test_db, email="recruiter.approved@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.approved@company.test")

    # Seed an APPROVED version
    req = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(req)
    test_db.commit()

    ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=req.job_id,
        version_number=1,
        version_status=JobVersionStatus.APPROVED,  # Non-draft
        revision=1,
        title="Approved Role",
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(ver)
    test_db.commit()

    # Attempt patch on non-draft -> 409 JOB_NOT_DRAFT
    resp = client.patch(
        f"/api/v1/jobs/{req.job_id}/draft",
        json={"expectedRevision": 1, "title": "Modified Title"},
    )
    assert resp.status_code == status.HTTP_409_CONFLICT
    assert resp.json()["detail"]["code"] == "JOB_NOT_DRAFT"
