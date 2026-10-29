import csv
import io
import os
import tempfile
import uuid
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
from app.models.auth import (
    Base,
    UserRecord,
    UserRole,
)
from app.models.taxonomy import (
    OccupationAlias,
    OccupationConcept,
    OccupationSkillLink,
    SkillAlias,
    SkillConcept,
    TaxonomySnapshot,
    TaxonomySnapshotState,
)
from app.services.security import canonicalize_email, hash_password
from app.services.taxonomy_normalization import normalize_search_query


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
def synthetic_onet_dir(tmp_path):
    """Creates small synthetic CSV files matching exact O*NET 31.0 schema."""
    d = tmp_path / "synthetic_onet"
    d.mkdir()

    # occupation_data.csv
    occ_rows = [
        ["O*NET-SOC Code", "Title", "Description"],
        ["15-1252.00", "Software Developers", "Research, design, and develop computer software."],
        ["15-1254.00", "Web Developers", "Develop and design websites and web applications."],
        ["11-1011.00", "Chief Executives", "Determine and formulate policies and provide overall direction."],
    ]
    with open(d / "occupation_data.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(occ_rows)

    # essential_skills.csv
    essential_rows = [
        ["O*NET-SOC Code", "Title", "Element ID", "Element Name", "Scale ID", "Scale Name", "Data Value", "Recommend Suppress", "Not Relevant"],
        ["15-1252.00", "Software Developers", "2.A.1.a", "Reading Comprehension", "IM", "Importance", "72.0", "N", "N"],
        ["15-1252.00", "Software Developers", "2.A.1.a", "Reading Comprehension", "LV", "Level", "4.5", "N", "N"],
        ["15-1252.00", "Software Developers", "2.A.1.b", "Active Listening", "IM", "Importance", "68.0", "N", "N"],
        ["15-1252.00", "Software Developers", "2.A.1.b", "Active Listening", "LV", "Level", "4.0", "N", "N"],
        ["15-1252.00", "Software Developers", "2.A.1.e", "Critical Thinking", "IM", "Importance", "80.0", "N", "N"],
        ["15-1252.00", "Software Developers", "2.A.1.e", "Critical Thinking", "LV", "Level", "5.0", "N", "N"],
        ["11-1011.00", "Chief Executives", "2.A.1.e", "Critical Thinking", "IM", "Importance", "85.0", "N", "N"],
    ]
    with open(d / "essential_skills.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(essential_rows)

    # transferable_skills.csv
    transferable_rows = [
        ["O*NET-SOC Code", "Title", "Element ID", "Element Name", "Scale ID", "Scale Name", "Data Value", "Recommend Suppress", "Not Relevant"],
        ["15-1252.00", "Software Developers", "2.B.2.i", "Complex Problem Solving", "IM", "Importance", "78.0", "N", "N"],
        ["15-1252.00", "Software Developers", "2.B.2.i", "Complex Problem Solving", "LV", "Level", "4.8", "N", "N"],
        ["15-1254.00", "Web Developers", "2.B.2.i", "Complex Problem Solving", "IM", "Importance", "70.0", "N", "N"],
    ]
    with open(d / "transferable_skills.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(transferable_rows)

    # job_titles.csv
    title_rows = [
        ["O*NET-SOC Code", "Title", "Job Title", "Short Title", "Source(s)"],
        ["15-1252.00", "Software Developers", "Full Stack Developer", "", "08"],
        ["15-1252.00", "Software Developers", "Backend Engineer", "", "08"],
        ["15-1254.00", "Web Developers", "Frontend Engineer", "", "08"],
        ["15-1254.00", "Web Developers", "React Developer", "", "08"],
    ]
    with open(d / "job_titles.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(title_rows)

    # software_skills.csv
    skill_rows = [
        ["O*NET-SOC Code", "Title", "Workplace Example", "Element ID", "Element Name", "Hot Technology", "In Demand"],
        ["15-1252.00", "Software Developers", "Python", "2.E.1.a", "Programming language", "Y", "Y"],
        ["15-1252.00", "Software Developers", "PostgreSQL", "2.E.6.f", "Data base user interface software", "Y", "N"],
        ["15-1252.00", "Software Developers", "C++", "2.E.1.a", "Programming language", "Y", "N"],
        ["15-1252.00", "Software Developers", "C#", "2.E.1.a", "Programming language", "Y", "N"],
        ["15-1252.00", "Software Developers", "C", "2.E.1.a", "Programming language", "N", "N"],
        ["15-1254.00", "Web Developers", "React", "2.E.1.a", "Web platform development software", "Y", "Y"],
        ["15-1254.00", "Web Developers", "Node.js", "2.E.1.a", "Web platform development software", "Y", "N"],
    ]
    with open(d / "software_skills.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(skill_rows)

    return d


# =========================================================================
# 1. Normalization Tests
# =========================================================================

def test_normalization_technical_terms():
    # Technical distinctions preserved
    assert normalize_search_query("C") == "c"
    assert normalize_search_query("C++") == "c++"
    assert normalize_search_query("C#") == "c#"
    assert normalize_search_query(".NET") == ".net"
    assert normalize_search_query("ASP.NET") == "asp.net"
    assert normalize_search_query("Node.js") == "node.js"
    assert normalize_search_query("R") == "r"
    assert normalize_search_query("Go") == "go"
    assert normalize_search_query("  Python   3.11  ") == "python 3.11"
    assert normalize_search_query("PostgreSQL") == "postgresql"


# =========================================================================
# 2. Importer Tests
# =========================================================================

def test_offline_importer_success_and_idempotency(synthetic_onet_dir, test_db):
    # 1. Initial import
    res1 = import_onet_taxonomy(source_dir=synthetic_onet_dir, version="31.0", session=test_db)
    assert res1 == 0

    # Verify snapshot active
    stmt = select(TaxonomySnapshot).where(TaxonomySnapshot.state == TaxonomySnapshotState.ACTIVE)
    snap = test_db.execute(stmt).scalar_one()
    assert snap.source_version == "31.0"
    assert snap.record_counts["occupations"] == 3
    assert snap.record_counts["occupation_aliases"] == 4
    assert snap.record_counts["skills"] >= 10
    assert snap.record_counts["occupation_skill_links"] >= 10

    # 2. Idempotent re-import of same version & content
    res2 = import_onet_taxonomy(source_dir=synthetic_onet_dir, version="31.0", session=test_db)
    assert res2 == 0

    # Verify still only 1 active snapshot
    all_snaps = test_db.execute(select(TaxonomySnapshot)).scalars().all()
    assert len(all_snaps) == 1


def test_importer_missing_required_file_fails_cleanly(tmp_path, test_db):
    d = tmp_path / "incomplete_onet"
    d.mkdir()

    with open(d / "occupation_data.csv", "w", newline="") as f:
        f.write("O*NET-SOC Code,Title,Description\n")
    with open(d / "software_skills.csv", "w", newline="") as f:
        f.write("O*NET-SOC Code,Title,Workplace Example,Element ID,Element Name,Hot Technology,In Demand\n")

    # Missing essential_skills.csv and transferable_skills.csv
    res = import_onet_taxonomy(source_dir=d, version="31.0", session=test_db)
    assert res == 1

    snaps = test_db.execute(select(TaxonomySnapshot)).scalars().all()
    assert len(snaps) == 0


def test_importer_header_mismatch_fails_before_mutation(tmp_path, test_db):
    d = tmp_path / "bad_headers"
    d.mkdir()

    # Invalid header
    with open(d / "occupation_data.csv", "w", newline="") as f:
        f.write("Wrong,Header,Names\n1,2,3\n")
    with open(d / "essential_skills.csv", "w", newline="") as f:
        f.write("O*NET-SOC Code,Title,Element ID,Element Name,Scale ID,Scale Name,Data Value\n")
    with open(d / "transferable_skills.csv", "w", newline="") as f:
        f.write("O*NET-SOC Code,Title,Element ID,Element Name,Scale ID,Scale Name,Data Value\n")
    with open(d / "job_titles.csv", "w", newline="") as f:
        f.write("O*NET-SOC Code,Title,Job Title,Short Title,Source(s)\n")
    with open(d / "software_skills.csv", "w", newline="") as f:
        f.write("O*NET-SOC Code,Title,Workplace Example,Element ID,Element Name,Hot Technology,In Demand\n")

    res = import_onet_taxonomy(source_dir=d, version="31.0", session=test_db)
    assert res == 1

    # Verify no snapshots created
    snaps = test_db.execute(select(TaxonomySnapshot)).scalars().all()
    assert len(snaps) == 0


def test_importer_rejects_same_version_different_content(synthetic_onet_dir, test_db):
    res1 = import_onet_taxonomy(source_dir=synthetic_onet_dir, version="31.0", session=test_db)
    assert res1 == 0

    # Modify file content
    with open(synthetic_onet_dir / "occupation_data.csv", "a") as f:
        f.write("11-9999.00,New Role,Description\n")

    # Re-import same version with altered hash -> fails
    res2 = import_onet_taxonomy(source_dir=synthetic_onet_dir, version="31.0", session=test_db)
    assert res2 == 1


# =========================================================================
# 3. Search Occupations API Tests (GET /api/v1/taxonomy/occupations)
# =========================================================================

def test_search_occupations_deterministic_ranking(synthetic_onet_dir, client, test_db):
    import_onet_taxonomy(source_dir=synthetic_onet_dir, version="31.0", session=test_db)

    _seed_user(test_db, email="recruiter.tax@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.tax@company.test")

    # Exact alias match "Full Stack Developer" -> maps to "Software Developers"
    resp_alias = client.get("/api/v1/taxonomy/occupations?q=Full%20Stack%20Developer")
    assert resp_alias.status_code == status.HTTP_200_OK
    data_alias = resp_alias.json()
    assert len(data_alias) >= 1
    assert data_alias[0]["canonicalTitle"] == "Software Developers"
    assert data_alias[0]["matchedAlias"] == "Full Stack Developer"
    assert data_alias[0]["onetSocCode"] == "15-1252.00"
    assert data_alias[0]["entityType"] == "occupation"
    assert data_alias[0]["datasetVersion"] == "31.0"

    # Canonical match "Web Developers"
    resp_canon = client.get("/api/v1/taxonomy/occupations?q=Web%20Developers")
    assert resp_canon.status_code == status.HTTP_200_OK
    data_canon = resp_canon.json()
    assert data_canon[0]["canonicalTitle"] == "Web Developers"
    assert data_canon[0]["matchedAlias"] is None


# =========================================================================
# 4. Search Skills API Tests (Essential, Transferable, Software)
# =========================================================================

def test_search_skills_essential_transferable_software(synthetic_onet_dir, client, test_db):
    import_onet_taxonomy(source_dir=synthetic_onet_dir, version="31.0", session=test_db)

    _seed_user(test_db, email="recruiter.skills@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.skills@company.test")

    # 1. Reading Comprehension (Essential Skill) is searchable
    resp_rc = client.get("/api/v1/taxonomy/skills?q=Reading%20Comprehension")
    assert resp_rc.status_code == status.HTTP_200_OK
    data_rc = resp_rc.json()
    assert len(data_rc) >= 1
    assert data_rc[0]["canonicalLabel"] == "Reading Comprehension"
    assert data_rc[0]["sourceFamily"] == "essential"
    assert data_rc[0]["entityType"] == "skill"

    # 2. Critical Thinking (Essential Skill) is searchable
    resp_ct = client.get("/api/v1/taxonomy/skills?q=Critical%20Thinking")
    assert resp_ct.status_code == status.HTTP_200_OK
    data_ct = resp_ct.json()
    assert len(data_ct) >= 1
    assert data_ct[0]["canonicalLabel"] == "Critical Thinking"
    assert data_ct[0]["sourceFamily"] == "essential"

    # 3. Active Listening (Essential Skill) is searchable
    resp_al = client.get("/api/v1/taxonomy/skills?q=Active%20Listening")
    assert resp_al.status_code == status.HTTP_200_OK
    data_al = resp_al.json()
    assert len(data_al) >= 1
    assert data_al[0]["canonicalLabel"] == "Active Listening"
    assert data_al[0]["sourceFamily"] == "essential"

    # 4. Complex Problem Solving (Transferable Skill) is searchable
    resp_cps = client.get("/api/v1/taxonomy/skills?q=Complex%20Problem%20Solving")
    assert resp_cps.status_code == status.HTTP_200_OK
    data_cps = resp_cps.json()
    assert len(data_cps) >= 1
    assert data_cps[0]["canonicalLabel"] == "Complex Problem Solving"
    assert data_cps[0]["sourceFamily"] == "transferable"

    # 5. Software skill search "Python"
    resp_py = client.get("/api/v1/taxonomy/skills?q=Python")
    assert resp_py.status_code == status.HTTP_200_OK
    data_py = resp_py.json()
    assert data_py[0]["canonicalLabel"] == "Python"
    assert data_py[0]["sourceFamily"] == "software"

    # Controlled alias search "python3" -> maps to Python
    resp_alias = client.get("/api/v1/taxonomy/skills?q=python3")
    assert resp_alias.status_code == status.HTTP_200_OK
    data_alias = resp_alias.json()
    assert data_alias[0]["canonicalLabel"] == "Python"
    assert data_alias[0]["matchedAlias"] == "python3"

    # Technical symbol exact match "C++" vs "C" vs "C#"
    resp_cpp = client.get("/api/v1/taxonomy/skills?q=C%2B%2B")
    assert resp_cpp.status_code == status.HTTP_200_OK
    assert resp_cpp.json()[0]["canonicalLabel"] == "C++"

    resp_csharp = client.get("/api/v1/taxonomy/skills?q=C%23")
    assert resp_csharp.status_code == status.HTTP_200_OK
    assert resp_csharp.json()[0]["canonicalLabel"] == "C#"

    resp_c = client.get("/api/v1/taxonomy/skills?q=C")
    assert resp_c.status_code == status.HTTP_200_OK
    assert resp_c.json()[0]["canonicalLabel"] == "C"


# =========================================================================
# 5. Occupation Skill Suggestions API Tests
# =========================================================================

def test_occupation_skill_suggestions(synthetic_onet_dir, client, test_db):
    import_onet_taxonomy(source_dir=synthetic_onet_dir, version="31.0", session=test_db)

    _seed_user(test_db, email="recruiter.sug@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.sug@company.test")

    # Find Software Developers concept ID
    occ = test_db.execute(select(OccupationConcept).where(OccupationConcept.onet_soc_code == "15-1252.00")).scalar_one()

    resp = client.get(f"/api/v1/taxonomy/occupations/{occ.concept_id}/skills")
    assert resp.status_code == status.HTTP_200_OK
    sug_list = resp.json()

    assert len(sug_list) > 0
    labels = [s["canonical_label"] for s in sug_list]

    # Must contain skills linked to Software Developers
    assert "Reading Comprehension" in labels
    assert "Active Listening" in labels
    assert "Critical Thinking" in labels
    assert "Complex Problem Solving" in labels
    assert "Python" in labels


def test_search_authorization_and_safety(client, test_db):
    # Unauthenticated -> 401
    resp_unauth = client.get("/api/v1/taxonomy/occupations?q=Developer")
    assert resp_unauth.status_code == status.HTTP_401_UNAUTHORIZED

    # Reviewer / Candidate -> 403
    _seed_user(test_db, email="candidate.tax@company.test", role=UserRole.REVIEWER)
    _login_user(client, email="candidate.tax@company.test")

    resp_forbidden = client.get("/api/v1/taxonomy/occupations?q=Developer")
    assert resp_forbidden.status_code == status.HTTP_403_FORBIDDEN

    # Empty query -> 422
    _seed_user(test_db, email="recruiter.query@company.test", role=UserRole.RECRUITER)
    _login_user(client, email="recruiter.query@company.test")

    resp_empty = client.get("/api/v1/taxonomy/occupations?q=   ")
    assert resp_empty.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Wildcards % and _ handled safely
    resp_wildcard = client.get("/api/v1/taxonomy/occupations?q=%25_")
    assert resp_wildcard.status_code == status.HTTP_200_OK


