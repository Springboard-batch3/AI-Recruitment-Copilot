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
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.candidate_access import CandidateApplicationRecord, CandidateApplicationStatus
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
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.matching import verify_no_prohibited_keys
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
):
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        display_name="Test User",
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        must_change_password=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login_user(client, email="user@company.test", password="Password123!"):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == status.HTTP_200_OK
    return resp.json()["csrfToken"], resp.json()["user"]


def _seed_candidate(test_db, name="Candidate One", skills=None):
    skills = skills or ["Python"]
    art = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="resume.pdf",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    test_db.add(art)
    test_db.flush()

    ident = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=art.artifact_id,
        full_name=name,
        primary_email=f"{name.lower().replace(' ', '.')}@example.com",
        identity_payload={"fullName": {"value": name}},
    )
    test_db.add(ident)
    test_db.flush()

    prof = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=1,
        artifact_id=art.artifact_id,
        extraction_id=uuid.uuid4(),
        identity_id=ident.identity_id,
        schema_version="1.0",
        parser_pipeline_version="1.0.0",
        provenance="PARSER_EXTRACTED",
        professional_profile={
            "headline": {"value": "Developer"},
            "skills": [
                {
                    "value": s,
                    "normalizedValue": s.lower(),
                    "confidence": 0.95,
                    "confidenceBand": "HIGH",
                    "evidenceIds": [f"ev-{s.lower()}"],
                    "reviewStatus": "CONFIRMED",
                }
                for s in skills
            ],
            "employment": [],
            "education": [],
            "projects": [],
            "certifications": [],
            "languages": [],
        },
        evidence_spans=[
            {
                "evidenceId": f"ev-{s.lower()}",
                "pageNumber": 1,
                "blockNumber": idx + 1,
                "characterStart": idx * 10,
                "characterEnd": idx * 10 + len(s),
                "originalText": s,
                "extractionMethod": "DIRECT_TEXT",
            }
            for idx, s in enumerate(skills)
        ],
        review_required=False,
    )
    test_db.add(prof)
    test_db.commit()
    return prof


def _seed_job(test_db, recruiter_id, status_val=JobVersionStatus.READY_FOR_MATCHING):
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(job)
    test_db.flush()

    ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        revision=1,
        version_status=status_val,
        title="Software Engineer",
        created_by_user_id=recruiter_id,
        ready_by_user_id=recruiter_id if status_val == JobVersionStatus.READY_FOR_MATCHING else None,
        ready_at=datetime.now(timezone.utc) if status_val == JobVersionStatus.READY_FOR_MATCHING else None,
    )
    test_db.add(ver)
    test_db.flush()

    req = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        category=RequirementCategory.SKILL,
        label="Python",
        normalized_label="python",
        display_order=0,
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
        rationale="Core service in Python.",
    )
    test_db.add(req)
    test_db.commit()
    return job, ver


def _seed_application(test_db, job, ver, cand, status=CandidateApplicationStatus.SUBMITTED.value):
    app = CandidateApplicationRecord(
        application_id=uuid.uuid4(),
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        user_id=uuid.uuid4(),
        binding_id=uuid.uuid4(),
        candidate_id=cand.identity_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        job_revision=ver.revision,
        artifact_id=cand.artifact_id,
        profile_id=cand.profile_id,
        profile_version=cand.profile_version,
        status=status,
        submitted_at=datetime.now(timezone.utc),
    )
    test_db.add(app)
    test_db.commit()
    return app


def test_unauthenticated_request_rejected(client):
    """Unauthenticated access to create match run is rejected with 401."""
    resp = client.post(
        "/api/v1/matches/runs",
        json={
            "jobId": str(uuid.uuid4()),
            "expectedRevision": 1,
            "candidates": [{"profileId": str(uuid.uuid4()), "profileVersion": 1}],
            "idempotencyKey": "some-key",
        },
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_non_recruiter_role_rejected(client, test_db):
    """Users with non-recruiter/non-admin roles (e.g. REVIEWER) are denied with 403."""
    _seed_user(test_db, email="reviewer@company.test", role=UserRole.REVIEWER)
    _login_user(client, email="reviewer@company.test")

    resp = client.post(
        "/api/v1/matches/runs",
        json={
            "jobId": str(uuid.uuid4()),
            "expectedRevision": 1,
            "candidates": [{"profileId": str(uuid.uuid4()), "profileVersion": 1}],
            "idempotencyKey": "some-key",
        },
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_recruiter_owner_lifecycle_and_concealment(client, test_db):
    """Recruiter A can create/view runs for their job. Recruiter B receives 404 concealment."""
    recruiter_a = _seed_user(test_db, email="recruiter.a@company.test", role=UserRole.RECRUITER)
    recruiter_b = _seed_user(test_db, email="recruiter.b@company.test", role=UserRole.RECRUITER)
    job_a, ver_a = _seed_job(test_db, recruiter_a.user_id)
    cand = _seed_candidate(test_db, name="Candidate One")
    _seed_application(test_db, job_a, ver_a, cand)

    # Recruiter A logs in and creates match run
    _login_user(client, email="recruiter.a@company.test")
    create_resp = client.post(
        "/api/v1/matches/runs",
        headers={"Idempotency-Key": "key-run-owner-1"},
        json={
            "jobId": str(job_a.job_id),
            "expectedRevision": ver_a.revision,
            "candidates": [{"profileId": str(cand.profile_id), "profileVersion": 1}],
        },
    )
    assert create_resp.status_code == status.HTTP_201_CREATED
    run_data = create_resp.json()
    run_id = run_data["runId"]
    assert run_data["lifecycleStatus"] == "SUCCEEDED"
    assert run_data["candidateCount"] == 1
    assert run_data["reused"] is False

    # Recruiter A can retrieve the run
    get_resp = client.get(f"/api/v1/matches/runs/{run_id}")
    assert get_resp.status_code == status.HTTP_200_OK
    assert get_resp.json()["runId"] == run_id

    # Recruiter A can view results
    results_resp = client.get(f"/api/v1/matches/runs/{run_id}/results")
    assert results_resp.status_code == status.HTTP_200_OK
    results_data = results_resp.json()
    assert results_data["total"] == 1
    assert len(results_data["items"]) == 1
    item = results_data["items"][0]
    assert item["rank"] == 1
    assert "detailUrl" in item

    # Recruiter A can view detailed result
    result_id = item["resultId"]
    detail_resp = client.get(f"/api/v1/matches/runs/{run_id}/results/{result_id}")
    assert detail_resp.status_code == status.HTTP_200_OK
    assert "scoreDecomposition" in detail_resp.json()

    # Recruiter B logs in -> attempts to access Recruiter A's run -> 404 concealment
    _login_user(client, email="recruiter.b@company.test")
    get_resp_b = client.get(f"/api/v1/matches/runs/{run_id}")
    assert get_resp_b.status_code == status.HTTP_404_NOT_FOUND

    # Recruiter B attempts to view results of Recruiter A's run -> 404 concealment
    results_resp_b = client.get(f"/api/v1/matches/runs/{run_id}/results")
    assert results_resp_b.status_code == status.HTTP_404_NOT_FOUND

    # Recruiter B attempts to create run for Recruiter A's job -> 404 concealment
    create_resp_b = client.post(
        "/api/v1/matches/runs",
        headers={"Idempotency-Key": "key-run-b-illegal"},
        json={
            "jobId": str(job_a.job_id),
            "expectedRevision": ver_a.revision,
            "candidates": [{"profileId": str(cand.profile_id), "profileVersion": 1}],
        },
    )
    assert create_resp_b.status_code == status.HTTP_404_NOT_FOUND


def test_idempotent_retry_and_conflict(client, test_db):
    """Identical submission returns reused: true with 200 OK; different payload returns 409 conflict."""
    recruiter = _seed_user(test_db, email="recruiter.idem@company.test", role=UserRole.RECRUITER)
    job, ver = _seed_job(test_db, recruiter.user_id)
    cand1 = _seed_candidate(test_db, name="Candidate A")
    cand2 = _seed_candidate(test_db, name="Candidate B")
    _seed_application(test_db, job, ver, cand1)
    _seed_application(test_db, job, ver, cand2)

    _login_user(client, email="recruiter.idem@company.test")
    payload = {
        "jobId": str(job.job_id),
        "expectedRevision": ver.revision,
        "candidates": [{"profileId": str(cand1.profile_id), "profileVersion": 1}],
    }

    # 1. First submission -> 201 Created
    resp1 = client.post(
        "/api/v1/matches/runs",
        headers={"Idempotency-Key": "key-test-idem-replay"},
        json=payload,
    )
    assert resp1.status_code == status.HTTP_201_CREATED
    data1 = resp1.json()
    assert data1["reused"] is False

    # 2. Identical submission -> 200 OK, reused: true
    resp2 = client.post(
        "/api/v1/matches/runs",
        headers={"Idempotency-Key": "key-test-idem-replay"},
        json=payload,
    )
    assert resp2.status_code == status.HTTP_200_OK
    data2 = resp2.json()
    assert data2["reused"] is True
    assert data2["runId"] == data1["runId"]

    # 3. Same key, different candidate -> 409 Conflict
    conflicting_payload = {
        "jobId": str(job.job_id),
        "expectedRevision": ver.revision,
        "candidates": [{"profileId": str(cand2.profile_id), "profileVersion": 1}],
    }
    resp3 = client.post(
        "/api/v1/matches/runs",
        headers={"Idempotency-Key": "key-test-idem-replay"},
        json=conflicting_payload,
    )
    assert resp3.status_code == status.HTTP_409_CONFLICT
    assert resp3.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_draft_job_revision_rejected(client, test_db):
    """Attempting match run against a DRAFT (not READY_FOR_MATCHING) version returns 409."""
    recruiter = _seed_user(test_db, email="recruiter.draft@company.test", role=UserRole.RECRUITER)
    job, ver = _seed_job(test_db, recruiter.user_id, status_val=JobVersionStatus.DRAFT)
    cand = _seed_candidate(test_db)
    _seed_application(test_db, job, ver, cand)

    _login_user(client, email="recruiter.draft@company.test")
    resp = client.post(
        "/api/v1/matches/runs",
        headers={"Idempotency-Key": "key-draft-reject"},
        json={
            "jobId": str(job.job_id),
            "expectedRevision": ver.revision,
            "candidates": [{"profileId": str(cand.profile_id), "profileVersion": 1}],
        },
    )
    assert resp.status_code == status.HTTP_409_CONFLICT
    assert resp.json()["detail"]["code"] == "REQUISITION_REVISION_NOT_READY"


def test_duplicate_candidate_input_rejected(client, test_db):
    """Submitting duplicate candidate profile in selection returns 422 Unprocessable Content."""
    recruiter = _seed_user(test_db, email="recruiter.dup@company.test", role=UserRole.RECRUITER)
    job, ver = _seed_job(test_db, recruiter.user_id)
    cand = _seed_candidate(test_db)
    _seed_application(test_db, job, ver, cand)

    _login_user(client, email="recruiter.dup@company.test")
    resp = client.post(
        "/api/v1/matches/runs",
        headers={"Idempotency-Key": "key-dup-api"},
        json={
            "jobId": str(job.job_id),
            "expectedRevision": ver.revision,
            "candidates": [
                {"profileId": str(cand.profile_id), "profileVersion": 1},
                {"profileId": str(cand.profile_id), "profileVersion": 1},
            ],
        },
    )
    assert resp.status_code in {status.HTTP_422_UNPROCESSABLE_ENTITY, 422}


def test_response_contains_zero_identity_or_protected_data(client, test_db):
    """Ensure all API responses contain zero identity fields or protected data."""
    recruiter = _seed_user(test_db, email="recruiter.priv@company.test", role=UserRole.RECRUITER)
    job, ver = _seed_job(test_db, recruiter.user_id)
    cand = _seed_candidate(test_db, name="Secret Candidate", skills=["Python"])
    _seed_application(test_db, job, ver, cand)

    _login_user(client, email="recruiter.priv@company.test")
    resp = client.post(
        "/api/v1/matches/runs",
        headers={"Idempotency-Key": "key-priv-verify"},
        json={
            "jobId": str(job.job_id),
            "expectedRevision": ver.revision,
            "candidates": [{"profileId": str(cand.profile_id), "profileVersion": 1}],
        },
    )
    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.json()
    verify_no_prohibited_keys(data)

    results_resp = client.get(f"/api/v1/matches/runs/{data['runId']}/results")
    assert results_resp.status_code == status.HTTP_200_OK
    results_data = results_resp.json()
    verify_no_prohibited_keys(results_data)
    assert "secret candidate" not in str(results_data).lower()
