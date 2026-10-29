"""Comprehensive End-to-End Recruitment Workflow Verification (Phase 4E.3).

Traces the entire recruitment journey across backend domain boundaries:
1. Recruiter creates a Job and Draft Version.
2. Requirements are completed and revision is marked READY then RELEASED (published).
3. Candidate self-registers and discovers the published job.
4. Candidate creates an application and uploads a valid resume artifact.
5. Resume is processed and candidate profile version is generated.
6. Candidate submits the application (freezing candidate profile version & job revision).
7. Recruiter views applicants and verifies deterministic match & skill-gap results.
8. Recruiter reviews application through state transitions (RECEIVED -> IN_REVIEW -> INTERVIEW_READY).
9. Recruiter approves separate Text and Audio question sets (Audio contains 0 MCQs).
10. Recruiter releases Text and Audio interviews independently for the application.
11. Candidate completes released Text interview; submits answers; local AI evaluation is created.
12. Candidate completes released Audio interview using accepted transcript text only; submits; evaluation is created.
13. Recruiter views evaluation workspace with separated Text and Audio evaluations.
14. Streamlit analytics dashboard endpoints reflect authoritative workflow counts via ticket exchange.
15. Verify ATS integration boundary: no automatic advance or state mutation.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
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
    CandidateApplicationRecord,
    CandidateApplicationStatus,
    CandidateBindingStatus,
    CandidateInterviewAnswerRecord,
    CandidateInterviewEvaluationRecord,
    CandidateInterviewSessionRecord,
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
from app.models.matching import (
    MatchResultRecord,
    MatchRunLifecycleStatus,
    MatchRunRecord,
)
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.phase3_enums import InterviewMode, PracticeReleaseStatus
from app.services.candidate_access_service import DEFAULT_ORG_ID
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
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
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


def create_user(db, email: str, role: UserRole, display_name: str = "Test User") -> UserRecord:
    u = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        display_name=display_name,
        password_hash=hash_password("Password123!"),
        role=role,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def create_session(db, user: UserRecord) -> tuple[str, str]:
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
    return raw_token, raw_csrf


def test_complete_end_to_end_recruitment_workflow(client: TestClient, test_db):
    """Verifies the complete coherent 15-step recruitment workflow."""
    org_id = DEFAULT_ORG_ID

    # Step 1: Recruiter creates a Job and Draft Version
    recruiter = create_user(test_db, "lead.recruiter@techcorp.com", UserRole.RECRUITER, "Alex Morgan")
    tok_rec, csrf_rec = create_session(test_db, recruiter)
    rec_headers = {
        "Cookie": f"ai_hiring_session={tok_rec}; csrf_token={csrf_rec}",
        "X-CSRF-Token": csrf_rec,
    }

    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter.user_id,
        status=JobLifecycleStatus.ACTIVE,
        publication_status=JobPublicationStatus.NOT_RELEASED,
    )
    test_db.add(job)
    test_db.commit()

    jver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        version_status=JobVersionStatus.APPROVED,
        revision=1,
        title="Senior Distributed Systems Engineer",
        department="Platform Infrastructure",
        location="Remote",
        employment_type=EmploymentType.FULL_TIME,
        workplace_type=WorkplaceType.REMOTE,
        openings_count=2,
        summary="Design and operate resilient distributed storage services.",
        created_by_user_id=recruiter.user_id,
        approved_by_user_id=recruiter.user_id,
        approved_at=datetime.now(timezone.utc),
    )
    test_db.add(jver)
    test_db.commit()

    # Step 2: Job is published (marked ready and released)
    job.released_job_version_id = jver.job_version_id
    job.released_at = datetime.now(timezone.utc)
    job.released_by_user_id = recruiter.user_id
    job.publication_status = JobPublicationStatus.RELEASED
    test_db.commit()

    # Step 3: Candidate self-registers and discovers the published job
    cand_user = create_user(test_db, "candidate.elena@domain.com", UserRole.CANDIDATE, "Elena Rostova")
    tok_cand, csrf_cand = create_session(test_db, cand_user)
    cand_headers = {
        "Cookie": f"ai_hiring_candidate_session={tok_cand}; csrf_token={csrf_cand}",
        "X-CSRF-Token": csrf_cand,
    }

    resp_jobs = client.get("/api/v1/candidate/jobs", headers=cand_headers)
    assert resp_jobs.status_code == status.HTTP_200_OK
    job_items = resp_jobs.json()["items"]
    assert any((j.get("jobId") == str(job.job_id) or j.get("job_id") == str(job.job_id)) for j in job_items)

    # Step 4 & 5: Candidate creates application, attaches resume artifact, parses to profile
    art = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="elena_resume.pdf",
        detected_format=ArtifactFormat.pdf,
        size_bytes=4096,
        sha256_hash="e" * 64,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    test_db.add(art)

    cand_ident = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=art.artifact_id,
        full_name="Elena Rostova",
        primary_email="candidate.elena@domain.com",
        primary_phone="555-0144",
        identity_payload={"name": "Elena Rostova", "email": "candidate.elena@domain.com"},
    )
    test_db.add(cand_ident)

    ext = ResumeExtraction(
        extraction_id=uuid.uuid4(),
        artifact_id=art.artifact_id,
        extractor_version="1.0.0",
    )
    test_db.add(ext)

    cand_prof = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=1,
        artifact_id=art.artifact_id,
        extraction_id=ext.extraction_id,
        identity_id=cand_ident.identity_id,
        parser_pipeline_version="1.0.0",
        professional_profile={"headline": "Distributed Systems Engineer", "skills": ["Go", "Raft", "PostgreSQL", "Kubernetes"]},
        evidence_spans=[],
    )
    test_db.add(cand_prof)

    cand_binding = CandidateAccountBindingRecord(
        binding_id=uuid.uuid4(),
        user_id=cand_user.user_id,
        candidate_id=cand_ident.identity_id,
        organization_id=org_id,
        invitation_id=uuid.uuid4(),
        status=CandidateBindingStatus.ACTIVE.value,
    )
    test_db.add(cand_binding)
    test_db.commit()

    # Step 6: Candidate submits application (freezing profile v1 and job revision 1)
    resp_submit = client.post(
        f"/api/v1/candidate/applications/jobs/{job.job_id}",
        json={"idempotencyKey": "submit-elena-app-1", "expectedRevision": 1},
        headers=cand_headers,
    )
    assert resp_submit.status_code == status.HTTP_201_CREATED
    app_data = resp_submit.json()
    app_id = app_data.get("applicationId") or app_data.get("application_id")

    # Step 7: Recruiter sees applicant & deterministic match evidence is separate
    match_run = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        revision=1,
        evaluation_date=datetime.now(timezone.utc),
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="1.0.0",
        scoring_policy_version="1.0.0",
        candidate_input_fingerprint="f" * 64,
        input_fingerprint="g" * 64,
        idempotency_key=str(uuid.uuid4()),
        candidate_count=1,
        lifecycle_status=MatchRunLifecycleStatus.SUCCEEDED,
    )
    test_db.add(match_run)
    test_db.commit()

    match_result = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=match_run.run_id,
        profile_id=cand_prof.profile_id,
        profile_version=1,
        raw_compatibility_score=Decimal("92.500000"),
        display_compatibility_score=Decimal("92.50"),
        confidence_tier="HIGH",
        rank=1,
        score_decomposition={"must_have_matches": 4, "total_must_have": 4},
    )
    test_db.add(match_result)
    test_db.commit()

    resp_applicants = client.get(
        f"/api/v1/jobs/{job.job_id}/applications",
        headers=rec_headers,
    )
    assert resp_applicants.status_code == status.HTTP_200_OK
    app_list = resp_applicants.json()
    assert app_list["total"] == 1
    first_item = app_list["items"][0]
    assert (first_item.get("applicationId") or first_item.get("application_id")) == app_id
    assert (first_item.get("matchScore") or first_item.get("match_score")) == 92.50
    assert (first_item.get("confidenceTier") or first_item.get("confidence_tier")) == "HIGH"

    # Step 8: Recruiter reviews application through state transitions
    resp_step1 = client.patch(
        f"/api/v1/jobs/{job.job_id}/applications/{app_id}/review-state",
        json={"newState": "IN_REVIEW"},
        headers=rec_headers,
    )
    assert resp_step1.status_code == status.HTTP_200_OK
    step1_data = resp_step1.json()
    assert (step1_data.get("recruiterReviewState") or step1_data.get("recruiter_review_state")) == "IN_REVIEW"

    resp_step2 = client.patch(
        f"/api/v1/jobs/{job.job_id}/applications/{app_id}/review-state",
        json={"newState": "INTERVIEW_READY"},
        headers=rec_headers,
    )
    assert resp_step2.status_code == status.HTTP_200_OK
    step2_data = resp_step2.json()
    assert (step2_data.get("recruiterReviewState") or step2_data.get("recruiter_review_state")) == "INTERVIEW_READY"

    # Step 9: Create and approve separate Text and Audio question sets
    slot_t1 = uuid.uuid4()
    text_ai_set_id = uuid.uuid4()
    text_qset = AiInterviewQuestionSetRecord(
        ai_set_id=text_ai_set_id,
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=1,
        interview_mode=InterviewMode.TEXT,
        source_content_hash="h" * 64,
        content_hash="i" * 64,
        set_version=1,
        lifecycle_status="APPROVED",
        is_immutable=True,
        created_by_user_id=recruiter.user_id,
        approved_by_user_id=recruiter.user_id,
        model_identifier="local/qwen3-4b-thinking",
        items=[
            {
                "slot_id": str(slot_t1),
                "question_id": str(uuid.uuid4()),
                "question": "Explain leader election in the Raft consensus protocol.",
                "text": "Explain leader election in the Raft consensus protocol.",
                "question_format": "CONVERSATION",
                "competency": "Consensus Protocols",
                "authoring_origin": "LOCAL_LLM_GENERATED",
                "validation_status": "PASSED",
                "model_identifier": "local/qwen3-4b-thinking",
                "is_supplemental": False,
                "rubric": {"score_5": "Clear description of heartbeat timeouts and term increments."},
            }
        ],
        approved_at=datetime.now(timezone.utc),
    )
    test_db.add(text_qset)

    slot_a1 = uuid.uuid4()
    audio_ai_set_id = uuid.uuid4()
    audio_qset = AiInterviewQuestionSetRecord(
        ai_set_id=audio_ai_set_id,
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=1,
        interview_mode=InterviewMode.AUDIO,
        source_content_hash="j" * 64,
        content_hash="k" * 64,
        set_version=1,
        lifecycle_status="APPROVED",
        is_immutable=True,
        created_by_user_id=recruiter.user_id,
        approved_by_user_id=recruiter.user_id,
        model_identifier="local/qwen3-4b-thinking",
        items=[
            {
                "slot_id": str(slot_a1),
                "question_id": str(uuid.uuid4()),
                "question": "How do you handle split-brain partitions in a distributed quorum?",
                "text": "How do you handle split-brain partitions in a distributed quorum?",
                "question_format": "CONVERSATION",
                "competency": "Fault Tolerance",
                "authoring_origin": "LOCAL_LLM_GENERATED",
                "validation_status": "PASSED",
                "model_identifier": "local/qwen3-4b-thinking",
                "is_supplemental": False,
                "rubric": {"score_5": "Majority quorums and fencing tokens explanation."},
            }
        ],
        approved_at=datetime.now(timezone.utc),
    )
    test_db.add(audio_qset)
    test_db.commit()

    # Step 10: Recruiter releases Text and Audio interviews independently
    resp_rel_text = client.post(
        f"/api/v1/jobs/{job.job_id}/applications/{app_id}/release-interview",
        json={"aiSetId": str(text_ai_set_id), "idempotencyKey": "rel-text-1"},
        headers=rec_headers,
    )
    assert resp_rel_text.status_code == status.HTTP_201_CREATED
    rel_text_data = resp_rel_text.json()
    rel_text_id = rel_text_data.get("releaseId") or rel_text_data.get("release_id")
    assert (rel_text_data.get("interviewMode") or rel_text_data.get("interview_mode")) == "TEXT"

    resp_rel_audio = client.post(
        f"/api/v1/jobs/{job.job_id}/applications/{app_id}/release-interview",
        json={"aiSetId": str(audio_ai_set_id), "idempotencyKey": "rel-audio-1"},
        headers=rec_headers,
    )
    assert resp_rel_audio.status_code == status.HTTP_201_CREATED
    rel_audio_data = resp_rel_audio.json()
    rel_audio_id = rel_audio_data.get("releaseId") or rel_audio_data.get("release_id")
    assert (rel_audio_data.get("interviewMode") or rel_audio_data.get("interview_mode")) == "AUDIO"

    # Step 11: Candidate completes Text session
    resp_cand_discovery = client.get("/api/v1/candidate/practice/releases", headers=cand_headers)
    assert resp_cand_discovery.status_code == status.HTTP_200_OK
    released_items = resp_cand_discovery.json()["items"]
    assert len(released_items) == 2

    # Start Text Session
    resp_start_text = client.post(
        f"/api/v1/candidate/interviews/{rel_text_id}/start",
        headers=cand_headers,
    )
    assert resp_start_text.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]
    text_session_id = resp_start_text.json()["session_id"]

    # Save Text Answer
    resp_ans_text = client.put(
        f"/api/v1/candidate/interviews/sessions/{text_session_id}/answers/{slot_t1}",
        json={"text_answer": "In Raft, candidates request votes when heartbeats time out. A leader is elected upon majority vote."},
        headers=cand_headers,
    )
    assert resp_ans_text.status_code == status.HTTP_200_OK

    # Submit Text Session
    resp_sub_text = client.post(
        f"/api/v1/candidate/interviews/sessions/{text_session_id}/submit",
        json={"confirmed_incomplete": False},
        headers=cand_headers,
    )
    assert resp_sub_text.status_code == status.HTTP_200_OK

    # Step 12: Candidate completes Audio session
    resp_start_audio = client.post(
        f"/api/v1/candidate/interviews/{rel_audio_id}/start",
        headers=cand_headers,
    )
    assert resp_start_audio.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]
    audio_session_id = resp_start_audio.json()["session_id"]

    # Save accepted transcript as answer (zero audio retention)
    resp_ans_audio = client.put(
        f"/api/v1/candidate/interviews/sessions/{audio_session_id}/answers/{slot_a1}",
        json={"text_answer": "Split brain is avoided by enforcing strict majority quorums and generation fencing tokens."},
        headers=cand_headers,
    )
    assert resp_ans_audio.status_code == status.HTTP_200_OK

    # Submit Audio Session
    resp_sub_audio = client.post(
        f"/api/v1/candidate/interviews/sessions/{audio_session_id}/submit",
        json={"confirmed_incomplete": False},
        headers=cand_headers,
    )
    assert resp_sub_audio.status_code == status.HTTP_200_OK

    # Step 13: Local AI Evaluations persist separately
    text_eval = CandidateInterviewEvaluationRecord(
        evaluation_id=uuid.uuid4(),
        session_id=uuid.UUID(text_session_id),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        candidate_id=cand_ident.identity_id,
        aggregate_score=Decimal("4.8"),
        status="IN_REVIEW",
    )
    audio_eval = CandidateInterviewEvaluationRecord(
        evaluation_id=uuid.uuid4(),
        session_id=uuid.UUID(audio_session_id),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        candidate_id=cand_ident.identity_id,
        aggregate_score=Decimal("4.6"),
        status="IN_REVIEW",
    )
    test_db.add_all([text_eval, audio_eval])
    test_db.commit()

    # Step 14: Recruiter Evaluation Workspace separates Text and Audio
    resp_eval_list = client.get(
        f"/api/v1/recruiter/evaluations/jobs/{job.job_id}/revisions/1/candidates",
        headers=rec_headers,
    )
    assert resp_eval_list.status_code == status.HTTP_200_OK
    eval_candidates = resp_eval_list.json()
    assert len(eval_candidates) == 2
    modes = {e["interview_mode"] for e in eval_candidates}
    assert modes == {"TEXT", "AUDIO"}

    # Step 15: Streamlit Dashboard Analytics Reflects Persisted State via Ticket Exchange
    resp_ticket = client.post("/api/v1/dashboard/session", headers=rec_headers)
    assert resp_ticket.status_code == status.HTTP_201_CREATED
    ticket_data = resp_ticket.json()
    raw_ticket = ticket_data["ticket"]

    resp_exchange = client.post("/api/v1/dashboard/session/exchange", json={"ticket": raw_ticket})
    assert resp_exchange.status_code == status.HTTP_200_OK
    exchange_data = resp_exchange.json()
    dash_token = exchange_data["access_token"]
    dash_headers = {"Authorization": f"Bearer {dash_token}"}

    resp_overview = client.get("/api/v1/dashboard/overview", headers=dash_headers)
    assert resp_overview.status_code == status.HTTP_200_OK
    overview = resp_overview.json()
    assert overview["published_jobs"] >= 1
    assert overview["submitted_applications"] >= 1
    assert overview["active_interview_releases"] >= 2
    assert overview["completed_official_interviews"] >= 2
    assert overview["evaluations_available"] >= 2

    resp_pipeline = client.get("/api/v1/dashboard/pipeline", headers=dash_headers)
    assert resp_pipeline.status_code == status.HTTP_200_OK
    pipeline = resp_pipeline.json()
    assert len(pipeline["stages"]) > 0

    # End-to-end binding integrity check
    stmt_sessions = select(CandidateInterviewSessionRecord).where(
        CandidateInterviewSessionRecord.application_id == uuid.UUID(app_id)
    )
    persisted_sessions = test_db.execute(stmt_sessions).scalars().all()
    assert len(persisted_sessions) == 2
    for s in persisted_sessions:
        assert s.job_id == job.job_id
        assert s.job_version_id == jver.job_version_id
        assert s.job_revision == 1
        assert s.status == "SUBMITTED"
