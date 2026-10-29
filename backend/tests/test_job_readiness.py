import uuid
from datetime import datetime, timezone
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models.auth import Base, UserRecord, UserRole
from app.models.job import (
    EmploymentType,
    JobLifecycleStatus,
    JobRequirement,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
    RequirementCategory,
    RequirementNecessity,
    RequirementOrigin,
    RequirementPriority,
    WorkplaceType,
)
from app.models.taxonomy import (
    OccupationConcept,
    SkillConcept,
    TaxonomySnapshot,
    TaxonomySnapshotState,
)
from app.services.security import canonicalize_email, hash_password


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
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
    email="recruiter.readiness@company.test",
    password="Password123!",
    role=UserRole.RECRUITER,
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
    db.refresh(user)
    return user


def _login_user(client: TestClient, email="recruiter.readiness@company.test", password="Password123!"):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == status.HTTP_200_OK
    return resp.json()["csrfToken"], resp.json()["user"]


@pytest.fixture
def sample_active_snapshot(test_db) -> TaxonomySnapshot:
    snapshot = TaxonomySnapshot(
        snapshot_id=uuid.uuid4(),
        source="ONET",
        source_version="31.0",
        combined_sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        state=TaxonomySnapshotState.ACTIVE,
        activated_at=datetime.now(timezone.utc),
    )
    test_db.add(snapshot)
    test_db.commit()
    test_db.refresh(snapshot)
    return snapshot


@pytest.fixture
def sample_onet_skill(test_db, sample_active_snapshot: TaxonomySnapshot) -> SkillConcept:
    skill = SkillConcept(
        concept_id=uuid.uuid4(),
        snapshot_id=sample_active_snapshot.snapshot_id,
        canonical_label="Python",
        normalized_label="python",
        category="Programming language",
        source="ONET_31_0",
        external_id="ONET_SKILL_PY",
    )
    test_db.add(skill)
    test_db.commit()
    test_db.refresh(skill)
    return skill


@pytest.fixture
def sample_onet_occupation(test_db, sample_active_snapshot: TaxonomySnapshot) -> OccupationConcept:
    occ = OccupationConcept(
        concept_id=uuid.uuid4(),
        snapshot_id=sample_active_snapshot.snapshot_id,
        onet_soc_code="15-1252.00",
        canonical_title="Software Developers",
        normalized_title="software developers",
        source_provenance="ONET_31_0",
    )
    test_db.add(occ)
    test_db.commit()
    test_db.refresh(occ)
    return occ


def test_migration_e2f5a7b9c1d4_upgrade_and_downgrade_isolated(capsys):
    """Verify migration e2f5a7b9c1d4 applies and rolls back cleanly."""
    from pathlib import Path
    from alembic import command
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parent.parent
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    alembic_cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg://dummy:dummy@localhost:5432/dummy_isolated_test")

    # Test upgrade SQL generation
    command.upgrade(alembic_cfg, "d1e4f6a8b2c3:e2f5a7b9c1d4", sql=True)
    captured_upgrade = capsys.readouterr()
    assert "ready_by_user_id" in captured_upgrade.out
    assert "ready_at" in captured_upgrade.out

    # Test downgrade SQL generation
    command.downgrade(alembic_cfg, "e2f5a7b9c1d4:d1e4f6a8b2c3", sql=True)
    captured_downgrade = capsys.readouterr()
    assert "ready_by_user_id" in captured_downgrade.out or "DROP COLUMN" in captured_downgrade.out



def test_readiness_check_empty_draft_blocks_readiness(client: TestClient, test_db):
    _seed_user(test_db, email="recruiter.ready1@company.test")
    _login_user(client, email="recruiter.ready1@company.test")

    # 1. Create a draft with title only, 0 requirements
    create_resp = client.post(
        "/api/v1/jobs",
        json={"title": "Junior Developer"},
    )
    assert create_resp.status_code == 201
    job_id = create_resp.json()["jobId"]

    # 2. Check readiness
    readiness_resp = client.get(
        f"/api/v1/jobs/{job_id}/readiness",
    )
    assert readiness_resp.status_code == 200
    data = readiness_resp.json()
    assert data["isReady"] is False
    assert any(e["code"] == "NO_REQUIREMENTS" for e in data["errors"])
    assert data["versionStatus"] == "DRAFT"


def test_readiness_check_must_have_missing_rationale_blocks(client: TestClient, test_db):
    _seed_user(test_db, email="recruiter.ready2@company.test")
    _login_user(client, email="recruiter.ready2@company.test")

    create_resp = client.post(
        "/api/v1/jobs",
        json={"title": "Backend Developer"},
    )
    job_id = create_resp.json()["jobId"]
    rev = create_resp.json()["currentVersion"]["revision"]

    # Save requirement with MUST_HAVE but no rationale
    save_resp = client.put(
        f"/api/v1/jobs/{job_id}/draft/requirements",
        json={
            "expectedRevision": rev,
            "requirements": [
                {
                    "displayOrder": 0,
                    "category": "SKILL",
                    "label": "FastAPI",
                    "necessity": "MUST_HAVE",
                    "priority": "HIGH",
                    "rationale": "",  # Missing rationale
                }
            ],
        },
    )
    assert save_resp.status_code == 200
    rev2 = save_resp.json()["currentVersion"]["revision"]

    # Readiness check
    readiness_resp = client.get(
        f"/api/v1/jobs/{job_id}/readiness?expected_revision={rev2}",
    )
    assert readiness_resp.status_code == 200
    data = readiness_resp.json()
    assert data["isReady"] is False
    assert any(e["code"] == "MISSING_MUST_HAVE_RATIONALE" for e in data["errors"])


def test_mark_ready_success_custom_and_onet_requirements(
    client: TestClient,
    test_db,
    sample_onet_skill: SkillConcept,
    sample_onet_occupation: OccupationConcept,
):
    recruiter = _seed_user(test_db, email="recruiter.ready3@company.test")
    _login_user(client, email="recruiter.ready3@company.test")

    # 1. Create job with occupation reference
    create_resp = client.post(
        "/api/v1/jobs",
        json={
            "title": "Senior Python Engineer",
            "department": "Core Platform",
            "location": "San Francisco, CA",
            "employmentType": "FULL_TIME",
            "workplaceType": "REMOTE",
            "occupationConceptId": str(sample_onet_occupation.concept_id),
        },
    )
    assert create_resp.status_code == 201
    job_id = create_resp.json()["jobId"]
    rev = create_resp.json()["currentVersion"]["revision"]

    # 2. Add 1 O*NET skill (MUST_HAVE with rationale) and 1 custom skill (PREFERRED)
    save_resp = client.put(
        f"/api/v1/jobs/{job_id}/draft/requirements",
        json={
            "expectedRevision": rev,
            "requirements": [
                {
                    "displayOrder": 0,
                    "category": "SKILL",
                    "label": "Python",
                    "taxonomySkillConceptId": str(sample_onet_skill.concept_id),
                    "necessity": "MUST_HAVE",
                    "priority": "HIGH",
                    "rationale": "Core backend language for our microservices architecture.",
                },
                {
                    "displayOrder": 1,
                    "category": "SKILL",
                    "label": "Distributed Systems Design",
                    "necessity": "PREFERRED",
                    "priority": "MEDIUM",
                },
            ],
        },
    )
    assert save_resp.status_code == 200
    rev2 = save_resp.json()["currentVersion"]["revision"]

    # 3. Check readiness
    readiness_resp = client.get(
        f"/api/v1/jobs/{job_id}/readiness?expected_revision={rev2}",
    )
    assert readiness_resp.status_code == 200
    r_data = readiness_resp.json()
    assert r_data["isReady"] is True
    assert len(r_data["errors"]) == 0

    # 4. Mark Ready
    ready_resp = client.post(
        f"/api/v1/jobs/{job_id}/ready",
        json={"expectedRevision": rev2},
    )
    assert ready_resp.status_code == 200
    v_data = ready_resp.json()["currentVersion"]
    assert v_data["versionStatus"] == "READY_FOR_MATCHING"
    assert v_data["readyByUserId"] == str(recruiter.user_id)
    assert v_data["readyAt"] is not None

    # 5. Idempotent call with same expected revision
    ready_idem_resp = client.post(
        f"/api/v1/jobs/{job_id}/ready",
        json={"expectedRevision": rev2},
    )
    assert ready_idem_resp.status_code == 200
    assert ready_idem_resp.json()["currentVersion"]["versionStatus"] == "READY_FOR_MATCHING"


def test_immutable_revision_locks_modifications(
    client: TestClient,
    test_db,
    sample_onet_skill: SkillConcept,
):
    _seed_user(test_db, email="recruiter.ready4@company.test")
    _login_user(client, email="recruiter.ready4@company.test")

    # 1. Create & Mark Ready
    create_resp = client.post(
        "/api/v1/jobs",
        json={"title": "Staff Engineer"},
    )
    job_id = create_resp.json()["jobId"]
    rev = create_resp.json()["currentVersion"]["revision"]

    save_resp = client.put(
        f"/api/v1/jobs/{job_id}/draft/requirements",
        json={
            "expectedRevision": rev,
            "requirements": [
                {
                    "displayOrder": 0,
                    "category": "SKILL",
                    "label": "Python",
                    "taxonomySkillConceptId": str(sample_onet_skill.concept_id),
                    "necessity": "MUST_HAVE",
                    "priority": "HIGH",
                    "rationale": "Critical infrastructure requirement.",
                }
            ],
        },
    )
    rev2 = save_resp.json()["currentVersion"]["revision"]

    ready_resp = client.post(
        f"/api/v1/jobs/{job_id}/ready",
        json={"expectedRevision": rev2},
    )
    assert ready_resp.status_code == 200

    # 2. Attempt to update draft on locked ready revision -> 409 JOB_NOT_DRAFT
    patch_resp = client.patch(
        f"/api/v1/jobs/{job_id}/draft",
        json={"expectedRevision": rev2, "title": "New Title Attempt"},
    )
    assert patch_resp.status_code == 409
    assert patch_resp.json()["detail"]["code"] == "JOB_NOT_DRAFT"

    # 3. Attempt to update requirements on locked ready revision -> 409 JOB_NOT_DRAFT
    put_req_resp = client.put(
        f"/api/v1/jobs/{job_id}/draft/requirements",
        json={"expectedRevision": rev2, "requirements": []},
    )
    assert put_req_resp.status_code == 409
    assert put_req_resp.json()["detail"]["code"] == "JOB_NOT_DRAFT"


def test_create_successor_version_and_history(
    client: TestClient,
    test_db,
    sample_onet_skill: SkillConcept,
):
    _seed_user(test_db, email="recruiter.ready5@company.test")
    _login_user(client, email="recruiter.ready5@company.test")

    # 1. Setup Ready version 1
    create_resp = client.post(
        "/api/v1/jobs",
        json={"title": "Principal Architect", "department": "R&D"},
    )
    job_id = create_resp.json()["jobId"]
    rev1 = create_resp.json()["currentVersion"]["revision"]

    save_resp = client.put(
        f"/api/v1/jobs/{job_id}/draft/requirements",
        json={
            "expectedRevision": rev1,
            "requirements": [
                {
                    "displayOrder": 0,
                    "category": "SKILL",
                    "label": "Python",
                    "taxonomySkillConceptId": str(sample_onet_skill.concept_id),
                    "necessity": "MUST_HAVE",
                    "priority": "CRITICAL",
                    "rationale": "High throughput systems.",
                }
            ],
        },
    )
    rev2 = save_resp.json()["currentVersion"]["revision"]

    client.post(
        f"/api/v1/jobs/{job_id}/ready",
        json={"expectedRevision": rev2},
    )

    # 2. Create Successor Version
    successor_resp = client.post(
        f"/api/v1/jobs/{job_id}/versions",
        json={},
    )
    assert successor_resp.status_code == 201
    v2_data = successor_resp.json()["currentVersion"]
    assert v2_data["versionNumber"] == 2
    assert v2_data["versionStatus"] == "DRAFT"
    assert v2_data["revision"] == 1
    assert v2_data["title"] == "Principal Architect"
    assert len(v2_data["requirements"]) == 1
    assert v2_data["readyByUserId"] is None

    # 3. Verify Version History endpoint
    history_resp = client.get(
        f"/api/v1/jobs/{job_id}/versions",
    )
    assert history_resp.status_code == 200
    versions_list = history_resp.json()
    assert len(versions_list) == 2
    assert versions_list[0]["versionNumber"] == 2
    assert versions_list[0]["versionStatus"] == "DRAFT"
    assert versions_list[1]["versionNumber"] == 1
    assert versions_list[1]["versionStatus"] == "READY_FOR_MATCHING"


def test_cross_recruiter_and_role_access_denial(client: TestClient, test_db):
    _seed_user(test_db, email="recruiter.owner@company.test")
    _seed_user(test_db, email="recruiter.other@company.test")
    _seed_user(test_db, email="reviewer.unauthorized@company.test", role=UserRole.REVIEWER)

    # Recruiter A creates job
    _login_user(client, email="recruiter.owner@company.test")
    create_resp = client.post(
        "/api/v1/jobs",
        json={"title": "Private Role"},
    )
    assert create_resp.status_code == 201
    job_id = create_resp.json()["jobId"]

    # Recruiter B attempts to check readiness -> 404 concealment
    _login_user(client, email="recruiter.other@company.test")
    resp_b_readiness = client.get(
        f"/api/v1/jobs/{job_id}/readiness",
    )
    assert resp_b_readiness.status_code == 404

    # Recruiter B attempts to mark ready -> 404 concealment
    resp_b_ready = client.post(
        f"/api/v1/jobs/{job_id}/ready",
        json={"expectedRevision": 1},
    )
    assert resp_b_ready.status_code == 404

    # Recruiter B attempts to create successor -> 404 concealment
    resp_b_succ = client.post(
        f"/api/v1/jobs/{job_id}/versions",
        json={},
    )
    assert resp_b_succ.status_code == 404

    # Reviewer attempts to access readiness -> 403 Forbidden
    _login_user(client, email="reviewer.unauthorized@company.test")
    resp_rev = client.get(
        f"/api/v1/jobs/{job_id}/readiness",
    )
    assert resp_rev.status_code == 403
