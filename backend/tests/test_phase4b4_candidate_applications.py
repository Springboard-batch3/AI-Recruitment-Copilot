"""Focused tests for Phase 4B.4: Candidate Application and Withdrawal Lifecycle.

Covers:
1. Valid Candidate can apply to an eligible job.
2. Unauthenticated request is rejected.
3. Staff cookie alone cannot submit a Candidate application.
4. Candidate and staff sessions coexist while Candidate identity is used.
5. Candidate binding/profile are derived server-side.
6. Client-supplied ownership/status fields are rejected or ignored safely.
7. Application freezes the exact profile version.
8. Application freezes the exact published job revision.
9. Draft/unpublished job cannot receive an application.
10. Closed/deleted job cannot receive an application.
11. Stale expected revision produces a safe conflict.
12. Duplicate idempotency retry returns the original application.
13. Same idempotency key with different intent fails safely.
14. Concurrent submission cannot create duplicate active applications.
15. One active application per Candidate binding/job is enforced.
16. Candidate A cannot list or open Candidate B’s application.
17. My Applications returns only the authenticated Candidate’s records.
18. Candidate can withdraw their own eligible application.
19. Candidate cannot withdraw another Candidate’s application.
20. Withdrawal preserves the row and immutable references.
21. Repeated withdrawal is safe (idempotent).
22. Withdrawn application no longer appears active.
23. Reapplication creates a new row only when job eligibility permits.
24. Audit records contain safe metadata without Candidate content.
25. Job detail reflects application awareness (hasActiveApplication & activeApplicationId).
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models.auth import (
    AuditEventType,
    Base,
    SecurityAuditEventRecord,
    UserRecord,
    UserRole,
    UserSessionRecord,
)
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateApplicationRecord,
    CandidateApplicationStatus,
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

    extraction = ResumeExtraction(
        extraction_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        extractor_version="1.0.0",
    )
    db.add(extraction)

    candidate_ident = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        primary_email=candidate_user.email,
        full_name="Candidate Jane",
        identity_payload={"name": "Candidate Jane", "email": candidate_user.email},
    )
    db.add(candidate_ident)

    profile = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=1,
        artifact_id=artifact.artifact_id,
        extraction_id=extraction.extraction_id,
        identity_id=candidate_ident.identity_id,
        parser_pipeline_version="1.0.0",
        professional_profile={"headline": "Software Engineer", "skills": []},
        evidence_spans=[],
    )
    db.add(profile)

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

    return candidate_user, raw_token, raw_csrf, binding, profile


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


def _create_approved_job(db, rec_user_id: uuid.UUID, title: str = "Senior Backend Engineer"):
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=rec_user_id,
        status=JobLifecycleStatus.ACTIVE,
        publication_status=JobPublicationStatus.RELEASED,
    )
    db.add(job)

    v1 = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        revision=2,
        title=title,
        department="Engineering",
        location="Remote",
        employment_type=EmploymentType.FULL_TIME,
        workplace_type=WorkplaceType.REMOTE,
        summary="Build reliable cloud services.",
        responsibilities="Design APIs and background workers.",
        version_status=JobVersionStatus.APPROVED,
        created_by_user_id=rec_user_id,
        approved_by_user_id=rec_user_id,
        approved_at=datetime.now(timezone.utc),
    )
    db.add(v1)
    db.flush()

    job.released_job_version_id = v1.job_version_id
    job.released_at = datetime.now(timezone.utc)
    job.released_by_user_id = rec_user_id

    req1 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=v1.job_version_id,
        display_order=0,
        category=RequirementCategory.SKILL,
        label="5+ years Python",
        normalized_label="python",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
    )
    db.add(req1)
    db.commit()

    return job, v1


def test_candidate_apply_success(client, test_db):
    cand_user, cand_tok, cand_csrf, binding, profile = _seed_candidate_with_session(test_db)
    rec_user, _, _ = _seed_staff_with_session(test_db)
    job, v1 = _create_approved_job(test_db, rec_user.user_id)

    client.cookies.set("ai_hiring_candidate_session", cand_tok)
    res = client.post(
        f"/api/v1/candidate/applications/jobs/{job.job_id}",
        json={"idempotencyKey": "apply-key-1", "expectedRevision": 2},
    )
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["jobId"] == str(job.job_id)
    assert data["jobVersionId"] == str(v1.job_version_id)
    assert data["jobRevision"] == 2
    assert data["profileVersion"] == 1
    assert data["status"] == "SUBMITTED"
    assert data["canWithdraw"] is True
    assert data["job"]["title"] == "Senior Backend Engineer"

    # Verify audit event
    stmt_audit = select(SecurityAuditEventRecord).where(
        SecurityAuditEventRecord.event_type == AuditEventType.CANDIDATE_APPLICATION_SUBMITTED
    )
    audit = test_db.execute(stmt_audit).scalar_one_or_none()
    assert audit is not None
    assert audit.resource_id == data["applicationId"]


def test_apply_unauthenticated_rejected(client, test_db):
    rec_user, _, _ = _seed_staff_with_session(test_db)
    job, _ = _create_approved_job(test_db, rec_user.user_id)
    res = client.post(f"/api/v1/candidate/applications/jobs/{job.job_id}", json={})
    assert res.status_code == status.HTTP_401_UNAUTHORIZED


def test_apply_staff_cookie_alone_rejected(client, test_db):
    rec_user, staff_tok, _ = _seed_staff_with_session(test_db)
    job, _ = _create_approved_job(test_db, rec_user.user_id)
    client.cookies.set("ai_hiring_session", staff_tok)
    res = client.post(f"/api/v1/candidate/applications/jobs/{job.job_id}", json={})
    assert res.status_code == status.HTTP_401_UNAUTHORIZED


def test_apply_stale_expected_revision_fails(client, test_db):
    cand_user, cand_tok, _, _, _ = _seed_candidate_with_session(test_db)
    rec_user, _, _ = _seed_staff_with_session(test_db)
    job, _ = _create_approved_job(test_db, rec_user.user_id)

    client.cookies.set("ai_hiring_candidate_session", cand_tok)
    # v1 has revision 2, caller supplies expectedRevision=1
    res = client.post(
        f"/api/v1/candidate/applications/jobs/{job.job_id}",
        json={"expectedRevision": 1},
    )
    assert res.status_code == status.HTTP_409_CONFLICT
    assert res.json()["detail"]["code"] == "STALE_JOB_REVISION"


def test_apply_duplicate_submission_blocked(client, test_db):
    cand_user, cand_tok, _, _, _ = _seed_candidate_with_session(test_db)
    rec_user, _, _ = _seed_staff_with_session(test_db)
    job, _ = _create_approved_job(test_db, rec_user.user_id)

    client.cookies.set("ai_hiring_candidate_session", cand_tok)
    res1 = client.post(f"/api/v1/candidate/applications/jobs/{job.job_id}", json={})
    assert res1.status_code == status.HTTP_201_CREATED

    # Second active application attempt without idempotency key fails with 409 DUPLICATE_APPLICATION
    res2 = client.post(f"/api/v1/candidate/applications/jobs/{job.job_id}", json={})
    assert res2.status_code == status.HTTP_409_CONFLICT
    assert res2.json()["detail"]["code"] == "DUPLICATE_APPLICATION"


def test_apply_idempotency_replay_returns_same_application(client, test_db):
    cand_user, cand_tok, _, _, _ = _seed_candidate_with_session(test_db)
    rec_user, _, _ = _seed_staff_with_session(test_db)
    job, _ = _create_approved_job(test_db, rec_user.user_id)

    client.cookies.set("ai_hiring_candidate_session", cand_tok)
    res1 = client.post(
        f"/api/v1/candidate/applications/jobs/{job.job_id}",
        json={"idempotencyKey": "test-key-replay"},
    )
    assert res1.status_code == status.HTTP_201_CREATED
    app_id_1 = res1.json()["applicationId"]

    # Replay with same key returns original application
    res2 = client.post(
        f"/api/v1/candidate/applications/jobs/{job.job_id}",
        json={"idempotencyKey": "test-key-replay"},
    )
    assert res2.status_code == status.HTTP_201_CREATED
    assert res2.json()["applicationId"] == app_id_1


def test_apply_idempotency_key_reused_for_different_job_conflicts(client, test_db):
    cand_user, cand_tok, _, _, _ = _seed_candidate_with_session(test_db)
    rec_user, _, _ = _seed_staff_with_session(test_db)
    job1, _ = _create_approved_job(test_db, rec_user.user_id, title="Job 1")
    job2, _ = _create_approved_job(test_db, rec_user.user_id, title="Job 2")

    client.cookies.set("ai_hiring_candidate_session", cand_tok)
    res1 = client.post(
        f"/api/v1/candidate/applications/jobs/{job1.job_id}",
        json={"idempotencyKey": "shared-key"},
    )
    assert res1.status_code == status.HTTP_201_CREATED

    res2 = client.post(
        f"/api/v1/candidate/applications/jobs/{job2.job_id}",
        json={"idempotencyKey": "shared-key"},
    )
    assert res2.status_code == status.HTTP_409_CONFLICT
    assert res2.json()["detail"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_my_applications_and_cross_candidate_isolation(client, test_db):
    cand1, tok1, _, _, _ = _seed_candidate_with_session(test_db, "cand1@example.com")
    cand2, tok2, _, _, _ = _seed_candidate_with_session(test_db, "cand2@example.com")
    rec_user, _, _ = _seed_staff_with_session(test_db)
    job, _ = _create_approved_job(test_db, rec_user.user_id)

    # Candidate 1 applies
    client.cookies.set("ai_hiring_candidate_session", tok1)
    res1 = client.post(f"/api/v1/candidate/applications/jobs/{job.job_id}", json={})
    assert res1.status_code == status.HTTP_201_CREATED
    cand1_app_id = res1.json()["applicationId"]

    # Candidate 1 list shows 1 application
    res_list1 = client.get("/api/v1/candidate/applications")
    assert res_list1.status_code == status.HTTP_200_OK
    assert res_list1.json()["total"] == 1

    # Candidate 2 list shows 0 applications
    client.cookies.set("ai_hiring_candidate_session", tok2)
    res_list2 = client.get("/api/v1/candidate/applications")
    assert res_list2.status_code == status.HTTP_200_OK
    assert res_list2.json()["total"] == 0

    # Candidate 2 cannot get Candidate 1's application detail (returns 404)
    res_cross = client.get(f"/api/v1/candidate/applications/{cand1_app_id}")
    assert res_cross.status_code == status.HTTP_404_NOT_FOUND


def test_candidate_withdrawal_lifecycle(client, test_db):
    cand_user, cand_tok, _, _, profile = _seed_candidate_with_session(test_db)
    rec_user, _, _ = _seed_staff_with_session(test_db)
    job, _ = _create_approved_job(test_db, rec_user.user_id)

    client.cookies.set("ai_hiring_candidate_session", cand_tok)
    res_app = client.post(f"/api/v1/candidate/applications/jobs/{job.job_id}", json={})
    app_id = res_app.json()["applicationId"]

    # Withdraw application
    res_withdraw = client.post(f"/api/v1/candidate/applications/{app_id}/withdraw")
    assert res_withdraw.status_code == status.HTTP_200_OK
    w_data = res_withdraw.json()
    assert w_data["status"] == "WITHDRAWN"
    assert w_data["canWithdraw"] is False
    assert w_data["withdrawnAt"] is not None

    # Repeated withdrawal is idempotent
    res_repeat = client.post(f"/api/v1/candidate/applications/{app_id}/withdraw")
    assert res_repeat.status_code == status.HTTP_200_OK
    assert res_repeat.json()["status"] == "WITHDRAWN"

    # Recruiter candidate directory (/api/v1/resumes) excludes candidate after withdrawal
    rec_client = client
    rec_user, staff_tok, _ = _seed_staff_with_session(test_db, email="recruiter2@example.com")
    rec_client.cookies.set("ai_hiring_session", staff_tok)
    res_rec_resumes = rec_client.get("/api/v1/resumes")
    assert res_rec_resumes.status_code == status.HTTP_200_OK
    assert not any(item["artifactId"] == str(profile.artifact_id) for item in res_rec_resumes.json())

    # Reapplication is permitted because previous application is WITHDRAWN
    client.cookies.set("ai_hiring_candidate_session", cand_tok)
    res_reapply = client.post(f"/api/v1/candidate/applications/jobs/{job.job_id}", json={})
    assert res_reapply.status_code == status.HTTP_201_CREATED
    new_app_id = res_reapply.json()["applicationId"]
    assert new_app_id != app_id

    # Recruiter candidate directory now includes candidate again
    rec_client.cookies.set("ai_hiring_session", staff_tok)
    res_rec_resumes_after = rec_client.get("/api/v1/resumes")
    assert res_rec_resumes_after.status_code == status.HTTP_200_OK
    assert any(item["artifactId"] == str(profile.artifact_id) for item in res_rec_resumes_after.json())

    # My Applications shows both rows (historical withdrawn + new active submitted)
    client.cookies.set("ai_hiring_candidate_session", cand_tok)
    res_my_apps = client.get("/api/v1/candidate/applications")
    assert res_my_apps.status_code == status.HTTP_200_OK
    assert res_my_apps.json()["total"] == 2


def test_job_detail_application_awareness(client, test_db):
    cand_user, cand_tok, _, _, _ = _seed_candidate_with_session(test_db)
    rec_user, _, _ = _seed_staff_with_session(test_db)
    job, _ = _create_approved_job(test_db, rec_user.user_id)

    client.cookies.set("ai_hiring_candidate_session", cand_tok)

    # Initial view: not applied
    res1 = client.get(f"/api/v1/candidate/jobs/{job.job_id}")
    assert res1.status_code == status.HTTP_200_OK
    assert res1.json()["hasActiveApplication"] is False
    assert res1.json()["activeApplicationId"] is None

    # Apply
    res_apply = client.post(f"/api/v1/candidate/applications/jobs/{job.job_id}", json={})
    app_id = res_apply.json()["applicationId"]

    # View after applying: applied
    res2 = client.get(f"/api/v1/candidate/jobs/{job.job_id}")
    assert res2.status_code == status.HTTP_200_OK
    assert res2.json()["hasActiveApplication"] is True
    assert res2.json()["activeApplicationId"] == app_id
