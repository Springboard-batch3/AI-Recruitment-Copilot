import csv
import io
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

from app.cli.import_onet import import_onet_taxonomy
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
    RequirementEvidenceType,
    RequirementNecessity,
    RequirementOrigin,
    RequirementPriority,
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


@pytest.fixture
def synthetic_taxonomy(tmp_path, test_db):
    d = tmp_path / "synthetic_onet_req"
    d.mkdir()

    occ_rows = [
        ["O*NET-SOC Code", "Title", "Description"],
        ["15-1252.00", "Software Developers", "Research, design, and develop software."],
    ]
    with open(d / "occupation_data.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(occ_rows)

    title_rows = [
        ["O*NET-SOC Code", "Title", "Job Title", "Short Title", "Source(s)"],
        ["15-1252.00", "Software Developers", "Full Stack Developer", "", "08"],
    ]
    with open(d / "job_titles.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(title_rows)

    skill_rows = [
        ["O*NET-SOC Code", "Title", "Workplace Example", "Element ID", "Element Name", "Hot Technology", "In Demand"],
        ["15-1252.00", "Software Developers", "Python", "2.E.1.a", "Programming language", "Y", "Y"],
        ["15-1252.00", "Software Developers", "PostgreSQL", "2.E.6.f", "Database", "Y", "N"],
    ]
    with open(d / "software_skills.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(skill_rows)

    essential_rows = [
        ["O*NET-SOC Code", "Title", "Element ID", "Element Name", "Scale ID", "Data Value", "Recommend Suppress", "Not Relevant"],
        ["15-1252.00", "Software Developers", "2.A.1.a", "Reading Comprehension", "IM", "4.0", "N", "N"],
    ]
    with open(d / "essential_skills.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(essential_rows)

    transferable_rows = [
        ["O*NET-SOC Code", "Title", "Element ID", "Element Name", "Scale ID", "Data Value", "Recommend Suppress", "Not Relevant"],
        ["15-1252.00", "Software Developers", "2.B.1.a", "Critical Thinking", "IM", "4.25", "N", "N"],
    ]
    with open(d / "transferable_skills.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(transferable_rows)

    import_onet_taxonomy(source_dir=d, version="31.0", session=test_db)
    return d


# =========================================================================
# 1. Model & Migration Tests
# =========================================================================

def test_job_requirement_models_and_constraints(test_db):
    recruiter = _seed_user(test_db, email="recruiter.req@company.test", role=UserRole.RECRUITER)
    now = datetime.now(timezone.utc)

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
        version_status=JobVersionStatus.DRAFT,
        revision=1,
        title="Senior Python Engineer",
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(ver)
    test_db.commit()

    # Add valid JobRequirement
    jr1 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Python",
        normalized_label="python",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.CRITICAL,
        min_experience_months=36,
        recency_years=3,
        custom_weight_override=1.5,
        accepted_evidence_types=["EMPLOYMENT", "PROJECT"],
        accepted_equivalents=["Python 3", "PyPy"],
        rationale="Core service backend language.",
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    test_db.add(jr1)
    test_db.commit()

    # Constraint check: duplicate (job_version_id, category, normalized_label)
    jr_dup = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        display_order=1,
        category=RequirementCategory.SKILL,
        label="Python 3.11",
        normalized_label="python",  # Duplicate normalized_label
        necessity=RequirementNecessity.PREFERRED,
        priority=RequirementPriority.MEDIUM,
    )
    test_db.add(jr_dup)
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()


def test_migration_c9d3e5f7a2b1_upgrade_and_downgrade_isolated(capsys):
    from alembic import command
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parent.parent
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    alembic_cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg://dummy:dummy@localhost:5432/dummy_isolated_test")

    # Verify offline upgrade SQL generation from b7c2d8e4f1a0 to c9d3e5f7a2b1
    command.upgrade(alembic_cfg, "b7c2d8e4f1a0:c9d3e5f7a2b1", sql=True)
    captured_upgrade = capsys.readouterr()
    assert "CREATE TABLE job_requirements" in captured_upgrade.out
    assert "occupation_concept_id" in captured_upgrade.out
    assert "uq_job_requirements_version_category_normalized_label" in captured_upgrade.out

    # Verify offline downgrade SQL generation from c9d3e5f7a2b1 back to b7c2d8e4f1a0
    command.downgrade(alembic_cfg, "c9d3e5f7a2b1:b7c2d8e4f1a0", sql=True)
    captured_downgrade = capsys.readouterr()
    assert "DROP TABLE job_requirements" in captured_downgrade.out
    assert "DROP COLUMN occupation_concept_id" in captured_downgrade.out or "occupation_concept_id" in captured_downgrade.out


# =========================================================================
# 2. Atomic Requirement Save Tests (PUT /api/v1/jobs/{job_id}/draft/requirements)
# =========================================================================

def test_atomic_requirement_save_success_and_revision_increment(client, test_db, synthetic_taxonomy):
    recruiter = _seed_user(test_db, email="recruiter.save@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.save@company.test")

    # Fetch skill concept for Python from DB
    py_skill = test_db.execute(select(SkillConcept).where(SkillConcept.normalized_label == "python")).scalar_one()

    # Create job draft
    create_resp = client.post("/api/v1/jobs", json={"title": "Backend Lead"})
    assert create_resp.status_code == status.HTTP_201_CREATED
    job_id = create_resp.json()["jobId"]
    assert create_resp.json()["currentVersion"]["revision"] == 1

    payload = {
        "expectedRevision": 1,
        "requirements": [
            {
                "displayOrder": 0,
                "category": "SKILL",
                "label": "Python",
                "taxonomySkillConceptId": str(py_skill.concept_id),
                "necessity": "MUST_HAVE",
                "priority": "CRITICAL",
                "minExperienceMonths": 48,
                "recencyYears": 5,
                "acceptedEvidenceTypes": ["EMPLOYMENT", "PROJECT"],
                "acceptedEquivalents": ["CPython", "Python 3"],
                "rationale": "Core backend development.",
            },
            {
                "displayOrder": 1,
                "category": "SKILL",
                "label": "PostgreSQL",
                "necessity": "PREFERRED",
                "priority": "HIGH",
                "rationale": "Primary relational datastore.",
            },
            {
                "displayOrder": 2,
                "category": "EXPERIENCE",
                "label": "Distributed Systems Architecture",
                "necessity": "MUST_HAVE",
                "priority": "HIGH",
                "minExperienceMonths": 36,
                "rationale": "Microservice scalability and reliability.",
            },
        ],
    }

    put_resp = client.put(f"/api/v1/jobs/{job_id}/draft/requirements", json=payload)
    assert put_resp.status_code == status.HTTP_200_OK
    data = put_resp.json()

    v = data["currentVersion"]
    assert v["revision"] == 2
    assert len(v["requirements"]) == 3

    r0 = v["requirements"][0]
    assert r0["label"] == "Python"
    assert r0["origin"] == "TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED"
    assert r0["taxonomySkillConceptId"] == str(py_skill.concept_id)
    assert r0["necessity"] == "MUST_HAVE"
    assert r0["priority"] == "CRITICAL"
    assert r0["minExperienceMonths"] == 48
    assert r0["acceptedEquivalents"] == ["CPython", "Python 3"]
    assert r0["reviewRequired"] is False  # Has rationale, no sensitive words

    r1 = v["requirements"][1]
    assert r1["label"] == "PostgreSQL"
    assert r1["origin"] == "RECRUITER_ENTERED"  # Custom recruiter entry without taxonomy ID
    assert r1["taxonomySkillConceptId"] is None


def test_stale_revision_returns_409_and_changes_nothing(client, test_db):
    _seed_user(test_db, email="recruiter.stale@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.stale@company.test")

    create_resp = client.post("/api/v1/jobs", json={"title": "Data Engineer"})
    job_id = create_resp.json()["jobId"]

    # Initial requirements save (moves revision from 1 to 2)
    client.put(
        f"/api/v1/jobs/{job_id}/draft/requirements",
        json={
            "expectedRevision": 1,
            "requirements": [{"label": "SQL", "category": "SKILL", "necessity": "MUST_HAVE", "rationale": "DB queries"}],
        },
    )

    # Stale save with expectedRevision=1 -> 409 Conflict
    stale_resp = client.put(
        f"/api/v1/jobs/{job_id}/draft/requirements",
        json={
            "expectedRevision": 1,
            "requirements": [{"label": "NoSQL", "category": "SKILL", "necessity": "PREFERRED"}],
        },
    )
    assert stale_resp.status_code == status.HTTP_409_CONFLICT
    assert stale_resp.json()["detail"]["code"] == "REVISION_CONFLICT"

    # Verify original requirements remained intact
    get_resp = client.get(f"/api/v1/jobs/{job_id}")
    reqs = get_resp.json()["currentVersion"]["requirements"]
    assert len(reqs) == 1
    assert reqs[0]["label"] == "SQL"


def test_validation_failure_rolls_back_entire_save(client, test_db):
    _seed_user(test_db, email="recruiter.val@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.val@company.test")

    create_resp = client.post("/api/v1/jobs", json={"title": "QA Lead"})
    job_id = create_resp.json()["jobId"]

    # 1. Save valid initial requirement
    client.put(
        f"/api/v1/jobs/{job_id}/draft/requirements",
        json={
            "expectedRevision": 1,
            "requirements": [{"label": "Selenium", "category": "SKILL", "rationale": "Automated UI tests"}],
        },
    )

    # 2. Try saving with 1 valid and 1 invalid requirement (negative experience)
    invalid_payload = {
        "expectedRevision": 2,
        "requirements": [
            {"label": "Playwright", "category": "SKILL", "rationale": "E2E testing"},
            {"label": "Cypress", "category": "SKILL", "minExperienceMonths": -5},
        ],
    }
    fail_resp = client.put(f"/api/v1/jobs/{job_id}/draft/requirements", json=invalid_payload)
    assert fail_resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Verify original requirement unchanged and revision still 2
    get_resp = client.get(f"/api/v1/jobs/{job_id}")
    v = get_resp.json()["currentVersion"]
    assert v["revision"] == 2
    assert len(v["requirements"]) == 1
    assert v["requirements"][0]["label"] == "Selenium"


def test_duplicate_requirement_in_same_category_rejected(client, test_db):
    _seed_user(test_db, email="recruiter.dup@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.dup@company.test")

    create_resp = client.post("/api/v1/jobs", json={"title": "DevOps"})
    job_id = create_resp.json()["jobId"]

    dup_payload = {
        "expectedRevision": 1,
        "requirements": [
            {"label": "Docker", "category": "SKILL", "rationale": "Containerization"},
            {"label": "  docker  ", "category": "SKILL", "rationale": "Duplicate term"},
        ],
    }
    resp = client.put(f"/api/v1/jobs/{job_id}/draft/requirements", json=dup_payload)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert resp.json()["detail"]["code"] == "DUPLICATE_REQUIREMENT"


# =========================================================================
# 3. Taxonomy Concept Resolution & Spoof Protection
# =========================================================================

def test_server_derives_canonical_label_and_blocks_spoofing(client, test_db, synthetic_taxonomy):
    _seed_user(test_db, email="recruiter.spoof@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.spoof@company.test")

    py_skill = test_db.execute(select(SkillConcept).where(SkillConcept.normalized_label == "python")).scalar_one()

    create_resp = client.post("/api/v1/jobs", json={"title": "Python Dev"})
    job_id = create_resp.json()["jobId"]

    # Client passes concept_id for Python, but tries to send label="Fake JavaScript"
    spoof_payload = {
        "expectedRevision": 1,
        "requirements": [
            {
                "label": "Fake JavaScript",
                "taxonomySkillConceptId": str(py_skill.concept_id),
                "category": "SKILL",
                "necessity": "MUST_HAVE",
                "rationale": "Valid rationale",
            }
        ],
    }
    resp = client.put(f"/api/v1/jobs/{job_id}/draft/requirements", json=spoof_payload)
    assert resp.status_code == status.HTTP_200_OK

    req_saved = resp.json()["currentVersion"]["requirements"][0]
    # Server derived authoritative canonical label "Python"
    assert req_saved["label"] == "Python"
    assert req_saved["normalizedLabel"] == "python"
    assert req_saved["origin"] == "TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED"


def test_invalid_taxonomy_concept_rejected(client, test_db):
    _seed_user(test_db, email="recruiter.invaltax@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.invaltax@company.test")

    create_resp = client.post("/api/v1/jobs", json={"title": "App Dev"})
    job_id = create_resp.json()["jobId"]

    fake_concept_id = str(uuid.uuid4())
    payload = {
        "expectedRevision": 1,
        "requirements": [
            {
                "label": "React",
                "taxonomySkillConceptId": fake_concept_id,
                "category": "SKILL",
            }
        ],
    }
    resp = client.put(f"/api/v1/jobs/{job_id}/draft/requirements", json=payload)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert resp.json()["detail"]["code"] == "TAXONOMY_CONCEPT_NOT_ACTIVE"


# =========================================================================
# 4. Review Warnings (Missing Rationale & Sensitive Language)
# =========================================================================

def test_missing_must_have_rationale_produces_warning(client, test_db):
    _seed_user(test_db, email="recruiter.warn@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.warn@company.test")

    create_resp = client.post("/api/v1/jobs", json={"title": "ML Engineer"})
    job_id = create_resp.json()["jobId"]

    payload = {
        "expectedRevision": 1,
        "requirements": [
            {
                "label": "PyTorch",
                "category": "SKILL",
                "necessity": "MUST_HAVE",
                "priority": "HIGH",
                "rationale": None,  # Missing rationale for MUST_HAVE
            }
        ],
    }
    resp = client.put(f"/api/v1/jobs/{job_id}/draft/requirements", json=payload)
    assert resp.status_code == status.HTTP_200_OK

    v = resp.json()["currentVersion"]
    req = v["requirements"][0]
    assert req["reviewRequired"] is True
    assert len(req["warningMetadata"]) == 1
    assert req["warningMetadata"][0]["code"] == "MISSING_MUST_HAVE_RATIONALE"

    assert len(v["reviewWarnings"]) == 1
    assert v["reviewWarnings"][0]["code"] == "MISSING_MUST_HAVE_RATIONALE"


def test_sensitive_language_flagged_with_human_review_warning(client, test_db):
    _seed_user(test_db, email="recruiter.sens@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.sens@company.test")

    create_resp = client.post("/api/v1/jobs", json={"title": "Community Manager"})
    job_id = create_resp.json()["jobId"]

    payload = {
        "expectedRevision": 1,
        "requirements": [
            {
                "label": "Young and energetic team player",  # Sensitive wording: 'young', 'energetic'
                "category": "OTHER",
                "necessity": "PREFERRED",
                "rationale": "High-growth startup vibe",
            }
        ],
    }
    resp = client.put(f"/api/v1/jobs/{job_id}/draft/requirements", json=payload)
    assert resp.status_code == status.HTTP_200_OK

    req = resp.json()["currentVersion"]["requirements"][0]
    assert req["reviewRequired"] is True
    codes = [w["code"] for w in req["warningMetadata"]]
    assert "POTENTIALLY_SENSITIVE_CRITERIA" in codes


# =========================================================================
# 5. Authorization & Role Isolation
# =========================================================================

def test_reviewer_and_unauthenticated_forbidden_on_requirements(client, test_db):
    recruiter = _seed_user(test_db, email="recruiter.auth@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.auth@company.test")

    create_resp = client.post("/api/v1/jobs", json={"title": "Architect"})
    job_id = create_resp.json()["jobId"]

    # Unauthenticated PUT
    client.cookies.clear()
    unauth_resp = client.put(
        f"/api/v1/jobs/{job_id}/draft/requirements",
        json={"expectedRevision": 1, "requirements": []},
    )
    assert unauth_resp.status_code == status.HTTP_401_UNAUTHORIZED

    # Reviewer PUT -> 403 Forbidden
    _seed_user(test_db, email="reviewer.auth@company.test", role=UserRole.REVIEWER)
    _login_user(client, email="reviewer.auth@company.test")

    forb_resp = client.put(
        f"/api/v1/jobs/{job_id}/draft/requirements",
        json={"expectedRevision": 1, "requirements": []},
    )
    assert forb_resp.status_code == status.HTTP_403_FORBIDDEN


def test_cross_recruiter_access_blocked(client, test_db):
    _seed_user(test_db, email="recruiter.alpha@company.test", role=UserRole.RECRUITER)
    _seed_user(test_db, email="recruiter.beta@company.test", role=UserRole.RECRUITER)

    # Recruiter Alpha creates job
    _login_user(client, email="recruiter.alpha@company.test")
    create_resp = client.post("/api/v1/jobs", json={"title": "Alpha Project"})
    job_id = create_resp.json()["jobId"]

    # Recruiter Beta tries to PUT requirements on Alpha's job -> 404 Concealed
    _login_user(client, email="recruiter.beta@company.test")
    intrude_resp = client.put(
        f"/api/v1/jobs/{job_id}/draft/requirements",
        json={"expectedRevision": 1, "requirements": []},
    )
    assert intrude_resp.status_code == status.HTTP_404_NOT_FOUND
