"""Focused backend tests for Phase 4C.2: Candidate Interview Workspace and Durable Answer Persistence.

Covers:
1. Candidate can start an active owned assignment.
2. Repeated start returns the same in-progress session.
3. Candidate cannot start or read another Candidate's assignment/session.
4. Question response excludes rubrics, correct MCQ answers, explanations, and recruiter notes.
5. Text answer saves and updates without duplicate rows.
6. Invalid question or MCQ option is rejected.
7. Refresh fetch returns previously saved answers and correct progress.
8. Final submission locks answers.
9. Repeated final submission is idempotent.
10. Saving after submission is rejected.
11. Incomplete submission requires explicit confirmation.
12. Starting, saving, or submitting never mutates match, application, or job data.
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
from app.models.matching import MatchAuditEventType, MatchResultRecord, MatchRunLifecycleStatus, MatchRunRecord
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.phase3_enums import PracticeReleaseStatus
from app.services.candidate_interview_service import _compute_snapshot_hash
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


def create_test_user(db, email: str, role: UserRole) -> UserRecord:
    clean_email = canonicalize_email(email)
    pwd_hash = hash_password("Password123!")
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=clean_email,
        display_name="Test User",
        password_hash=pwd_hash,
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_test_session(db, user: UserRecord) -> tuple[str, str]:
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


def setup_candidate_with_profile(db, org_id: uuid.UUID, email: str, name: str):
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
        professional_profile={"headline": "Software Engineer", "skills": ["Python", "FastAPI", "PostgreSQL"]},
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
):
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter.user_id,
        status=JobLifecycleStatus.ACTIVE,
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
    slot_ids: list,
):
    q_items = [
        {
            "slot_id": slot_ids[0],
            "question_id": str(uuid.uuid4()),
            "question": "Describe your experience with async architectures in Python.",
            "text": "Describe your experience with async architectures in Python.",
            "question_format": "CONVERSATION",
            "competency": "Python Architecture",
            "rubric": {"score_5": "Exceptional concurrency explanation"},
            "authoring_origin": "LOCAL_LLM_GENERATED",
            "validation_status": "PASSED",
        },
        {
            "slot_id": slot_ids[1],
            "question_id": str(uuid.uuid4()),
            "stem": "Which HTTP status represents a conflict?",
            "text": "Which HTTP status represents a conflict?",
            "question_format": "MCQ_SINGLE_ANSWER",
            "options": [
                {"id": "A", "text": "200 OK"},
                {"id": "B", "text": "404 Not Found"},
                {"id": "C", "text": "409 Conflict"},
                {"id": "D", "text": "500 Internal Server Error"},
            ],
            "correct_option_id": "C",
            "explanation": "HTTP status code 409 indicates a request conflict with current server state.",
            "competency": "HTTP Protocols",
            "rubric": {"score_5": "Correctly identifies HTTP 409"},
            "authoring_origin": "LOCAL_LLM_GENERATED",
            "validation_status": "PASSED",
        },
    ]

    q_set = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=jver.version_number,
        source_content_hash="a" * 64,
        content_hash="b" * 64,
        set_version=1,
        lifecycle_status="APPROVED",
        is_immutable=True,
        created_by_user_id=user_id,
        approved_by_user_id=user_id,
        model_identifier="local/qwen3-4b-thinking",
        items=q_items,
        approved_at=datetime.now(timezone.utc),
    )
    db.add(q_set)
    db.commit()
    db.refresh(q_set)
    return q_set


def test_phase4c2_candidate_interview_workspace_flow(client, test_db):
    org_id = uuid.uuid4()
    other_org_id = uuid.uuid4()

    recruiter = create_test_user(test_db, "recruiter@org.com", UserRole.RECRUITER)

    # Candidate 1 (Alice)
    cand_1_user, cand_1_ident, cand_1_prof, cand_1_binding = setup_candidate_with_profile(
        test_db, org_id, "alice@org.com", "Alice Candidate"
    )
    cand_1_token, cand_1_csrf = create_test_session(test_db, cand_1_user)

    # Candidate 2 (Bob)
    cand_2_user, cand_2_ident, cand_2_prof, cand_2_binding = setup_candidate_with_profile(
        test_db, org_id, "bob@org.com", "Bob Candidate"
    )
    cand_2_token, cand_2_csrf = create_test_session(test_db, cand_2_user)

    job, jver = setup_test_job(test_db, recruiter, org_id)

    slot_1 = str(uuid.uuid4())
    slot_2 = str(uuid.uuid4())
    q_set = setup_approved_ai_question_set(test_db, job, jver, org_id, recruiter.user_id, [slot_1, slot_2])

    # Application for Alice
    app_1 = CandidateApplicationRecord(
        application_id=uuid.uuid4(),
        organization_id=org_id,
        user_id=cand_1_user.user_id,
        binding_id=cand_1_binding.binding_id,
        candidate_id=cand_1_ident.identity_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=jver.version_number,
        profile_version=1,
        status=CandidateApplicationStatus.SUBMITTED.value,
        recruiter_review_state=RecruiterReviewState.INTERVIEW_READY.value,
        submitted_at=datetime.now(timezone.utc),
    )
    test_db.add(app_1)
    test_db.commit()

    # Match result for Alice (must remain unmutated)
    match_run = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        revision=jver.version_number,
        evaluation_date=datetime.now(timezone.utc),
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="1.0.0",
        scoring_policy_version="1.0.0",
        candidate_input_fingerprint="c" * 64,
        input_fingerprint="d" * 64,
        idempotency_key=str(uuid.uuid4()),
        candidate_count=1,
        lifecycle_status=MatchRunLifecycleStatus.SUCCEEDED,
    )
    test_db.add(match_run)
    test_db.commit()

    match_res = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=match_run.run_id,
        profile_id=cand_1_prof.profile_id,
        profile_version=cand_1_prof.profile_version,
        raw_compatibility_score=88.5,
        display_compatibility_score=88.5,
        confidence_tier="HIGH",
        rank=1,
        score_decomposition={},
    )
    test_db.add(match_res)
    test_db.commit()

    # Release for Alice
    release = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        organization_id=org_id,
        candidate_id=cand_1_ident.identity_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=jver.version_number,
        ai_set_id=q_set.ai_set_id,
        set_version=q_set.set_version,
        application_id=app_1.application_id,
        match_result_id=match_res.result_id,
        status=PracticeReleaseStatus.RELEASED.value,
        released_by_user_id=recruiter.user_id,
        released_at=datetime.now(timezone.utc),
    )
    test_db.add(release)
    test_db.commit()

    # -------------------------------------------------------------
    # 1. Candidate Alice can start an active owned assignment
    # -------------------------------------------------------------
    start_res = client.post(
        f"/api/v1/candidate/interviews/{release.release_id}/start",
        cookies={"ai_hiring_candidate_session": cand_1_token},
        headers={"X-CSRF-Token": cand_1_csrf},
    )
    assert start_res.status_code == status.HTTP_200_OK, start_res.json()
    sess_data = start_res.json()
    session_id = sess_data["session_id"]
    assert sess_data["status"] == "IN_PROGRESS"
    assert sess_data["total_questions"] == 2
    assert sess_data["answered_questions"] == 0
    assert len(sess_data["questions"]) == 2

    # -------------------------------------------------------------
    # 4. Question response excludes rubrics, correct answers, explanations
    # -------------------------------------------------------------
    q1 = sess_data["questions"][0]
    assert q1["slot_id"] == slot_1
    assert q1["question_format"] in ("CONVERSATION", "CONVERSATIONAL")
    assert "rubric" not in q1
    assert "authoring_origin" not in q1
    assert "validation_status" not in q1

    q2 = sess_data["questions"][1]
    assert q2["slot_id"] == slot_2
    assert q2["question_format"] in ("MCQ", "MCQ_SINGLE_ANSWER")
    assert len(q2["options"]) == 4
    assert "correct_option_id" not in q2
    assert "explanation" not in q2
    assert "rubric" not in q2

    # -------------------------------------------------------------
    # 2. Repeated start returns the same in-progress session (idempotent)
    # -------------------------------------------------------------
    start_res_2 = client.post(
        f"/api/v1/candidate/interviews/{release.release_id}/start",
        cookies={"ai_hiring_candidate_session": cand_1_token},
        headers={"X-CSRF-Token": cand_1_csrf},
    )
    assert start_res_2.status_code == status.HTTP_200_OK
    assert start_res_2.json()["session_id"] == session_id

    # -------------------------------------------------------------
    # 3. Candidate Bob cannot start or read Alice's assignment/session
    # -------------------------------------------------------------
    bob_start = client.post(
        f"/api/v1/candidate/interviews/{release.release_id}/start",
        cookies={"ai_hiring_candidate_session": cand_2_token},
        headers={"X-CSRF-Token": cand_2_csrf},
    )
    assert bob_start.status_code == status.HTTP_404_NOT_FOUND

    bob_get = client.get(
        f"/api/v1/candidate/interviews/sessions/{session_id}",
        cookies={"ai_hiring_candidate_session": cand_2_token},
    )
    assert bob_get.status_code == status.HTTP_404_NOT_FOUND

    # -------------------------------------------------------------
    # 5. Text answer saves and updates without duplicate rows
    # -------------------------------------------------------------
    save_q1_res = client.put(
        f"/api/v1/candidate/interviews/sessions/{session_id}/answers/{slot_1}",
        json={"text_answer": "I have built high-throughput microservices using asyncio and FastAPI."},
        cookies={"ai_hiring_candidate_session": cand_1_token},
        headers={"X-CSRF-Token": cand_1_csrf},
    )
    assert save_q1_res.status_code == status.HTTP_200_OK, save_q1_res.json()
    assert save_q1_res.json()["is_answered"] is True
    assert save_q1_res.json()["answered_questions"] == 1

    # Update text answer again
    save_q1_res_2 = client.put(
        f"/api/v1/candidate/interviews/sessions/{session_id}/answers/{slot_1}",
        json={"text_answer": "I have built high-throughput microservices using asyncio, uvloop, and FastAPI with connection pooling."},
        cookies={"ai_hiring_candidate_session": cand_1_token},
        headers={"X-CSRF-Token": cand_1_csrf},
    )
    assert save_q1_res_2.status_code == status.HTTP_200_OK
    assert save_q1_res_2.json()["answered_questions"] == 1

    # Verify single row in DB
    ans_rows = test_db.execute(
        select(CandidateInterviewAnswerRecord).where(
            CandidateInterviewAnswerRecord.session_id == uuid.UUID(session_id),
            CandidateInterviewAnswerRecord.slot_id == uuid.UUID(slot_1),
        )
    ).scalars().all()
    assert len(ans_rows) == 1
    assert "connection pooling" in ans_rows[0].text_answer

    # -------------------------------------------------------------
    # 6. Invalid question slot or MCQ option is rejected
    # -------------------------------------------------------------
    bad_slot_res = client.put(
        f"/api/v1/candidate/interviews/sessions/{session_id}/answers/unknown-slot-123",
        json={"text_answer": "Something"},
        cookies={"ai_hiring_candidate_session": cand_1_token},
        headers={"X-CSRF-Token": cand_1_csrf},
    )
    assert bad_slot_res.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND)

    bad_mcq_opt = client.put(
        f"/api/v1/candidate/interviews/sessions/{session_id}/answers/{slot_2}",
        json={"selected_option_id": "INVALID_OPTION_Z"},
        cookies={"ai_hiring_candidate_session": cand_1_token},
        headers={"X-CSRF-Token": cand_1_csrf},
    )
    assert bad_mcq_opt.status_code == status.HTTP_400_BAD_REQUEST

    # Type mismatch rejected: text answer sent to MCQ question
    mismatch_res = client.put(
        f"/api/v1/candidate/interviews/sessions/{session_id}/answers/{slot_2}",
        json={"text_answer": "A text answer to an MCQ"},
        cookies={"ai_hiring_candidate_session": cand_1_token},
        headers={"X-CSRF-Token": cand_1_csrf},
    )
    assert mismatch_res.status_code == status.HTTP_400_BAD_REQUEST

    # Valid MCQ answer save
    save_q2_res = client.put(
        f"/api/v1/candidate/interviews/sessions/{session_id}/answers/{slot_2}",
        json={"selected_option_id": "C"},
        cookies={"ai_hiring_candidate_session": cand_1_token},
        headers={"X-CSRF-Token": cand_1_csrf},
    )
    assert save_q2_res.status_code == status.HTTP_200_OK
    assert save_q2_res.json()["is_answered"] is True
    assert save_q2_res.json()["answered_questions"] == 2

    # -------------------------------------------------------------
    # 7. Refresh fetch returns saved answers and accurate progress
    # -------------------------------------------------------------
    get_res = client.get(
        f"/api/v1/candidate/interviews/sessions/{session_id}",
        cookies={"ai_hiring_candidate_session": cand_1_token},
    )
    assert get_res.status_code == status.HTTP_200_OK
    data = get_res.json()
    assert data["answered_questions"] == 2
    assert data["total_questions"] == 2
    assert "connection pooling" in data["questions"][0]["saved_text_answer"]
    assert data["questions"][1]["saved_selected_option_id"] == "C"

    # -------------------------------------------------------------
    # 11. Incomplete submission requires explicit confirmation
    # Test incomplete submit on a fresh session with 1 answer
    # -------------------------------------------------------------
    # Reset q2 answer to empty to test incomplete submission logic
    clear_q2 = client.put(
        f"/api/v1/candidate/interviews/sessions/{session_id}/answers/{slot_2}",
        json={"selected_option_id": None},
        cookies={"ai_hiring_candidate_session": cand_1_token},
        headers={"X-CSRF-Token": cand_1_csrf},
    )
    assert clear_q2.status_code == status.HTTP_200_OK
    assert clear_q2.json()["answered_questions"] == 1

    # Attempt submit without confirmed_incomplete
    incomplete_submit = client.post(
        f"/api/v1/candidate/interviews/sessions/{session_id}/submit",
        json={"confirmed_incomplete": False},
        cookies={"ai_hiring_candidate_session": cand_1_token},
        headers={"X-CSRF-Token": cand_1_csrf},
    )
    assert incomplete_submit.status_code == status.HTTP_400_BAD_REQUEST
    assert incomplete_submit.json()["detail"]["code"] in (
        "INTERVIEW_INCOMPLETE",
        "INCOMPLETE_SUBMISSION_REQUIRES_CONFIRMATION",
    )

    # -------------------------------------------------------------
    # 8. Final submission locks answers (with confirmed_incomplete or complete)
    # -------------------------------------------------------------
    final_submit = client.post(
        f"/api/v1/candidate/interviews/sessions/{session_id}/submit",
        json={"confirmed_incomplete": True},
        cookies={"ai_hiring_candidate_session": cand_1_token},
        headers={"X-CSRF-Token": cand_1_csrf},
    )
    assert final_submit.status_code == status.HTTP_200_OK
    receipt = final_submit.json()
    assert receipt["status"] == "SUBMITTED"
    assert receipt["submitted_at"] is not None

    # -------------------------------------------------------------
    # 9. Repeated final submission is idempotent
    # -------------------------------------------------------------
    repeat_submit = client.post(
        f"/api/v1/candidate/interviews/sessions/{session_id}/submit",
        json={"confirmed_incomplete": True},
        cookies={"ai_hiring_candidate_session": cand_1_token},
        headers={"X-CSRF-Token": cand_1_csrf},
    )
    assert repeat_submit.status_code == status.HTTP_200_OK
    assert repeat_submit.json()["status"] == "SUBMITTED"

    # -------------------------------------------------------------
    # 10. Saving after submission is rejected
    # -------------------------------------------------------------
    save_after_submit = client.put(
        f"/api/v1/candidate/interviews/sessions/{session_id}/answers/{slot_1}",
        json={"text_answer": "Trying to edit after submission"},
        cookies={"ai_hiring_candidate_session": cand_1_token},
        headers={"X-CSRF-Token": cand_1_csrf},
    )
    assert save_after_submit.status_code == status.HTTP_400_BAD_REQUEST
    assert save_after_submit.json()["detail"]["code"] == "SESSION_ALREADY_SUBMITTED"

    # -------------------------------------------------------------
    # 12. Starting, saving, or submitting never mutates match, application, or job data
    # -------------------------------------------------------------
    refreshed_match = test_db.execute(
        select(MatchResultRecord).where(MatchResultRecord.result_id == match_res.result_id)
    ).scalar_one()
    assert refreshed_match.display_compatibility_score == 88.5

    refreshed_app = test_db.execute(
        select(CandidateApplicationRecord).where(CandidateApplicationRecord.application_id == app_1.application_id)
    ).scalar_one()
    assert refreshed_app.status == CandidateApplicationStatus.SUBMITTED.value
    assert refreshed_app.recruiter_review_state == RecruiterReviewState.INTERVIEW_READY.value

    refreshed_job = test_db.execute(
        select(JobRequisition).where(JobRequisition.job_id == job.job_id)
    ).scalar_one()
    assert refreshed_job.status == JobLifecycleStatus.ACTIVE
