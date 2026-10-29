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
    RequirementGapState,
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
    email="recruiter.api@example.com",
    password="Password123!",
    role=UserRole.RECRUITER,
):
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        display_name="Test Recruiter",
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        must_change_password=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login_user(client, email="recruiter.api@example.com", password="Password123!"):
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
        title="Backend Engineer",
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
    return job, ver, prof, run, res


def test_api_create_report_unauthenticated_rejected(client):
    """Unauthenticated access to create gap report returns 401."""
    resp = client.post(
        "/api/v1/gap-reports",
        json={"matchResultId": str(uuid.uuid4())},
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_api_create_and_retrieve_gap_report(client, test_db):
    """Recruiter can create a gap report, retrieve it, list items, and fetch canonical JSON."""
    user = _seed_user(test_db, email="recruiter.flow@company.test")
    _, _, _, _, res = _seed_environment(test_db, user.user_id)
    csrf, _ = _login_user(client, email="recruiter.flow@company.test")

    # 1. Create Report -> 201 Created
    create_resp = client.post(
        "/api/v1/gap-reports",
        headers={"Idempotency-Key": "key-api-flow-1"},
        json={"matchResultId": str(res.result_id)},
    )
    assert create_resp.status_code == status.HTTP_201_CREATED
    summary_data = create_resp.json()
    assert summary_data["reused"] is False
    assert summary_data["totalRequirements"] == 2
    assert summary_data["coveredCount"] == 1
    assert summary_data["partiallyCoveredCount"] == 1
    report_id = summary_data["reportId"]
    assert report_id is not None

    # Verify no prohibited PII or raw text in summary response
    verify_no_prohibited_keys(summary_data)
    assert "Jane Doe" not in str(summary_data)
    assert "jane@example.com" not in str(summary_data)

    # 2. Replay identical creation -> 200 OK, reused: true
    replay_resp = client.post(
        "/api/v1/gap-reports",
        headers={"Idempotency-Key": "key-api-flow-1"},
        json={"matchResultId": str(res.result_id)},
    )
    assert replay_resp.status_code == status.HTTP_200_OK
    assert replay_resp.json()["reused"] is True
    assert replay_resp.json()["reportId"] == report_id

    # 3. Retrieve Report by ID -> 200 OK
    get_resp = client.get(f"/api/v1/gap-reports/{report_id}")
    assert get_resp.status_code == status.HTTP_200_OK
    detail_data = get_resp.json()
    assert detail_data["reportId"] == report_id
    assert len(detail_data["items"]) == 2
    verify_no_prohibited_keys(detail_data)

    # 4. Retrieve Report Items -> 200 OK
    items_resp = client.get(f"/api/v1/gap-reports/{report_id}/items")
    assert items_resp.status_code == status.HTTP_200_OK
    items_data = items_resp.json()
    assert len(items_data) == 2
    assert items_data[0]["gapState"] == RequirementGapState.COVERED.value
    assert items_data[1]["gapState"] == RequirementGapState.PARTIALLY_COVERED.value
    verify_no_prohibited_keys(items_data)

    # 5. Retrieve Canonical JSON -> 200 OK
    json_resp = client.get(f"/api/v1/gap-reports/{report_id}/json")
    assert json_resp.status_code == status.HTTP_200_OK
    canonical_data = json_resp.json()
    assert canonical_data["reportId"] == report_id
    assert canonical_data["totalRequirements"] == 2
    assert "disclaimer" in canonical_data
    assert len(canonical_data["gaps"]) == 2
    verify_no_prohibited_keys(canonical_data)

    # Invariant: totalRequirements == sum(summaryCounts)
    counts = canonical_data["summaryCounts"]
    assert canonical_data["totalRequirements"] == sum(counts.values())

    # 6. List gap reports for match result -> 200 OK
    list_resp = client.get(f"/api/v1/matches/results/{res.result_id}/gap-reports")
    assert list_resp.status_code == status.HTTP_200_OK
    assert len(list_resp.json()) == 1


def test_api_cross_recruiter_concealment(client, test_db):
    """Recruiter B receives 404 concealment when attempting to create or access Recruiter A's gap report."""
    recruiter_a = _seed_user(test_db, email="recruiter.a@company.test")
    recruiter_b = _seed_user(test_db, email="recruiter.b@company.test")
    _, _, _, _, res_a = _seed_environment(test_db, recruiter_a.user_id)

    # Recruiter A creates report
    _login_user(client, email="recruiter.a@company.test")
    resp_a = client.post(
        "/api/v1/gap-reports",
        headers={"Idempotency-Key": "rec-a-key-1"},
        json={"matchResultId": str(res_a.result_id)},
    )
    assert resp_a.status_code == status.HTTP_201_CREATED
    report_id = resp_a.json()["reportId"]

    # Recruiter B logs in
    _login_user(client, email="recruiter.b@company.test")

    # 1. B attempts to create report for A's match result -> 404
    resp_b_create = client.post(
        "/api/v1/gap-reports",
        headers={"Idempotency-Key": "rec-b-key-1"},
        json={"matchResultId": str(res_a.result_id)},
    )
    assert resp_b_create.status_code == status.HTTP_404_NOT_FOUND

    # 2. B attempts to get A's report by ID -> 404
    resp_b_get = client.get(f"/api/v1/gap-reports/{report_id}")
    assert resp_b_get.status_code == status.HTTP_404_NOT_FOUND

    # 3. B attempts to get A's report items -> 404
    resp_b_items = client.get(f"/api/v1/gap-reports/{report_id}/items")
    assert resp_b_items.status_code == status.HTTP_404_NOT_FOUND

    # 4. B attempts to get A's report JSON -> 404
    resp_b_json = client.get(f"/api/v1/gap-reports/{report_id}/json")
    assert resp_b_json.status_code == status.HTTP_404_NOT_FOUND

    # 5. B lists reports for A's result -> 404
    resp_b_list = client.get(f"/api/v1/matches/results/{res_a.result_id}/gap-reports")
    assert resp_b_list.status_code == status.HTTP_404_NOT_FOUND


def test_api_idempotency_conflict_409(client, test_db):
    """Same idempotency key with different payload returns 409 conflict."""
    user = _seed_user(test_db, email="recruiter.conflict@company.test")
    _, _, _, _, res1 = _seed_environment(test_db, user.user_id)
    _, _, _, _, res2 = _seed_environment(test_db, user.user_id)
    _login_user(client, email="recruiter.conflict@company.test")

    # First call succeeds
    resp1 = client.post(
        "/api/v1/gap-reports",
        headers={"Idempotency-Key": "reused-key-conflict"},
        json={"matchResultId": str(res1.result_id)},
    )
    assert resp1.status_code == status.HTTP_201_CREATED

    # Second call with same key but different match_result_id -> 409 Conflict
    resp2 = client.post(
        "/api/v1/gap-reports",
        headers={"Idempotency-Key": "reused-key-conflict"},
        json={"matchResultId": str(res2.result_id)},
    )
    assert resp2.status_code == status.HTTP_409_CONFLICT
    assert resp2.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
