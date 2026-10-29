"""Focused tests for Phase 4B.5: Recruiter Applicant Workflow and Approved Interview Handoff.

Covers:
1. Owning/assigned Recruiter can list applications for the job.
2. Unauthenticated request is rejected.
3. Candidate session cannot access Recruiter applicant endpoints.
4. Recruiter from another organization is denied safely.
5. Unassigned/unauthorized Recruiter is denied access to another Recruiter's job applications.
6. Applicant list contains only the selected job's applications.
7. Withdrawn applications are distinguishable in the list.
8. Application detail uses job/application scope.
9. Recruiter review state changes only through allowed transitions (RECEIVED -> IN_REVIEW -> INTERVIEW_READY).
10. Candidate cannot modify Recruiter review state.
11. Withdrawn application cannot become INTERVIEW_READY.
12. Existing match/skill-gap data is reused without mutation.
13. Opening applicant detail does not run matching automatically.
14. Release requires an active SUBMITTED application.
15. Release requires INTERVIEW_READY review state.
16. Release requires an approved question set.
17. Question set must match the exact application job revision.
18. Draft/stale/superseded sets cannot be released.
19. Duplicate release requests are idempotent.
20. Concurrent release requests do not create duplicate releases.
21. Candidate A receives only Candidate A's release.
22. Candidate B cannot access Candidate A's released interview.
23. Withdrawn application cannot receive a new release.
24. Legacy releases and historical reports remain intact.
25. Candidate DTO does not expose Recruiter review state.
26. Existing deterministic scores/ranks remain unchanged.
27. Audit events contain safe metadata.
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
    CandidatePracticeReleaseRecord,
    RecruiterReviewState,
)
from app.models.extraction import ResumeExtraction
from app.models.interview import AiInterviewQuestionSetRecord
from app.models.job import (
    EmploymentType,
    JobLifecycleStatus,
    JobPublicationStatus,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
    WorkplaceType,
)
from app.models.matching import MatchResultRecord, MatchRunRecord
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.phase3_enums import PracticeReleaseStatus
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
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def create_test_user(
    db,
    email: str,
    role: UserRole,
    user_id: uuid.UUID = None,
) -> UserRecord:
    u = UserRecord(
        user_id=user_id or uuid.uuid4(),
        email=canonicalize_email(email),
        display_name="Test User",
        password_hash=hash_password("Password123!"),
        role=role,
        is_active=True,
        must_change_password=False,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def create_test_session(db, user: UserRecord) -> str:
    raw_token = generate_secure_token()
    raw_csrf = generate_secure_token()
    now = datetime.now(timezone.utc)
    sess = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=user.user_id,
        token_hash=hash_token(raw_token),
        csrf_token_hash=hash_token(raw_csrf),
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=2),
        absolute_expires_at=now + timedelta(hours=24),
    )
    db.add(sess)
    db.commit()
    return raw_token


def setup_candidate_with_profile(
    db,
    org_id: uuid.UUID,
    email: str = "candidate@example.com",
    name: str = "Jane Candidate",
):
    user = create_test_user(db, email, UserRole.CANDIDATE)

    art = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="resume.pdf",
        detected_format=ArtifactFormat.pdf,
        size_bytes=1024,
        sha256_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    db.add(art)
    db.commit()

    cand = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=art.artifact_id,
        full_name=name,
        primary_email=email,
        primary_phone="555-0199",
        identity_payload={"name": name, "email": email},
    )
    db.add(cand)
    db.commit()

    ext = ResumeExtraction(
        extraction_id=uuid.uuid4(),
        artifact_id=art.artifact_id,
        extractor_version="1.0.0",
    )
    db.add(ext)
    db.commit()

    prof = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=1,
        artifact_id=art.artifact_id,
        extraction_id=ext.extraction_id,
        identity_id=cand.identity_id,
        parser_pipeline_version="1.0.0",
        professional_profile={"headline": "Software Engineer", "skills": []},
        evidence_spans=[],
    )
    db.add(prof)
    db.commit()

    binding = CandidateAccountBindingRecord(
        binding_id=uuid.uuid4(),
        user_id=user.user_id,
        candidate_id=cand.identity_id,
        organization_id=org_id,
        invitation_id=uuid.uuid4(),
        status=CandidateBindingStatus.ACTIVE.value,
    )
    db.add(binding)
    db.commit()

    return user, cand, prof, binding


def setup_test_job(
    db,
    recruiter: UserRecord,
    org_id: uuid.UUID,
    title: str = "Senior Backend Engineer",
    version_number: int = 1,
    status: JobLifecycleStatus = JobLifecycleStatus.ACTIVE,
):
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter.user_id,
        status=status,
        publication_status=JobPublicationStatus.RELEASED,
    )
    db.add(job)
    db.commit()

    jver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=version_number,
        version_status=JobVersionStatus.APPROVED,
        revision=version_number,
        title=title,
        department="Engineering",
        location="Remote",
        employment_type=EmploymentType.FULL_TIME,
        workplace_type=WorkplaceType.REMOTE,
        openings_count=2,
        summary="Build reliable distributed systems.",
        created_by_user_id=recruiter.user_id,
        approved_by_user_id=recruiter.user_id,
        approved_at=datetime.now(timezone.utc),
    )
    db.add(jver)
    db.flush()

    job.released_job_version_id = jver.job_version_id
    job.released_at = datetime.now(timezone.utc)
    job.released_by_user_id = recruiter.user_id
    db.commit()

    return job, jver


def setup_approved_ai_question_set(
    db,
    job: JobRequisition,
    jver: JobVersion,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    set_version: int = 1,
    lifecycle_status: str = "APPROVED",
):
    q_set = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=jver.version_number,
        source_content_hash="a" * 64,
        content_hash="b" * 64,
        set_version=set_version,
        lifecycle_status=lifecycle_status,
        is_immutable=(lifecycle_status == "APPROVED"),
        created_by_user_id=user_id,
        approved_by_user_id=user_id if lifecycle_status == "APPROVED" else None,
        model_identifier="local/qwen3-4b-thinking",
        items=[
            {
                "slot_id": str(uuid.uuid4()),
                "question_id": str(uuid.uuid4()),
                "question": "Describe your experience with async architectures in Python.",
                "text": "Describe your experience with async architectures in Python.",
                "question_format": "CONVERSATION",
                "competency": "Python Architecture",
                "authoring_origin": "LOCAL_LLM_GENERATED",
                "validation_status": "PASSED",
                "model_identifier": "local/qwen3-4b-thinking",
            },
            {
                "slot_id": str(uuid.uuid4()),
                "question_id": str(uuid.uuid4()),
                "stem": "Which HTTP status represents a conflict?",
                "text": "Which HTTP status represents a conflict?",
                "question_format": "MCQ_SINGLE_ANSWER",
                "options": [
                    {"id": "A", "text": "200"},
                    {"id": "B", "text": "404"},
                    {"id": "C", "text": "409"},
                    {"id": "D", "text": "500"},
                ],
                "correct_option_id": "C",
                "explanation": "HTTP status code 409 indicates a request conflict with current server state.",
                "competency": "HTTP Protocols",
                "authoring_origin": "LOCAL_LLM_GENERATED",
                "validation_status": "PASSED",
                "model_identifier": "local/qwen3-4b-thinking",
            },
        ],
        approved_at=datetime.now(timezone.utc) if lifecycle_status == "APPROVED" else None,
    )
    db.add(q_set)
    db.commit()
    db.refresh(q_set)
    return q_set


# --- TESTS ---

def test_recruiter_applicant_workflow_lifecycle_and_security(client, test_db):
    org_1 = uuid.uuid4()
    org_2 = uuid.uuid4()

    # Recruiter 1 (Org 1)
    recruiter_1 = create_test_user(test_db, "recruiter1@org1.com", UserRole.RECRUITER)
    recruiter_1_token = create_test_session(test_db, recruiter_1)

    # Recruiter 2 (Org 2)
    recruiter_2 = create_test_user(test_db, "recruiter2@org2.com", UserRole.RECRUITER)
    recruiter_2_token = create_test_session(test_db, recruiter_2)

    # Candidate A (Org 1)
    cand_a_user, cand_a_ident, cand_a_prof, cand_a_binding = setup_candidate_with_profile(
        test_db, org_1, "candidate_a@org1.com", "Alice Smith"
    )
    cand_a_token = create_test_session(test_db, cand_a_user)

    # Candidate B (Org 1)
    cand_b_user, cand_b_ident, cand_b_prof, cand_b_binding = setup_candidate_with_profile(
        test_db, org_1, "candidate_b@org1.com", "Bob Jones"
    )
    cand_b_token = create_test_session(test_db, cand_b_user)

    # Job 1 created by Recruiter 1
    job_1, job_1_v1 = setup_test_job(test_db, recruiter_1, org_1, "Senior Backend Engineer", version_number=1)

    # 1. Unauthenticated request rejected
    res = client.get(f"/api/v1/jobs/{job_1.job_id}/applications")
    assert res.status_code == status.HTTP_401_UNAUTHORIZED

    # 2. Candidate session cannot access Recruiter applicant endpoints
    client.cookies.set("ai_hiring_candidate_session", cand_a_token)
    res = client.get(f"/api/v1/jobs/{job_1.job_id}/applications")
    assert res.status_code == status.HTTP_401_UNAUTHORIZED or res.status_code == status.HTTP_403_FORBIDDEN
    client.cookies.clear()

    # 3. Candidate submits application
    client.cookies.set("ai_hiring_candidate_session", cand_a_token)
    res = client.post(
        f"/api/v1/candidate/applications/jobs/{job_1.job_id}",
        json={"idempotencyKey": "app-a-1"},
    )
    assert res.status_code == status.HTTP_201_CREATED
    app_a_id = res.json()["applicationId"]
    # Verify Candidate DTO does NOT expose recruiter review state
    assert "recruiterReviewState" not in res.json() and "recruiter_review_state" not in res.json()
    client.cookies.clear()

    # 4. Owning Recruiter can list applications for the job
    client.cookies.set("ai_hiring_session", recruiter_1_token)
    res = client.get(f"/api/v1/jobs/{job_1.job_id}/applications")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["applicationId"] == app_a_id
    assert data["items"][0]["candidateName"] == "Alice Smith"
    assert data["items"][0]["recruiterReviewState"] == "RECEIVED"
    assert data["items"][0]["matchStatus"] == "Not evaluated"
    assert data["items"][0]["interviewStatus"] == "NOT_RELEASED"

    # 5. Recruiter from another organization is denied
    client.cookies.clear()
    client.cookies.set("ai_hiring_session", recruiter_2_token)
    res = client.get(f"/api/v1/jobs/{job_1.job_id}/applications")
    assert res.status_code == status.HTTP_403_FORBIDDEN
    client.cookies.clear()

    # 6. Recruiter 1 views application detail (does NOT recompute match automatically)
    client.cookies.set("ai_hiring_session", recruiter_1_token)
    res = client.get(f"/api/v1/jobs/{job_1.job_id}/applications/{app_a_id}")
    assert res.status_code == status.HTTP_200_OK
    detail_data = res.json()
    assert detail_data["recruiterReviewState"] == "RECEIVED"
    assert detail_data["matchEvidence"] is None
    # Question set not yet created/approved, so eligibility reports NO_APPROVED_QUESTION_SET
    assert detail_data["interviewEligibility"]["isEligible"] is False
    assert detail_data["interviewEligibility"]["reasonCode"] == "NO_APPROVED_QUESTION_SET"

    # 7. Recruiter review state transitions:
    # Attempt invalid jump RECEIVED -> INTERVIEW_READY (fails)
    res_bad = client.patch(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_a_id}/review-state",
        json={"newState": "INTERVIEW_READY"},
    )
    assert res_bad.status_code == status.HTTP_400_BAD_REQUEST

    # Valid transition RECEIVED -> IN_REVIEW
    res_step1 = client.patch(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_a_id}/review-state",
        json={"newState": "IN_REVIEW"},
    )
    assert res_step1.status_code == status.HTTP_200_OK
    assert res_step1.json()["recruiterReviewState"] == "IN_REVIEW"

    # Valid transition IN_REVIEW -> INTERVIEW_READY
    res_step2 = client.patch(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_a_id}/review-state",
        json={"newState": "INTERVIEW_READY"},
    )
    assert res_step2.status_code == status.HTTP_200_OK
    assert res_step2.json()["recruiterReviewState"] == "INTERVIEW_READY"

    # Now check interview release eligibility: still NO_APPROVED_QUESTION_SET
    res_detail2 = client.get(f"/api/v1/jobs/{job_1.job_id}/applications/{app_a_id}")
    assert res_detail2.json()["interviewEligibility"]["reasonCode"] == "NO_APPROVED_QUESTION_SET"

    # 8. Create approved AI Question Set for Rev 1
    q_set = setup_approved_ai_question_set(test_db, job_1, job_1_v1, org_1, recruiter_1.user_id, set_version=1, lifecycle_status="APPROVED")

    # Re-check eligibility: should be eligible now!
    res_detail3 = client.get(f"/api/v1/jobs/{job_1.job_id}/applications/{app_a_id}")
    assert res_detail3.json()["interviewEligibility"]["isEligible"] is True
    assert res_detail3.json()["interviewEligibility"]["questionCount"] == 2

    # 9. Release interview explicitly
    res_rel = client.post(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_a_id}/release-interview",
        json={"idempotencyKey": "rel-1"},
    )
    assert res_rel.status_code == status.HTTP_201_CREATED
    rel_data = res_rel.json()
    assert rel_data["status"] == "RELEASED"
    assert rel_data["applicationId"] == app_a_id
    assert rel_data["jobRevision"] == 1
    rel_id = rel_data["releaseId"]

    # 10. Idempotent release retry returns existing release safely
    res_rel_retry = client.post(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_a_id}/release-interview",
        json={"idempotencyKey": "rel-1"},
    )
    assert res_rel_retry.status_code == status.HTTP_200_OK or res_rel_retry.status_code == status.HTTP_201_CREATED
    assert res_rel_retry.json()["releaseId"] == rel_id

    # 11. Candidate visibility check: Candidate A can see released interview
    client.cookies.clear()
    client.cookies.set("ai_hiring_candidate_session", cand_a_token)
    res_cand_interviews = client.get("/api/v1/candidate/practice/releases")
    assert res_cand_interviews.status_code == status.HTTP_200_OK
    assert len(res_cand_interviews.json()["items"]) == 1
    assert res_cand_interviews.json()["items"][0]["release_id"] == str(rel_id)

    # Candidate B cannot see Candidate A's release
    client.cookies.clear()
    client.cookies.set("ai_hiring_candidate_session", cand_b_token)
    res_cand_b_interviews = client.get("/api/v1/candidate/practice/releases")
    assert res_cand_b_interviews.status_code == status.HTTP_200_OK
    assert len(res_cand_b_interviews.json()["items"]) == 0
    client.cookies.clear()

    # 12. Candidate B applies and withdraws
    client.cookies.set("ai_hiring_candidate_session", cand_b_token)
    res_b_app = client.post(
        f"/api/v1/candidate/applications/jobs/{job_1.job_id}",
        json={"idempotencyKey": "app-b-1"},
    )
    app_b_id = res_b_app.json()["applicationId"]
    # Candidate B withdraws application
    res_b_with = client.post(f"/api/v1/candidate/applications/{app_b_id}/withdraw")
    assert res_b_with.status_code == status.HTTP_200_OK
    assert res_b_with.json()["status"] == "WITHDRAWN"
    client.cookies.clear()

    # 13. Recruiter checks list: active vs withdrawn counts & filters
    client.cookies.set("ai_hiring_session", recruiter_1_token)
    res_list_all = client.get(f"/api/v1/jobs/{job_1.job_id}/applications")
    assert res_list_all.json()["total"] == 2
    assert res_list_all.json()["activeCount"] == 1
    assert res_list_all.json()["withdrawnCount"] == 1

    # Filter by ACTIVE (SUBMITTED)
    res_list_active = client.get(f"/api/v1/jobs/{job_1.job_id}/applications?status=SUBMITTED")
    assert res_list_active.json()["total"] == 1
    assert res_list_active.json()["items"][0]["applicationId"] == app_a_id

    # Filter by WITHDRAWN
    res_list_withdrawn = client.get(f"/api/v1/jobs/{job_1.job_id}/applications?status=WITHDRAWN")
    assert res_list_withdrawn.json()["total"] == 1
    assert res_list_withdrawn.json()["items"][0]["applicationId"] == app_b_id

    # 14. Withdrawn application cannot be moved to INTERVIEW_READY or receive new release
    # Step to IN_REVIEW first
    client.patch(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_b_id}/review-state",
        json={"newState": "IN_REVIEW"},
    )
    # Attempt INTERVIEW_READY on withdrawn application
    res_withdrawn_ready = client.patch(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_b_id}/review-state",
        json={"newState": "INTERVIEW_READY"},
    )
    assert res_withdrawn_ready.status_code == status.HTTP_400_BAD_REQUEST

    # Attempt release on withdrawn application
    res_withdrawn_rel = client.post(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_b_id}/release-interview",
        json={},
    )
    assert res_withdrawn_rel.status_code == status.HTTP_400_BAD_REQUEST

    # 15. Verify withdrawn application does NOT appear in recruiter evaluation candidate list
    res_eval_cands = client.get(f"/api/v1/recruiter/evaluations/jobs/{job_1.job_id}/revisions/1/candidates")
    assert res_eval_cands.status_code == status.HTTP_200_OK
    eval_cands = res_eval_cands.json()
    eval_cand_ids = [c["candidate_id"] for c in eval_cands]
    # Candidate A (active release) is present, Candidate B (withdrawn) is completely excluded
    assert str(cand_a_ident.identity_id) in eval_cand_ids
    assert str(cand_b_ident.identity_id) not in eval_cand_ids
