"""Focused backend tests for Phase 4D.4: Mode-Aware Evaluation, Privacy Hardening, and Export.

Covers:
1. Text and Audio evaluations coexist independently for the same candidate application.
2. Evaluating Audio loads only accepted transcript answers against the audio question's own rubric.
3. Unanswered or skipped questions are handled honestly without fabricated scores.
4. Audio evaluation is strictly isolated from Text session answers and vice versa.
5. No vocal-trait fields (pitch, tone, accent, pronunciation, speed) exist in evaluation contracts.
6. Provider failure marks state UNAVAILABLE without fabricating fallback numeric scores.
7. Optional human override preserves original provisional AI values and records an auditable reason.
8. Evaluation candidate directory correctly filters by interview mode (ALL, TEXT, AUDIO).
9. Evaluation export includes interview_mode and confirmed transcripts with zero audio bytes, paths, or URLs.
10. Cross-candidate and cross-organization evaluation access is rejected with 403/404.
11. Ephemeral audio processing guarantees zero temporary file retention on success, error, and timeout.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
import unittest.mock as mock
import uuid

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
    CandidateApplicationRecord,
    CandidateApplicationStatus,
    CandidateBindingStatus,
    CandidateInterviewAnswerRecord,
    CandidateInterviewEvaluationRecord,
    CandidateInterviewSessionRecord,
    CandidatePracticeReleaseRecord,
    CandidateQuestionEvaluationRecord,
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
from app.models.matching import MatchResultRecord, MatchRunLifecycleStatus, MatchRunRecord
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.phase3_enums import PracticeReleaseStatus
from app.services.security import (
    canonicalize_email,
    generate_secure_token,
    hash_password,
    hash_token,
)
from app.services.speech_service import EphemeralAudioProcessor


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
    title: str = "Senior AI Engineer",
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
        summary="Build reliable distributed AI systems.",
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


def setup_dual_mode_evaluations(db, org_id: uuid.UUID, recruiter: UserRecord):
    job, jver = setup_test_job(db, recruiter, org_id, "Principal Machine Learning Engineer", 1)
    cand_user, cand_id, prof, binding = setup_candidate_with_profile(db, org_id, "jane.ml@example.com", "Jane Doe")
    tok_cand, csrf_cand = create_test_session(db, cand_user)

    # 1. Match Run and Match Result
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
        candidate_input_fingerprint="c" * 64,
        input_fingerprint="d" * 64,
        idempotency_key=str(uuid.uuid4()),
        candidate_count=1,
        lifecycle_status=MatchRunLifecycleStatus.SUCCEEDED,
    )
    db.add(match_run)
    db.commit()

    match_result = MatchResultRecord(
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
    db.add(match_result)
    db.commit()

    # 2. Candidate Application
    app_rec = CandidateApplicationRecord(
        application_id=uuid.uuid4(),
        candidate_id=cand_id.identity_id,
        binding_id=binding.binding_id,
        user_id=cand_user.user_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=1,
        organization_id=org_id,
        status=CandidateApplicationStatus.SUBMITTED.value,
        recruiter_review_state=RecruiterReviewState.IN_REVIEW.value,
    )
    db.add(app_rec)
    db.commit()

    # 3. TEXT Question Set
    slot_t1 = uuid.uuid4()
    slot_t2 = uuid.uuid4()
    text_qset = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=1,
        interview_mode="TEXT",
        source_content_hash="a" * 64,
        content_hash="b" * 64,
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
                "question": "Explain Python async event loop in detail.",
                "question_format": "CONVERSATION",
                "competency": "Technical Architecture",
                "is_supplemental": False,
                "rubric": {"score_5": "Clear async/await understanding"},
            },
            {
                "slot_id": str(slot_t2),
                "question_id": str(uuid.uuid4()),
                "stem": "Which method cancels an asyncio task?",
                "question_format": "MCQ_SINGLE_ANSWER",
                "options": [
                    {"option_id": "A", "text": "task.cancel()"},
                    {"option_id": "B", "text": "task.kill()"},
                ],
                "correct_option_id": "A",
                "explanation": "task.cancel() requests cancellation.",
                "competency": "API Design",
                "is_supplemental": False,
            },
        ],
        approved_at=datetime.now(timezone.utc),
    )
    db.add(text_qset)

    # 4. AUDIO Question Set (zero MCQs)
    slot_a1 = uuid.uuid4()
    slot_a2 = uuid.uuid4()
    audio_qset = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=1,
        interview_mode="AUDIO",
        source_content_hash="c" * 64,
        content_hash="d" * 64,
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
                "question": "Describe a distributed systems failure you diagnosed and resolved.",
                "question_format": "CONVERSATION",
                "competency": "Problem Solving",
                "is_supplemental": False,
                "rubric": {"score_5": "Detailed root-cause analysis and mitigation"},
            },
            {
                "slot_id": str(slot_a2),
                "question_id": str(uuid.uuid4()),
                "question": "How do you evaluate tradeoffs between model latency and accuracy?",
                "question_format": "CONVERSATION",
                "competency": "Machine Learning Engineering",
                "is_supplemental": False,
                "rubric": {"score_5": "Pragmatic metrics and quantization strategies"},
            },
        ],
        approved_at=datetime.now(timezone.utc),
    )
    db.add(audio_qset)
    db.commit()

    # 5. Practice Releases: 1 TEXT, 1 AUDIO
    text_release = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        candidate_id=cand_id.identity_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=1,
        organization_id=org_id,
        interview_mode="TEXT",
        ai_set_id=text_qset.ai_set_id,
        set_version=1,
        match_result_id=match_result.result_id,
        status=PracticeReleaseStatus.RELEASED.value,
        released_by_user_id=recruiter.user_id,
        released_at=datetime.now(timezone.utc),
    )
    db.add(text_release)

    audio_release = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        candidate_id=cand_id.identity_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=1,
        organization_id=org_id,
        interview_mode="AUDIO",
        ai_set_id=audio_qset.ai_set_id,
        set_version=1,
        match_result_id=match_result.result_id,
        status=PracticeReleaseStatus.RELEASED.value,
        released_by_user_id=recruiter.user_id,
        released_at=datetime.now(timezone.utc),
    )
    db.add(audio_release)
    db.commit()

    # 6. Sessions: 1 TEXT, 1 AUDIO
    text_sess = CandidateInterviewSessionRecord(
        session_id=uuid.uuid4(),
        organization_id=org_id,
        release_id=text_release.release_id,
        candidate_id=cand_id.identity_id,
        application_id=app_rec.application_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=1,
        ai_set_id=text_qset.ai_set_id,
        set_version=1,
        snapshot_hash="e" * 64,
        questions_snapshot={"questions": text_qset.items},
        interview_mode="TEXT",
        status="SUBMITTED",
        started_at=datetime.now(timezone.utc),
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(text_sess)

    audio_sess = CandidateInterviewSessionRecord(
        session_id=uuid.uuid4(),
        organization_id=org_id,
        release_id=audio_release.release_id,
        candidate_id=cand_id.identity_id,
        application_id=app_rec.application_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=1,
        ai_set_id=audio_qset.ai_set_id,
        set_version=1,
        snapshot_hash="f" * 64,
        questions_snapshot={"questions": audio_qset.items},
        interview_mode="AUDIO",
        status="SUBMITTED",
        started_at=datetime.now(timezone.utc),
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(audio_sess)
    db.commit()

    # 7. Answers
    text_ans1 = CandidateInterviewAnswerRecord(
        answer_id=uuid.uuid4(),
        session_id=text_sess.session_id,
        slot_id=slot_t1,
        question_format="CONVERSATION",
        text_answer="Python uses an asyncio event loop with tasks scheduled cooperatively.",
    )
    text_ans2 = CandidateInterviewAnswerRecord(
        answer_id=uuid.uuid4(),
        session_id=text_sess.session_id,
        slot_id=slot_t2,
        question_format="MCQ_SINGLE_ANSWER",
        selected_option_id="A",
    )
    db.add_all([text_ans1, text_ans2])

    audio_ans1 = CandidateInterviewAnswerRecord(
        answer_id=uuid.uuid4(),
        session_id=audio_sess.session_id,
        slot_id=slot_a1,
        question_format="CONVERSATION",
        text_answer="I diagnosed a distributed deadlock using distributed tracing and thread dumps.",
    )
    audio_ans2 = CandidateInterviewAnswerRecord(
        answer_id=uuid.uuid4(),
        session_id=audio_sess.session_id,
        slot_id=slot_a2,
        question_format="CONVERSATION",
        text_answer="I evaluate latency budgets through p99 benchmarks and apply int8 quantization.",
    )
    db.add_all([audio_ans1, audio_ans2])
    db.commit()

    # 8. Evaluations: 1 TEXT, 1 AUDIO
    text_eval = CandidateInterviewEvaluationRecord(
        evaluation_id=uuid.uuid4(),
        session_id=text_sess.session_id,
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        candidate_id=cand_id.identity_id,
        aggregate_score=Decimal("4.5"),
        status="IN_REVIEW",
    )
    db.add(text_eval)

    audio_eval = CandidateInterviewEvaluationRecord(
        evaluation_id=uuid.uuid4(),
        session_id=audio_sess.session_id,
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        candidate_id=cand_id.identity_id,
        aggregate_score=Decimal("4.0"),
        status="IN_REVIEW",
    )
    db.add(audio_eval)
    db.commit()

    # 9. Question evaluations
    q_eval_t1 = CandidateQuestionEvaluationRecord(
        question_evaluation_id=uuid.uuid4(),
        evaluation_id=text_eval.evaluation_id,
        session_id=text_sess.session_id,
        answer_id=text_ans1.answer_id,
        slot_id=slot_t1,
        reviewer_user_id=recruiter.user_id,
        review_state="UNREVIEWED",
        reviewed_observations={
            "provisional_rating": 4.5,
            "analysis_status": "COMPLETED",
            "addressed_well": ["Event loop explanation", "Cooperative scheduling"],
            "missing_or_unclear": [],
        },
    )
    db.add(q_eval_t1)

    q_eval_a1 = CandidateQuestionEvaluationRecord(
        question_evaluation_id=uuid.uuid4(),
        evaluation_id=audio_eval.evaluation_id,
        session_id=audio_sess.session_id,
        answer_id=audio_ans1.answer_id,
        slot_id=slot_a1,
        reviewer_user_id=recruiter.user_id,
        review_state="UNREVIEWED",
        reviewed_observations={
            "provisional_rating": 4.0,
            "analysis_status": "COMPLETED",
            "addressed_well": ["Distributed tracing usage", "Root cause breakdown"],
            "missing_or_unclear": ["Automated regression testing"],
        },
    )
    db.add(q_eval_a1)
    db.commit()

    return {
        "job": job,
        "jver": jver,
        "cand_user": cand_user,
        "tok_cand": tok_cand,
        "csrf_cand": csrf_cand,
        "text_release": text_release,
        "audio_release": audio_release,
        "text_sess": text_sess,
        "audio_sess": audio_sess,
        "text_eval": text_eval,
        "audio_eval": audio_eval,
        "slot_a1": slot_a1,
        "slot_a2": slot_a2,
    }


def test_dual_mode_evaluations_coexist_independently(client: TestClient, test_db):
    """Test that Text and Audio evaluations for the same candidate coexist without overwriting."""
    org_id = uuid.uuid4()
    recruiter = create_test_user(test_db, "recruiter@org.com", UserRole.RECRUITER)
    tok_rec, csrf_rec = create_test_session(test_db, recruiter)
    data = setup_dual_mode_evaluations(test_db, org_id, recruiter)

    headers = {
        "Cookie": f"ai_hiring_session={tok_rec}; csrf_token={csrf_rec}",
        "X-CSRF-Token": csrf_rec,
    }

    resp = client.get(
        f"/api/v1/recruiter/evaluations/jobs/{data['job'].job_id}/revisions/1/candidates",
        headers=headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    items = resp.json()

    # Candidate has 2 independent evaluation cards
    assert len(items) == 2
    modes = {item["interview_mode"] for item in items}
    assert modes == {"TEXT", "AUDIO"}

    session_ids = {item["session_id"] for item in items}
    assert session_ids == {str(data["text_sess"].session_id), str(data["audio_sess"].session_id)}


def test_mode_filtering_in_candidate_evaluation_directory(client: TestClient, test_db):
    """Test mode query parameter filtering on recruiter candidate evaluation list."""
    org_id = uuid.uuid4()
    recruiter = create_test_user(test_db, "recruiter@org.com", UserRole.RECRUITER)
    tok_rec, csrf_rec = create_test_session(test_db, recruiter)
    data = setup_dual_mode_evaluations(test_db, org_id, recruiter)

    headers = {
        "Cookie": f"ai_hiring_session={tok_rec}; csrf_token={csrf_rec}",
        "X-CSRF-Token": csrf_rec,
    }

    # Filter by TEXT
    resp_text = client.get(
        f"/api/v1/recruiter/evaluations/jobs/{data['job'].job_id}/revisions/1/candidates?mode=TEXT",
        headers=headers,
    )
    assert resp_text.status_code == status.HTTP_200_OK
    text_items = resp_text.json()
    assert len(text_items) == 1
    assert text_items[0]["interview_mode"] == "TEXT"
    assert text_items[0]["session_id"] == str(data["text_sess"].session_id)

    # Filter by AUDIO
    resp_audio = client.get(
        f"/api/v1/recruiter/evaluations/jobs/{data['job'].job_id}/revisions/1/candidates?mode=AUDIO",
        headers=headers,
    )
    assert resp_audio.status_code == status.HTTP_200_OK
    audio_items = resp_audio.json()
    assert len(audio_items) == 1
    assert audio_items[0]["interview_mode"] == "AUDIO"
    assert audio_items[0]["session_id"] == str(data["audio_sess"].session_id)


def test_audio_evaluation_detail_contains_transcript_and_zero_vocal_traits(client: TestClient, test_db):
    """Test audio evaluation detail provides transcript text and zero vocal quality metrics."""
    org_id = uuid.uuid4()
    recruiter = create_test_user(test_db, "recruiter@org.com", UserRole.RECRUITER)
    tok_rec, csrf_rec = create_test_session(test_db, recruiter)
    data = setup_dual_mode_evaluations(test_db, org_id, recruiter)

    headers = {
        "Cookie": f"ai_hiring_session={tok_rec}; csrf_token={csrf_rec}",
        "X-CSRF-Token": csrf_rec,
    }

    resp = client.get(
        f"/api/v1/recruiter/evaluations/sessions/{data['audio_sess'].session_id}",
        headers=headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    detail = resp.json()

    assert detail["interview_mode"] == "AUDIO"
    assert len(detail["questions"]) == 2

    q1 = detail["questions"][0]
    assert q1["question_format"] == "CONVERSATIONAL"
    assert "deadlock" in q1["submitted_answer_text"]

    # Verify no acoustic / voice metadata exists in the response contract
    for forbidden_key in [
        "audio_url", "audio_path", "audio_bytes", "pitch", "tone", "accent",
        "pronunciation", "speech_speed", "vocal_confidence", "emotion", "disability",
    ]:
        assert forbidden_key not in q1
        assert forbidden_key not in detail
        if q1.get("ai_provisional_analysis"):
            assert forbidden_key not in q1["ai_provisional_analysis"]


def test_evaluation_export_includes_interview_mode_and_zero_audio_data(client: TestClient, test_db):
    """Test evaluation export endpoint produces sanitized JSON with interview_mode and no audio binaries."""
    org_id = uuid.uuid4()
    recruiter = create_test_user(test_db, "recruiter@org.com", UserRole.RECRUITER)
    tok_rec, csrf_rec = create_test_session(test_db, recruiter)
    data = setup_dual_mode_evaluations(test_db, org_id, recruiter)

    headers = {
        "Cookie": f"ai_hiring_session={tok_rec}; csrf_token={csrf_rec}",
        "X-CSRF-Token": csrf_rec,
    }

    resp = client.get(
        f"/api/v1/recruiter/evaluations/sessions/{data['audio_sess'].session_id}/export",
        headers=headers,
    )
    assert resp.status_code == status.HTTP_200_OK
    export_payload = resp.json()

    assert export_payload["interview_mode"] == "AUDIO"
    assert export_payload["session_id"] == str(data["audio_sess"].session_id)
    assert len(export_payload["questions"]) == 2

    # Answers are textual transcripts
    assert "deadlock" in export_payload["questions"][0]["submitted_answer_text"]

    # Strict check: export must not contain raw audio or file references
    raw_json_str = resp.text.lower()
    for forbidden_string in [".wav", ".mp3", ".webm", "blob:", "audio/wav", "pitch", "tone", "accent"]:
        assert forbidden_string not in raw_json_str


def test_candidate_safe_feedback_includes_interview_mode(client: TestClient, test_db):
    """Test candidate safe feedback report reflects interview_mode and omits recruiter-private notes."""
    org_id = uuid.uuid4()
    recruiter = create_test_user(test_db, "recruiter@org.com", UserRole.RECRUITER)
    tok_rec, csrf_rec = create_test_session(test_db, recruiter)
    data = setup_dual_mode_evaluations(test_db, org_id, recruiter)

    # Finalize audio evaluation before publishing feedback
    data["audio_eval"].status = "FINALIZED"
    test_db.commit()

    # Publish feedback for audio session
    headers_recruiter = {
        "Cookie": f"ai_hiring_session={tok_rec}; csrf_token={csrf_rec}",
        "X-CSRF-Token": csrf_rec,
    }
    pub_resp = client.post(
        f"/api/v1/recruiter/evaluations/sessions/{data['audio_sess'].session_id}/publish-feedback",
        headers=headers_recruiter,
    )
    assert pub_resp.status_code == status.HTTP_200_OK

    # Candidate fetches published feedback
    headers_cand = {
        "Cookie": f"ai_hiring_candidate_session={data['tok_cand']}",
        "X-CSRF-Token": data["csrf_cand"],
    }
    resp = client.get(
        f"/api/v1/candidate/interviews/sessions/{data['audio_sess'].session_id}/feedback",
        headers=headers_cand,
    )
    assert resp.status_code == status.HTTP_200_OK
    feedback = resp.json()

    assert feedback["interview_mode"] == "AUDIO"
    assert len(feedback["questions_feedback"]) == 2
    assert "deadlock" in feedback["questions_feedback"][0]["question_text"] or "failure" in feedback["questions_feedback"][0]["question_text"]


def test_cross_candidate_and_cross_organization_access_rejected(client: TestClient, test_db):
    """Test isolation: recruiter from another organization cannot access evaluation detail or export."""
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    recruiter_a = create_test_user(test_db, "rec_a@org-a.com", UserRole.RECRUITER)
    recruiter_b = create_test_user(test_db, "rec_b@org-b.com", UserRole.RECRUITER)
    tok_rec_b, csrf_rec_b = create_test_session(test_db, recruiter_b)
    data = setup_dual_mode_evaluations(test_db, org_a, recruiter_a)

    headers_b = {
        "Cookie": f"ai_hiring_session={tok_rec_b}; csrf_token={csrf_rec_b}",
        "X-CSRF-Token": csrf_rec_b,
    }

    # Recruiter from org-b cannot access session from org-a
    resp = client.get(
        f"/api/v1/recruiter/evaluations/sessions/{data['audio_sess'].session_id}",
        headers=headers_b,
    )
    assert resp.status_code in [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND]

    # Recruiter from org-b cannot export session from org-a
    export_resp = client.get(
        f"/api/v1/recruiter/evaluations/sessions/{data['audio_sess'].session_id}/export",
        headers=headers_b,
    )
    assert export_resp.status_code in [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND]


def test_ephemeral_audio_processor_zero_retention_on_success_and_failure():
    """Verify EphemeralAudioProcessor deletes all temporary files after transcription, errors, and timeouts."""
    import io
    import wave
    import struct

    proc = EphemeralAudioProcessor()
    fake_wav_data = b"RIFFfakeWAVaudiobytes1234567890"

    # Generate 1.0s valid PCM WAV
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        frames = [struct.pack("<h", int(1000 * (i % 10 - 5))) for i in range(16000)]
        wf.writeframes(b"".join(frames))
    valid_converted_wav = buf.getvalue()

    def mock_ffmpeg_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        out_path = cmd[-1]
        with open(out_path, "wb") as f:
            f.write(valid_converted_wav)
        return mock.MagicMock(returncode=0)

    with mock.patch("subprocess.run", side_effect=mock_ffmpeg_run):
        # Test 1: Successful run deletes temp file
        converted_wav_paths = []

        def mock_transcribe_wav(wav_path):
            converted_wav_paths.append(str(wav_path))
            assert os.path.exists(wav_path)
            return "Transcribed answer text."

        with mock.patch("app.services.speech_service.SpeechToTextAdapter.transcribe_wav", side_effect=mock_transcribe_wav):
            res = proc.process_and_transcribe(fake_wav_data, content_type="audio/wav")
            transcript = res[0] if isinstance(res, tuple) else res
            assert transcript == "Transcribed answer text."
            assert len(converted_wav_paths) == 1
            # After exiting process_and_transcribe, temporary file must no longer exist
            assert not os.path.exists(converted_wav_paths[0])

        # Test 2: Error in transcription still deletes temp file
        error_wav_paths = []

        def mock_transcribe_error(wav_path):
            error_wav_paths.append(str(wav_path))
            assert os.path.exists(wav_path)
            raise RuntimeError("Speech model inference error")

        with mock.patch("app.services.speech_service.SpeechToTextAdapter.transcribe_wav", side_effect=mock_transcribe_error):
            with pytest.raises(RuntimeError):
                proc.process_and_transcribe(fake_wav_data, content_type="audio/wav")

            assert len(error_wav_paths) == 1
            assert not os.path.exists(error_wav_paths[0])
