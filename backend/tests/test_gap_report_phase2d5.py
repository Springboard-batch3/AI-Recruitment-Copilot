import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models.auth import Base, UserRecord, UserRole
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.job import (
    JobLifecycleStatus,
    JobRequirement,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
    RequirementCategory,
    RequirementNecessity,
    RequirementOrigin,
    RequirementPriority,
)
from app.models.matching import (
    MatchResultRecord,
    MatchRunLifecycleStatus,
    MatchRunRecord,
)
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.matching import verify_no_prohibited_keys
from app.schemas.requirement_gap import (
    GAP_POLICY_VERSION,
    GAP_SCHEMA_VERSION,
    WORDING_TEMPLATE_VERSION,
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
    email="recruiter.alpha@example.com",
    password="Password123!",
    role=UserRole.RECRUITER,
):
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        display_name="Recruiter Alpha",
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        must_change_password=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login_user(client, email="recruiter.alpha@example.com", password="Password123!"):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == status.HTTP_200_OK
    return resp.json()["csrfToken"], resp.json()["user"]


def _seed_environment(db, recruiter_id):
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    db.add(job)
    db.flush()

    ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        revision=1,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        title="Senior Software Engineer",
        created_by_user_id=recruiter_id,
    )
    db.add(ver)
    db.flush()

    req1 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        display_order=0,
        category=RequirementCategory.SKILL,
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
        label="Python",
        normalized_label="python",
        rationale="Must know Python",
    )
    req2 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        display_order=1,
        category=RequirementCategory.EXPERIENCE,
        necessity=RequirementNecessity.PREFERRED,
        priority=RequirementPriority.MEDIUM,
        origin=RequirementOrigin.RECRUITER_ENTERED,
        label="36 months experience",
        normalized_label="36 months experience",
        rationale="36 months experience preferred",
    )
    db.add_all([req1, req2])
    db.flush()

    art = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="resume.pdf",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    db.add(art)
    db.flush()

    ident = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=art.artifact_id,
        full_name="Jane Doe",
        primary_email="jane@example.com",
        identity_payload={"fullName": {"value": "Jane Doe"}},
    )
    db.add(ident)
    db.flush()

    prof = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=1,
        artifact_id=art.artifact_id,
        extraction_id=uuid.uuid4(),
        identity_id=ident.identity_id,
        schema_version="1.0",
        parser_pipeline_version="1.0.0",
        provenance="PARSER_EXTRACTED",
        professional_profile={"skills": [{"name": "Python"}]},
    )
    db.add(prof)
    db.flush()

    run = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=ver.revision,
        evaluation_date=datetime.now(timezone.utc),
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="1.0.0",
        scoring_policy_version="phase2c3_scoring_v1",
        candidate_input_fingerprint="c_input_fp",
        input_fingerprint="input_fp",
        idempotency_key=f"run_{uuid.uuid4()}",
        lifecycle_status=MatchRunLifecycleStatus.SUCCEEDED,
        candidate_count=1,
    )
    db.add(run)
    db.flush()

    res = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=run.run_id,
        profile_id=prof.profile_id,
        profile_version=prof.profile_version,
        raw_compatibility_score=Decimal("83.333333"),
        display_compatibility_score=Decimal("83.33"),
        confidence_tier="HIGH",
        rank=1,
        warning_count=0,
        must_have_warning_count=0,
        score_decomposition={
            "requirement_contributions": [
                {
                    "requirement_id": str(req1.requirement_id),
                    "display_order": 0,
                    "category": req1.category.value,
                    "necessity": req1.necessity.value,
                    "priority": req1.priority.value,
                    "match_state": "MATCHED",
                    "match_value": 1.0,
                    "evidence_references": ["resume.pdf#ev1"],
                    "expected_value": "Python",
                    "observed_value": "Python",
                    "review_recommended": False,
                },
                {
                    "requirement_id": str(req2.requirement_id),
                    "display_order": 1,
                    "category": req2.category.value,
                    "necessity": req2.necessity.value,
                    "priority": req2.priority.value,
                    "match_state": "PARTIALLY_MATCHED",
                    "match_value": 0.6667,
                    "evidence_references": ["resume.pdf#ev2"],
                    "expected_value": "36 months",
                    "observed_value": "24 months",
                    "reason_codes": ["EXPERIENCE_BELOW_THRESHOLD_24_OF_36_MONTHS"],
                    "review_recommended": False,
                },
            ],
            "must_have_warnings": [],
        },
    )
    db.add(res)
    db.commit()

    return {
        "job": job,
        "version": ver,
        "requirements": [req1, req2],
        "profile": prof,
        "run": run,
        "match_result": res,
    }


def test_phase2d5_valid_creation_includes_score_and_confidence(client, test_db):
    """Phase 2D.5: Valid match result creates gap report with compatibilityScore and confidenceTier."""
    recruiter = _seed_user(test_db, "recruiter.valid@example.com")
    csrf_token, _ = _login_user(client, "recruiter.valid@example.com")
    env = _seed_environment(test_db, recruiter.user_id)
    result_id = str(env["match_result"].result_id)

    resp = client.post(
        "/api/v1/gap-reports",
        json={"match_result_id": result_id, "idempotency_key": "test-key-001"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    report_id = data["reportId"]
    assert report_id is not None
    assert data["totalRequirements"] == 2
    assert data["reused"] is False

    # Check detail endpoint for compatibility_score and confidence_tier
    detail_resp = client.get(f"/api/v1/gap-reports/{report_id}")
    assert detail_resp.status_code == status.HTTP_200_OK
    detail = detail_resp.json()
    assert detail["compatibilityScore"] == 83.33
    assert detail["confidenceTier"] == "HIGH"
    assert detail["totalRequirements"] == 2
    assert len(detail["items"]) == 2

    # Verify reconciliation sum: covered + partiallyCovered + notEvidenced + unverified + conflicting + notApplicable == totalRequirements
    sum_counts = (
        detail["coveredCount"]
        + detail["partiallyCoveredCount"]
        + detail["notEvidencedCount"]
        + detail["unverifiedCount"]
        + detail["conflictingCount"]
        + detail["notApplicableCount"]
    )
    assert sum_counts == detail["totalRequirements"]


def test_phase2d5_idempotent_creation_returns_existing_report(client, test_db):
    """Phase 2D.5: Repeated creation with same idempotency key or result returns existing report with reused=True."""
    recruiter = _seed_user(test_db, "recruiter.idem@example.com")
    csrf_token, _ = _login_user(client, "recruiter.idem@example.com")
    env = _seed_environment(test_db, recruiter.user_id)
    result_id = str(env["match_result"].result_id)

    # First call creates
    resp1 = client.post(
        "/api/v1/gap-reports",
        json={"match_result_id": result_id, "idempotency_key": "idem-key-999"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp1.status_code == status.HTTP_201_CREATED
    rep1_id = resp1.json()["reportId"]

    # Second call returns existing
    resp2 = client.post(
        "/api/v1/gap-reports",
        json={"match_result_id": result_id, "idempotency_key": "idem-key-999"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp2.status_code == status.HTTP_200_OK
    rep2_data = resp2.json()
    assert rep2_data["reportId"] == rep1_id
    assert rep2_data["reused"] is True


def test_phase2d5_cross_recruiter_access_blocked(client, test_db):
    """Phase 2D.5: Recruiter B cannot view or generate gap report for Recruiter A's match result."""
    recruiter_a = _seed_user(test_db, "recruiter.a@example.com")
    recruiter_b = _seed_user(test_db, "recruiter.b@example.com")
    env_a = _seed_environment(test_db, recruiter_a.user_id)
    result_a_id = str(env_a["match_result"].result_id)

    # Recruiter A generates report
    csrf_a, _ = _login_user(client, "recruiter.a@example.com")
    resp_a = client.post(
        "/api/v1/gap-reports",
        json={"match_result_id": result_a_id, "idempotency_key": "key-a"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert resp_a.status_code == status.HTTP_201_CREATED
    report_a_id = resp_a.json()["reportId"]

    # Login as Recruiter B
    csrf_b, _ = _login_user(client, "recruiter.b@example.com")

    # Recruiter B tries to generate report for Recruiter A's result
    resp_b_post = client.post(
        "/api/v1/gap-reports",
        json={"match_result_id": result_a_id, "idempotency_key": "key-b"},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert resp_b_post.status_code in [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND]

    # Recruiter B tries to view Recruiter A's report detail
    resp_b_get = client.get(f"/api/v1/gap-reports/{report_a_id}")
    assert resp_b_get.status_code in [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND]

    # Recruiter B tries to view Recruiter A's canonical JSON
    resp_b_json = client.get(f"/api/v1/gap-reports/{report_a_id}/json")
    assert resp_b_json.status_code in [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND]


def test_phase2d5_canonical_json_privacy_and_immutability(client, test_db):
    """Phase 2D.5: Canonical JSON export contains compatibility metrics and passes verify_no_prohibited_keys."""
    recruiter = _seed_user(test_db, "recruiter.canon@example.com")
    csrf_token, _ = _login_user(client, "recruiter.canon@example.com")
    env = _seed_environment(test_db, recruiter.user_id)
    result_id = str(env["match_result"].result_id)

    # Generate report
    resp = client.post(
        "/api/v1/gap-reports",
        json={"match_result_id": result_id, "idempotency_key": "canon-key-01"},
        headers={"X-CSRF-Token": csrf_token},
    )
    report_id = resp.json()["reportId"]

    # Fetch canonical JSON
    canon_resp = client.get(f"/api/v1/gap-reports/{report_id}/json")
    assert canon_resp.status_code == status.HTTP_200_OK
    canon_data = canon_resp.json()

    # Verify no prohibited PII or uncanonical keys
    verify_no_prohibited_keys(canon_data)
    assert canon_data["compatibilityScore"] == 83.33
    assert canon_data["confidenceTier"] == "HIGH"
    assert canon_data["reportId"] == report_id
    assert canon_data["gapSchemaVersion"] == GAP_SCHEMA_VERSION
    assert canon_data["gapPolicyVersion"] == GAP_POLICY_VERSION
    assert canon_data["wordingTemplateVersion"] == WORDING_TEMPLATE_VERSION


def test_phase2d5_match_score_and_rank_unaltered(client, test_db):
    """Phase 2D.5: Gap report creation does not alter Phase 2C match result scores or ranking."""
    recruiter = _seed_user(test_db, "recruiter.immut@example.com")
    csrf_token, _ = _login_user(client, "recruiter.immut@example.com")
    env = _seed_environment(test_db, recruiter.user_id)
    match_result = env["match_result"]
    original_score = match_result.display_compatibility_score
    original_rank = match_result.rank
    result_id = str(match_result.result_id)

    # Create gap report
    client.post(
        "/api/v1/gap-reports",
        json={"match_result_id": result_id, "idempotency_key": "immut-key-1"},
        headers={"X-CSRF-Token": csrf_token},
    )

    # Verify database match result record
    test_db.refresh(match_result)
    assert match_result.display_compatibility_score == original_score
    assert match_result.rank == original_rank
