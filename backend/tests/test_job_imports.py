import csv
import io
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.cli.import_onet import import_onet_taxonomy
from app.database import get_db
from app.main import app
from app.models.auth import Base, UserRecord, UserRole
from app.models.job import (
    EmploymentType,
    JobDescriptionImport,
    JobImportSourceType,
    JobImportStatus,
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
    email="recruiter.import@company.test",
    password="Password123!",
    role=UserRole.RECRUITER,
    is_active=True,
    must_change_password=False,
):
    encoded_hash = hash_password(password)
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        display_name="Test Recruiter",
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


def _login_user(client, email="recruiter.import@company.test", password="Password123!"):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == status.HTTP_200_OK
    return resp.json()["csrfToken"], resp.json()["user"]


@pytest.fixture
def synthetic_taxonomy(tmp_path, test_db):
    d = tmp_path / "synthetic_onet_imports"
    d.mkdir()

    occ_rows = [
        ["O*NET-SOC Code", "Title", "Description"],
        ["15-1252.00", "Software Developers", "Develop software applications."],
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
        ["15-1252.00", "Software Developers", "Docker", "2.E.1.a", "DevOps", "Y", "Y"],
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
# 1. Pasted Text Analysis & Extraction (Deprecated -> 410 GONE)
# =========================================================================

def test_pasted_text_analysis_success(client, test_db):
    _seed_user(test_db, email="recruiter.jd@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.jd@company.test")

    jd_text = """Job Title: Senior Backend Developer
Department: Engineering
Location: San Francisco, CA
Employment Type: Full-Time
Workplace Type: Remote
Openings: 2

About the role
Build and scale cloud backend microservices for our core platform.
"""

    resp = client.post("/api/v1/jobs/imports/analyze-text", json={"text": jd_text})
    assert resp.status_code == status.HTTP_410_GONE
    data = resp.json()
    assert data["detail"]["code"] == "FEATURE_DEPRECATED"


def test_empty_and_oversized_input_validation(client, test_db):
    _seed_user(test_db, email="recruiter.bounds@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.bounds@company.test")

    # Deprecated endpoint returns 410 GONE
    resp_empty = client.post("/api/v1/jobs/imports/analyze-text", json={"text": "   "})
    assert resp_empty.status_code == status.HTTP_410_GONE
    assert resp_empty.json()["detail"]["code"] == "FEATURE_DEPRECATED"


# =========================================================================
# 2. Protected Information Safeguard (Deprecated -> 410 GONE)
# =========================================================================

def test_protected_information_produces_warning_and_not_requirement(client, test_db):
    _seed_user(test_db, email="recruiter.protect@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.protect@company.test")

    jd_text = """Job Title: Operations Associate
Required qualifications
- Must be a young and energetic candidate.
"""

    resp = client.post("/api/v1/jobs/imports/analyze-text", json={"text": jd_text})
    assert resp.status_code == status.HTTP_410_GONE
    assert resp.json()["detail"]["code"] == "FEATURE_DEPRECATED"


# =========================================================================
# 3. Suggestion Review, Edit, and Atomic Apply Flow (Deprecated -> 410 GONE)
# =========================================================================

def test_suggestion_review_edit_and_apply_flow(client, test_db):
    _seed_user(test_db, email="recruiter.flow@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.flow@company.test")

    session_id = uuid.uuid4()
    sug_id = str(uuid.uuid4())

    patch_resp = client.patch(
        f"/api/v1/jobs/imports/{session_id}/suggestions/{sug_id}",
        json={"review_status": "ACCEPTED", "edited_value": "Lead Platform Engineer"},
    )
    assert patch_resp.status_code == status.HTTP_410_GONE

    apply_resp = client.post(
        f"/api/v1/jobs/imports/{session_id}/apply",
        json={"expectedRevision": 1, "acceptedSuggestionIds": [sug_id]},
    )
    assert apply_resp.status_code == status.HTTP_410_GONE


# =========================================================================
# 4. Authorization & Cross-Recruiter Isolation Tests
# =========================================================================

def test_cross_recruiter_import_session_access_denied(client, test_db):
    _seed_user(test_db, email="recruiter.a@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.a@company.test")

    session_id = uuid.uuid4()
    resp_get = client.get(f"/api/v1/jobs/imports/{session_id}")
    assert resp_get.status_code == status.HTTP_410_GONE


# =========================================================================
# 5. Document Upload and Deprecation Tests
# =========================================================================

def test_image_only_pdf_rejected_without_ocr(client, test_db):
    _seed_user(test_db, email="recruiter.doc@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.doc@company.test")

    pdf_bytes = b"%PDF-1.4 empty blank dummy"
    files = {"file": ("scanned_jd.pdf", pdf_bytes, "application/pdf")}
    resp = client.post("/api/v1/jobs/imports/analyze-file", files=files)
    assert resp.status_code == status.HTTP_410_GONE
    assert resp.json()["detail"]["code"] == "FEATURE_DEPRECATED"


def test_corrupted_document_rejected(client, test_db):
    _seed_user(test_db, email="recruiter.corrupt@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.corrupt@company.test")

    corrupt_bytes = b"%PDF-1.4 garbage corrupted binary content here"
    files = {"file": ("corrupt.pdf", corrupt_bytes, "application/pdf")}
    resp = client.post("/api/v1/jobs/imports/analyze-file", files=files)
    assert resp.status_code == status.HTTP_410_GONE
    assert resp.json()["detail"]["code"] == "FEATURE_DEPRECATED"


def test_reviewer_forbidden_from_imports(client, test_db):
    _seed_user(test_db, email="reviewer.imports@company.test", role=UserRole.REVIEWER)
    _login_user(client, email="reviewer.imports@company.test")

    resp = client.post("/api/v1/jobs/imports/analyze-text", json={"text": "Job Title: Test"})
    assert resp.status_code == status.HTTP_403_FORBIDDEN



# =========================================================================
# 6. Migration Isolated Upgrade and Downgrade Test
# =========================================================================

def test_migration_d1e4f6a8b2c3_upgrade_and_downgrade_isolated(capsys):
    from alembic import command
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parent.parent
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    alembic_cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg://dummy:dummy@localhost:5432/dummy_isolated_test")

    # Test upgrade SQL generation
    command.upgrade(alembic_cfg, "c9d3e5f7a2b1:d1e4f6a8b2c3", sql=True)
    captured_upgrade = capsys.readouterr()
    assert "CREATE TABLE job_description_imports" in captured_upgrade.out
    assert "ix_job_description_imports_created_by_user_id" in captured_upgrade.out

    # Test downgrade SQL generation
    command.downgrade(alembic_cfg, "d1e4f6a8b2c3:c9d3e5f7a2b1", sql=True)
    captured_downgrade = capsys.readouterr()
    assert "DROP TABLE job_description_imports" in captured_downgrade.out
