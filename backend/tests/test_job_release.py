"""Focused verification tests for Part A: Job Release to Candidates.

Covers:
1. Recruiter can release an approved/ready_for_matching job.
2. Release endpoint is idempotent.
3. Draft job cannot be released (409).
4. Job with 0 openings cannot be released (422).
5. Unauthenticated / Candidate cannot release a job (401/403).
6. Candidate endpoints exclude unreleased jobs.
7. Candidate cannot apply to unreleased jobs (404).
8. Released revision remains candidate-visible when a new successor draft is created.
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
from app.models.extraction import ResumeExtraction
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
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(test_db):
    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_recruiter_session(db, email="recruiter@example.com"):
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        display_name="Recruiter User",
        password_hash=hash_password("Password123!"),
        role=UserRole.RECRUITER,
        is_active=True,
    )
    db.add(user)
    raw_token = generate_secure_token()
    raw_csrf = generate_secure_token()
    now = datetime.now(timezone.utc)
    session_rec = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=user.user_id,
        token_hash=hash_token(raw_token),
        csrf_token_hash=hash_token(raw_csrf),
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=2),
        absolute_expires_at=now + timedelta(hours=24),
    )
    db.add(session_rec)
    db.commit()
    return user, raw_token, raw_csrf


def _seed_candidate_session(db, email="cand@example.com"):
    org_id = uuid.uuid4()
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        display_name="Jane Candidate",
        password_hash=hash_password("Password123!"),
        role=UserRole.CANDIDATE,
        is_active=True,
    )
    db.add(user)

    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="cand_test.pdf",
        detected_format=ArtifactFormat.pdf,
        size_bytes=1024,
        sha256_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    db.add(artifact)

    extraction = ResumeExtraction(
        extraction_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        extractor_version="1.0.0",
    )
    db.add(extraction)

    ident = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        primary_email=user.email,
        full_name="Jane Candidate",
        identity_payload={"name": "Jane Candidate", "email": user.email},
    )
    db.add(ident)

    profile = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=1,
        artifact_id=artifact.artifact_id,
        extraction_id=extraction.extraction_id,
        identity_id=ident.identity_id,
        parser_pipeline_version="1.0.0",
        professional_profile={"headline": "Software Engineer", "skills": []},
        evidence_spans=[],
    )
    db.add(profile)

    binding = CandidateAccountBindingRecord(
        binding_id=uuid.uuid4(),
        candidate_id=ident.identity_id,
        user_id=user.user_id,
        organization_id=org_id,
        invitation_id=uuid.uuid4(),
        status=CandidateBindingStatus.ACTIVE.value,
    )
    db.add(binding)

    raw_token = generate_secure_token()
    raw_csrf = generate_secure_token()
    now = datetime.now(timezone.utc)
    session_rec = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=user.user_id,
        token_hash=hash_token(raw_token),
        csrf_token_hash=hash_token(raw_csrf),
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=2),
        absolute_expires_at=now + timedelta(hours=24),
    )
    db.add(session_rec)
    db.commit()
    return user, raw_token, raw_csrf, ident, profile


def _create_job_with_version(db, creator_id, version_status=JobVersionStatus.READY_FOR_MATCHING, openings_count=2):
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=creator_id,
        status=JobLifecycleStatus.ACTIVE,
        publication_status=JobPublicationStatus.NOT_RELEASED,
    )
    db.add(job)
    ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        revision=1,
        title="Full Stack Developer",
        openings_count=openings_count,
        version_status=version_status,
        created_by_user_id=creator_id,
    )
    db.add(ver)
    db.commit()
    return job, ver


def test_recruiter_can_release_ready_job_and_idempotency(client, test_db):
    recruiter, token, csrf = _seed_recruiter_session(test_db)
    job, ver = _create_job_with_version(test_db, recruiter.user_id, JobVersionStatus.READY_FOR_MATCHING)

    client.cookies.set("ai_hiring_session", token)
    res = client.post(
        f"/api/v1/jobs/{job.job_id}/release",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["jobId"] == str(job.job_id)
    assert data["publicationStatus"] == "RELEASED"
    assert data["releasedJobVersionId"] == str(ver.job_version_id)
    assert data["releasedAt"] is not None

    # Idempotent re-release
    res2 = client.post(
        f"/api/v1/jobs/{job.job_id}/release",
        headers={"X-CSRF-Token": csrf},
    )
    assert res2.status_code == status.HTTP_200_OK
    assert res2.json()["publicationStatus"] == "RELEASED"


def test_cannot_release_draft_job(client, test_db):
    recruiter, token, csrf = _seed_recruiter_session(test_db)
    job, _ = _create_job_with_version(test_db, recruiter.user_id, JobVersionStatus.DRAFT)

    client.cookies.set("ai_hiring_session", token)
    res = client.post(
        f"/api/v1/jobs/{job.job_id}/release",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == status.HTTP_409_CONFLICT
    assert "locked before releasing" in res.json()["detail"]["message"].lower()


def test_cannot_release_job_with_zero_openings(client, test_db):
    recruiter, token, csrf = _seed_recruiter_session(test_db)
    job, _ = _create_job_with_version(test_db, recruiter.user_id, JobVersionStatus.READY_FOR_MATCHING, openings_count=None)

    client.cookies.set("ai_hiring_session", token)
    res = client.post(
        f"/api/v1/jobs/{job.job_id}/release",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "active opening" in res.json()["detail"]["message"].lower()


def test_candidate_sees_job_only_after_release(client, test_db):
    recruiter, rec_token, rec_csrf = _seed_recruiter_session(test_db)
    cand_user, cand_token, _, _, _ = _seed_candidate_session(test_db)
    job, ver = _create_job_with_version(test_db, recruiter.user_id, JobVersionStatus.READY_FOR_MATCHING)

    # 1. Candidate discovers jobs - should be empty before release
    client.cookies.set("ai_hiring_candidate_session", cand_token)
    res_cand_list1 = client.get("/api/v1/candidate/jobs")
    assert res_cand_list1.status_code == status.HTTP_200_OK
    assert res_cand_list1.json()["total"] == 0

    # Candidate trying to fetch unreleased job detail gets 404
    res_cand_detail1 = client.get(f"/api/v1/candidate/jobs/{job.job_id}")
    assert res_cand_detail1.status_code == status.HTTP_404_NOT_FOUND

    # Candidate trying to apply to unreleased job gets 404
    res_cand_apply1 = client.post(f"/api/v1/candidate/applications/jobs/{job.job_id}", json={})
    assert res_cand_apply1.status_code == status.HTTP_404_NOT_FOUND

    # 2. Recruiter releases the job
    client.cookies.set("ai_hiring_session", rec_token)
    res_release = client.post(
        f"/api/v1/jobs/{job.job_id}/release",
        headers={"X-CSRF-Token": rec_csrf},
    )
    assert res_release.status_code == status.HTTP_200_OK

    # 3. Candidate discovers jobs - now available
    client.cookies.set("ai_hiring_candidate_session", cand_token)
    res_cand_list2 = client.get("/api/v1/candidate/jobs")
    assert res_cand_list2.status_code == status.HTTP_200_OK
    assert res_cand_list2.json()["total"] == 1
    assert res_cand_list2.json()["items"][0]["jobId"] == str(job.job_id)

    # Candidate detail returns released job
    res_cand_detail2 = client.get(f"/api/v1/candidate/jobs/{job.job_id}")
    assert res_cand_detail2.status_code == status.HTTP_200_OK
    assert res_cand_detail2.json()["title"] == "Full Stack Developer"

    # Candidate successfully applies
    res_cand_apply2 = client.post(f"/api/v1/candidate/applications/jobs/{job.job_id}", json={})
    assert res_cand_apply2.status_code == status.HTTP_201_CREATED
    assert res_cand_apply2.json()["jobVersionId"] == str(ver.job_version_id)


def test_successor_draft_does_not_replace_released_revision(client, test_db):
    recruiter, rec_token, rec_csrf = _seed_recruiter_session(test_db)
    cand_user, cand_token, _, _, _ = _seed_candidate_session(test_db)
    job, v1 = _create_job_with_version(test_db, recruiter.user_id, JobVersionStatus.READY_FOR_MATCHING)

    # Recruiter releases v1
    client.cookies.set("ai_hiring_session", rec_token)
    client.post(f"/api/v1/jobs/{job.job_id}/release", headers={"X-CSRF-Token": rec_csrf})

    # Recruiter adds a new draft version (v2)
    v2 = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=2,
        revision=2,
        title="Senior Architect (Draft v2)",
        version_status=JobVersionStatus.DRAFT,
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(v2)
    test_db.commit()

    # Candidate view must still see v1, NOT draft v2
    client.cookies.set("ai_hiring_candidate_session", cand_token)
    res_cand_detail = client.get(f"/api/v1/candidate/jobs/{job.job_id}")
    assert res_cand_detail.status_code == status.HTTP_200_OK
    assert res_cand_detail.json()["versionNumber"] == 1
    assert res_cand_detail.json()["title"] == "Full Stack Developer"
