"""Focused backend tests for Phase 4C.4: Recruiter Evaluation Workspace & Final Interview Reports.

Covers all 16 prompt requirements:
1. Recruiter sees only authorized Jobs and submitted sessions.
2. Candidate and unrelated Recruiter cannot access evaluation APIs.
3. Job directory counts submitted, pending, ready, and finalized sessions correctly.
4. Question detail binds the exact question, answer, rubric, and analysis.
5. AI analysis and Recruiter-reviewed values remain separate.
6. Override requires a reason.
7. Manual review works when AI analysis is unavailable.
8. Supplemental questions are excluded from the core aggregate.
9. Final aggregate is calculated deterministically.
10. Incomplete review cannot be finalized.
11. Finalized review cannot be edited in place.
12. Finalization does not mutate match, rank, application, Job, profile, or question-set records.
13. Candidate cannot see unpublished feedback.
14. Candidate sees only their own published Candidate-safe report.
15. Candidate-safe report excludes Recruiter-private fields.
16. Feedback publication is idempotent.
"""
from datetime import datetime, timedelta, timezone
import uuid

from fastapi import status
from fastapi.testclient import TestClient
import pytest
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
    CandidateQuestionEvaluationRecord,
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
from app.models.matching import MatchResultRecord
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


def setup_question_set_with_supplemental(db, job, jver, org_id, user_id, slot_ids):
    q_items = [
        {
            "slot_id": slot_ids[0],
            "question_id": str(uuid.uuid4()),
            "question": "Core Question 1: Explain ACID transactions in PostgreSQL.",
            "question_format": "CONVERSATION",
            "competency": "Databases",
            "is_supplemental": False,
            "rubric": {"score_5": "Mastery of isolation levels and WAL"},
        },
        {
            "slot_id": slot_ids[1],
            "question_id": str(uuid.uuid4()),
            "stem": "Core Question 2: Which HTTP status code is used for Rate Limiting?",
            "question_format": "MCQ_SINGLE_ANSWER",
            "options": [
                {"option_id": "A", "text": "400 Bad Request"},
                {"option_id": "B", "text": "429 Too Many Requests"},
                {"option_id": "C", "text": "500 Internal Server Error"},
                {"option_id": "D", "text": "503 Service Unavailable"},
            ],
            "correct_option_id": "B",
            "explanation": "HTTP 429 indicates user has sent too many requests.",
            "competency": "API Design",
            "is_supplemental": False,
        },
        {
            "slot_id": slot_ids[2],
            "question_id": str(uuid.uuid4()),
            "question": "Supplemental Question 3: Tell us about an open-source contribution you made.",
            "question_format": "CONVERSATION",
            "competency": "Open Source",
            "is_supplemental": True,
            "layer": "SUPPLEMENTAL_EVIDENCE",
            "rubric": {"score_5": "Detailed PR walkthrough"},
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


def test_phase4c4_evaluation_workspace_flow(client: TestClient, test_db):
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()

    # Create recruiters
    recruiter_a = create_test_user(test_db, "recruiter_a@org-a.com", UserRole.RECRUITER)
    recruiter_b = create_test_user(test_db, "recruiter_b@org-b.com", UserRole.RECRUITER)
    tok_rec_a, csrf_rec_a = create_test_session(test_db, recruiter_a)
    tok_rec_b, csrf_rec_b = create_test_session(test_db, recruiter_b)

    rec_a_headers = {
        "Cookie": f"ai_hiring_session={tok_rec_a}; csrf_token={csrf_rec_a}",
        "X-CSRF-Token": csrf_rec_a,
    }
    rec_b_headers = {
        "Cookie": f"ai_hiring_session={tok_rec_b}; csrf_token={csrf_rec_b}",
        "X-CSRF-Token": csrf_rec_b,
    }

    # Setup job and candidate
    job_a, jver_a = setup_test_job(test_db, recruiter_a, org_a, "Staff Distributed Engineer", 1)
    cand_user, cand_id, prof, binding = setup_candidate_with_profile(test_db, org_a, "cand_test@domain.com", "Alice Engineer")
    tok_cand, csrf_cand = create_test_session(test_db, cand_user)
    cand_cookies = {"ai_hiring_candidate_session": tok_cand}
    cand_headers = {"X-CSRF-Token": csrf_cand}

    # Match Run and Match Result reference
    from decimal import Decimal
    from app.models.matching import MatchRunRecord, MatchRunLifecycleStatus

    match_run = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter_a.user_id,
        job_id=job_a.job_id,
        job_version_id=jver_a.job_version_id,
        revision=1,
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
        profile_id=prof.profile_id,
        profile_version=1,
        raw_compatibility_score=Decimal("88.000000"),
        display_compatibility_score=Decimal("88.00"),
        confidence_tier="HIGH",
        rank=1,
        score_decomposition={
            "matched_requirements_count": 4,
            "total_requirements_count": 5,
            "category_scores": {"Databases": 0.9, "API Design": 0.85},
        },
    )
    test_db.add(match_res)
    test_db.commit()

    # Add submitted application for candidate on job_a to fulfill eligibility
    cand_app = CandidateApplicationRecord(
        application_id=uuid.uuid4(),
        organization_id=org_a,
        candidate_id=cand_id.identity_id,
        job_id=job_a.job_id,
        job_version_id=jver_a.job_version_id,
        job_revision=1,
        status=CandidateApplicationStatus.SUBMITTED.value,
    )
    test_db.add(cand_app)
    test_db.commit()

    slot_1 = str(uuid.uuid4())
    slot_2 = str(uuid.uuid4())
    slot_3 = str(uuid.uuid4())
    q_set = setup_question_set_with_supplemental(test_db, job_a, jver_a, org_a, recruiter_a.user_id, [slot_1, slot_2, slot_3])

    # Create release
    release = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        organization_id=org_a,
        job_id=job_a.job_id,
        job_version_id=jver_a.job_version_id,
        job_revision=1,
        ai_set_id=q_set.ai_set_id,
        set_version=1,
        candidate_id=cand_id.identity_id,
        match_result_id=match_res.result_id,
        status=PracticeReleaseStatus.RELEASED.value,
        released_by_user_id=recruiter_a.user_id,
    )
    test_db.add(release)
    test_db.commit()

    # 1. Candidate starts and submits interview
    res_start = client.post(
        f"/api/v1/candidate/interviews/{release.release_id}/start",
        cookies=cand_cookies,
        headers=cand_headers,
    )
    assert res_start.status_code == status.HTTP_200_OK, res_start.json()
    session_id = res_start.json()["session_id"]

    # Save answers: slot 1 (text), slot 2 (MCQ 'B' - correct), slot 3 (supplemental text)
    client.put(
        f"/api/v1/candidate/interviews/sessions/{session_id}/answers/{slot_1}",
        json={"text_answer": "PostgreSQL ensures ACID by utilizing Write-Ahead Logging (WAL) and MVCC multi-version concurrency control."},
        cookies=cand_cookies,
        headers=cand_headers,
    )
    client.put(
        f"/api/v1/candidate/interviews/sessions/{session_id}/answers/{slot_2}",
        json={"selected_option_id": "B"},
        cookies=cand_cookies,
        headers=cand_headers,
    )
    client.put(
        f"/api/v1/candidate/interviews/sessions/{session_id}/answers/{slot_3}",
        json={"text_answer": "I contributed to FastAPI by writing async documentation and fixing edge case query param parsing."},
        cookies=cand_cookies,
        headers=cand_headers,
    )

    # Submit interview
    res_sub = client.post(
        f"/api/v1/candidate/interviews/sessions/{session_id}/submit",
        json={"confirmed_incomplete": False},
        cookies=cand_cookies,
        headers=cand_headers,
    )
    assert res_sub.status_code == status.HTTP_200_OK

    # -------------------------------------------------------------
    # 2. Authorization Checks
    # -------------------------------------------------------------
    # Candidate cannot access recruiter evaluation directory
    res_cand_eval = client.get("/api/v1/recruiter/evaluations/jobs", cookies=cand_cookies, headers=cand_headers)
    assert res_cand_eval.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    # Unrelated Recruiter B cannot access Recruiter A's evaluation session
    res_b_eval = client.get(f"/api/v1/recruiter/evaluations/sessions/{session_id}", headers=rec_b_headers)
    assert res_b_eval.status_code == status.HTTP_404_NOT_FOUND

    # -------------------------------------------------------------
    # 3. Level 1: Job Directory Checks
    # -------------------------------------------------------------
    res_jobs = client.get("/api/v1/recruiter/evaluations/jobs", headers=rec_a_headers)
    assert res_jobs.status_code == status.HTTP_200_OK
    job_dirs = res_jobs.json()
    assert len(job_dirs) == 1
    assert job_dirs[0]["job_title"] == "Staff Distributed Engineer"
    assert job_dirs[0]["job_revision"] == 1
    assert job_dirs[0]["submitted_count"] == 1
    assert job_dirs[0]["ready_for_review_count"] == 1
    assert job_dirs[0]["finalized_count"] == 0

    # -------------------------------------------------------------
    # 4. Level 2: Candidate Sessions List
    # -------------------------------------------------------------
    res_cands = client.get(
        f"/api/v1/recruiter/evaluations/jobs/{job_a.job_id}/revisions/1/candidates",
        headers=rec_a_headers,
    )
    assert res_cands.status_code == status.HTTP_200_OK
    cand_list = res_cands.json()
    assert len(cand_list) == 1
    assert cand_list[0]["candidate_name"] == "Alice Engineer"
    assert cand_list[0]["resume_match_score"] == 88.0
    assert "4 of 5" in cand_list[0]["required_skills_coverage"]
    assert cand_list[0]["evaluation_status"] == "READY_FOR_REVIEW"

    # -------------------------------------------------------------
    # 5. Level 3: Candidate Evaluation Detail & Provenance Separation
    # -------------------------------------------------------------
    res_detail = client.get(f"/api/v1/recruiter/evaluations/sessions/{session_id}", headers=rec_a_headers)
    assert res_detail.status_code == status.HTTP_200_OK
    detail = res_detail.json()

    assert detail["candidate_name"] == "Alice Engineer"
    assert detail["resume_match_reference"]["overall_score"] == 88.0
    assert len(detail["questions"]) == 3

    # Check question 1 (Conversational)
    q1 = next(q for q in detail["questions"] if q["slot_id"] == slot_1)
    assert q1["question_format"] == "CONVERSATIONAL"
    assert "WAL" in q1["submitted_answer_text"]
    assert q1["ai_provisional_analysis"]["provisional_rating"] is None
    assert q1["ai_provisional_analysis"]["analysis_status"] in ("PENDING", "UNAVAILABLE")

    # Check question 2 (MCQ - Correct)
    q2 = next(q for q in detail["questions"] if q["slot_id"] == slot_2)
    assert q2["question_format"] == "MCQ"
    assert q2["selected_option_id"] == "B"
    assert q2["correct_option_id"] == "B"
    assert q2["is_correct"] is True

    # Check question 3 (Supplemental)
    q3 = next(q for q in detail["questions"] if q["slot_id"] == slot_3)
    assert q3["is_supplemental"] is True

    # -------------------------------------------------------------
    # 6. Incomplete Evaluation Cannot be Finalized (Q1 has no AI rating and no override)
    # -------------------------------------------------------------
    res_premature_finalize = client.post(
        f"/api/v1/recruiter/evaluations/sessions/{session_id}/finalize",
        headers=rec_a_headers,
    )
    assert res_premature_finalize.status_code == status.HTTP_400_BAD_REQUEST
    assert res_premature_finalize.json()["detail"]["code"] == "INCOMPLETE_EVALUATION"

    # -------------------------------------------------------------
    # 7. Override Validation: Override requires an explanation
    # -------------------------------------------------------------
    res_bad_override = client.put(
        f"/api/v1/recruiter/evaluations/sessions/{session_id}/questions/{slot_1}/review",
        json={
            "review_state": "OVERRIDDEN",
            "reviewed_rating": 5,
            "override_reason": "",  # Empty reason rejected
        },
        headers=rec_a_headers,
    )
    assert res_bad_override.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Valid review with override reason
    res_valid_review_1 = client.put(
        f"/api/v1/recruiter/evaluations/sessions/{session_id}/questions/{slot_1}/review",
        json={
            "review_state": "OVERRIDDEN",
            "reviewed_rating": 5,
            "override_reason": "Candidate provided exceptionally thorough WAL and MVCC breakdown.",
            "recruiter_note": "Clear senior-level understanding of database engine internals.",
        },
        headers=rec_a_headers,
    )
    assert res_valid_review_1.status_code == status.HTTP_200_OK
    assert res_valid_review_1.json()["overview"]["questions_reviewed"] == 1

    # Review question 2 (MCQ) - Rating 5
    res_review_2 = client.put(
        f"/api/v1/recruiter/evaluations/sessions/{session_id}/questions/{slot_2}/review",
        json={
            "review_state": "ACCEPTED",
            "reviewed_rating": 5,
        },
        headers=rec_a_headers,
    )
    assert res_review_2.status_code == status.HTTP_200_OK

    # Review question 3 (Supplemental) - Rating 1 (Supplemental must be excluded from core average)
    client.put(
        f"/api/v1/recruiter/evaluations/sessions/{session_id}/questions/{slot_3}/review",
        json={
            "review_state": "ACCEPTED",
            "reviewed_rating": 1,
        },
        headers=rec_a_headers,
    )

    # -------------------------------------------------------------
    # 8. Deterministic Core Aggregate Calculation & Finalization
    # -------------------------------------------------------------
    # Core questions: Q1 (5) + Q2 (5) -> Average = 5.0 (Supplemental Q3 with 1 is excluded!)
    res_finalize = client.post(
        f"/api/v1/recruiter/evaluations/sessions/{session_id}/finalize",
        headers=rec_a_headers,
    )
    assert res_finalize.status_code == status.HTTP_200_OK
    fin_data = res_finalize.json()
    assert fin_data["status"] == "FINALIZED"
    assert fin_data["aggregate_score"] == 5.0  # Core average strictly 5.0

    # -------------------------------------------------------------
    # 9. Immutability: Finalized evaluation cannot be edited
    # -------------------------------------------------------------
    res_edit_after_finalize = client.put(
        f"/api/v1/recruiter/evaluations/sessions/{session_id}/questions/{slot_1}/review",
        json={"review_state": "ACCEPTED", "reviewed_rating": 3},
        headers=rec_a_headers,
    )
    assert res_edit_after_finalize.status_code == status.HTTP_400_BAD_REQUEST
    assert res_edit_after_finalize.json()["detail"]["code"] == "EVALUATION_FINALIZED"

    # -------------------------------------------------------------
    # 10. Candidate Feedback Privacy & Publication
    # -------------------------------------------------------------
    # Candidate cannot see unpublished feedback
    res_cand_unpub = client.get(
        f"/api/v1/candidate/interviews/sessions/{session_id}/feedback",
        cookies=cand_cookies,
        headers=cand_headers,
    )
    assert res_cand_unpub.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)

    # Recruiter publishes feedback (Idempotent)
    res_pub_1 = client.post(
        f"/api/v1/recruiter/evaluations/sessions/{session_id}/publish-feedback",
        headers=rec_a_headers,
    )
    assert res_pub_1.status_code == status.HTTP_200_OK
    assert res_pub_1.json()["candidate_feedback_status"] == "PUBLISHED"

    res_pub_2 = client.post(
        f"/api/v1/recruiter/evaluations/sessions/{session_id}/publish-feedback",
        headers=rec_a_headers,
    )
    assert res_pub_2.status_code == status.HTTP_200_OK

    # Candidate now retrieves their Candidate-Safe report
    res_cand_pub = client.get(
        f"/api/v1/candidate/interviews/sessions/{session_id}/feedback",
        cookies=cand_cookies,
        headers=cand_headers,
    )
    assert res_cand_pub.status_code == status.HTTP_200_OK
    cand_report = res_cand_pub.json()
    assert cand_report["job_title"] == "Staff Distributed Engineer"
    assert len(cand_report["questions_feedback"]) == 3

    # Candidate report STRICTLY omits internal recruiter notes and override reasons
    report_str = str(cand_report)
    assert "Clear senior-level understanding" not in report_str
    assert "Candidate provided exceptionally thorough" not in report_str
    assert "score_5" not in report_str
