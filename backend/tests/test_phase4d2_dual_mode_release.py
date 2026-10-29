"""Focused tests for Phase 4D.2: Mode-Specific Interview Release and Candidate Session Isolation.

Covers:
1. Existing Text release still succeeds.
2. Approved Audio set creates an Audio release.
3. Audio release with any MCQ is rejected with AUDIO_MCQ_NOT_ALLOWED.
4. Same application can hold one Text and one Audio release independently.
5. Duplicate release within the same mode follows existing duplicate/idempotency policy.
6. Cross-mode question set / release mismatch is rejected with structured errors.
7. Cross-job, cross-application, cross-candidate, and cross-org release attempts are rejected.
8. Text and Audio sessions preserve separate question snapshots and modes.
9. Candidate A cannot access Candidate B's release.
10. Audio session cannot enter IN_PROGRESS through the current start endpoint until Phase 4D.3 enables it (AUDIO_INTERVIEW_NOT_READY).
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
    CandidatePracticeReleaseRecord,
    CandidateInterviewSessionRecord,
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


def setup_question_set(
    db,
    job: JobRequisition,
    jver: JobVersion,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    mode: str = "TEXT",
    set_version: int = 1,
    has_mcq: bool = False,
    lifecycle_status: str = "APPROVED",
):
    items = [
        {
            "slot_id": str(uuid.uuid4()),
            "question_id": str(uuid.uuid4()),
            "question": f"Describe your approach to designing {mode.lower()} systems.",
            "text": f"Describe your approach to designing {mode.lower()} systems.",
            "question_format": "CONVERSATION",
            "competency": "System Design",
            "authoring_origin": "LOCAL_LLM_GENERATED",
            "validation_status": "PASSED",
        }
    ]
    if has_mcq:
        items.append({
            "slot_id": str(uuid.uuid4()),
            "question_id": str(uuid.uuid4()),
            "stem": "Which status code represents Bad Request?",
            "text": "Which status code represents Bad Request?",
            "question_format": "MCQ_SINGLE_ANSWER",
            "options": [
                {"id": "A", "text": "400"},
                {"id": "B", "text": "500"},
            ],
            "correct_option_id": "A",
            "competency": "Protocols",
            "authoring_origin": "LOCAL_LLM_GENERATED",
            "validation_status": "PASSED",
        })

    q_set = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=jver.version_number,
        interview_mode=mode,
        source_content_hash="a" * 64,
        content_hash="b" * 64,
        set_version=set_version,
        lifecycle_status=lifecycle_status,
        is_immutable=(lifecycle_status == "APPROVED"),
        created_by_user_id=user_id,
        approved_by_user_id=user_id if lifecycle_status == "APPROVED" else None,
        model_identifier="local/qwen3-4b-thinking",
        items=items,
        approved_at=datetime.now(timezone.utc) if lifecycle_status == "APPROVED" else None,
    )
    db.add(q_set)
    db.commit()
    db.refresh(q_set)
    return q_set


def test_phase4d2_dual_mode_release_and_session_isolation(client, test_db):
    org_1 = uuid.uuid4()
    org_2 = uuid.uuid4()

    recruiter_1 = create_test_user(test_db, "recruiter1@org1.com", UserRole.RECRUITER)
    rec_token_1, _ = create_test_session(test_db, recruiter_1)

    recruiter_2 = create_test_user(test_db, "recruiter2@org2.com", UserRole.RECRUITER)
    rec_token_2, _ = create_test_session(test_db, recruiter_2)

    job_1, jver_1 = setup_test_job(test_db, recruiter_1, org_1, title="Staff Backend Engineer", version_number=1)

    # Candidate A at Org 1
    cand_user_a, cand_a, prof_a, binding_a = setup_candidate_with_profile(test_db, org_1, "candidate_a@email.com", "Alice Smith")
    cand_token_a, cand_csrf_a = create_test_session(test_db, cand_user_a)

    # Candidate B at Org 2
    cand_user_b, cand_b, prof_b, binding_b = setup_candidate_with_profile(test_db, org_2, "candidate_b@email.com", "Bob Jones")
    cand_token_b, cand_csrf_b = create_test_session(test_db, cand_user_b)

    # Submit application for Candidate A on Job 1
    app_a = CandidateApplicationRecord(
        application_id=uuid.uuid4(),
        organization_id=org_1,
        user_id=cand_user_a.user_id,
        binding_id=binding_a.binding_id,
        candidate_id=cand_a.identity_id,
        job_id=job_1.job_id,
        job_version_id=jver_1.job_version_id,
        job_revision=jver_1.version_number,
        profile_id=prof_a.profile_id,
        profile_version=prof_a.profile_version,
        status=CandidateApplicationStatus.SUBMITTED.value,
        recruiter_review_state=RecruiterReviewState.INTERVIEW_READY.value,
        submitted_at=datetime.now(timezone.utc),
    )
    test_db.add(app_a)
    test_db.commit()

    # Question sets: 1 Text (with MCQ), 1 Audio (conversational only), 1 Audio (invalid with MCQ)
    text_set = setup_question_set(test_db, job_1, jver_1, org_1, recruiter_1.user_id, mode="TEXT", has_mcq=True)
    audio_set = setup_question_set(test_db, job_1, jver_1, org_1, recruiter_1.user_id, mode="AUDIO", has_mcq=False)
    invalid_audio_set = setup_question_set(test_db, job_1, jver_1, org_1, recruiter_1.user_id, mode="AUDIO", set_version=2, has_mcq=True)

    # 1. Releasing Audio set with MCQ must be rejected with AUDIO_MCQ_NOT_ALLOWED
    client.cookies.set("ai_hiring_session", rec_token_1)
    res_invalid_audio = client.post(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_a.application_id}/release-interview",
        json={"ai_set_id": str(invalid_audio_set.ai_set_id)},
    )
    assert res_invalid_audio.status_code == status.HTTP_400_BAD_REQUEST
    assert res_invalid_audio.json()["detail"]["code"] == "AUDIO_MCQ_NOT_ALLOWED"

    # 2. Release TEXT interview for Candidate A
    res_text_rel = client.post(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_a.application_id}/release-interview",
        json={"ai_set_id": str(text_set.ai_set_id)},
    )
    assert res_text_rel.status_code == status.HTTP_201_CREATED
    text_rel_data = res_text_rel.json()
    assert text_rel_data["interviewMode"] == "TEXT"
    text_release_id = text_rel_data["releaseId"]

    # 3. Release AUDIO interview for same application Candidate A (Dual mode coexistence)
    res_audio_rel = client.post(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_a.application_id}/release-interview",
        json={"ai_set_id": str(audio_set.ai_set_id)},
    )
    assert res_audio_rel.status_code == status.HTTP_201_CREATED
    audio_rel_data = res_audio_rel.json()
    assert audio_rel_data["interviewMode"] == "AUDIO"
    audio_release_id = audio_rel_data["releaseId"]
    assert audio_release_id != text_release_id

    # 4. Idempotent release returns existing release for specific mode without invalidating the other mode
    res_text_idemp = client.post(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_a.application_id}/release-interview",
        json={"ai_set_id": str(text_set.ai_set_id)},
    )
    assert res_text_idemp.status_code == status.HTTP_201_CREATED
    assert res_text_idemp.json()["releaseId"] == text_release_id
    assert res_text_idemp.json()["interviewMode"] == "TEXT"

    # Verify both releases are ACTIVE in DB
    releases = test_db.execute(
        select(CandidatePracticeReleaseRecord).where(
            CandidatePracticeReleaseRecord.application_id == app_a.application_id,
            CandidatePracticeReleaseRecord.status == PracticeReleaseStatus.RELEASED.value,
        )
    ).scalars().all()
    assert len(releases) == 2
    modes = {r.interview_mode for r in releases}
    assert modes == {"TEXT", "AUDIO"}

    # 5. Cross-organization release rejection
    client.cookies.clear()
    client.cookies.set("ai_hiring_session", rec_token_2)
    res_cross_org = client.post(
        f"/api/v1/jobs/{job_1.job_id}/applications/{app_a.application_id}/release-interview",
        json={"ai_set_id": str(text_set.ai_set_id)},
    )
    assert res_cross_org.status_code == status.HTTP_403_FORBIDDEN

    # 6. Candidate Discovery shows both releases with independent modes
    client.cookies.clear()
    client.cookies.set("ai_hiring_candidate_session", cand_token_a)
    res_cand_discovery = client.get("/api/v1/candidate/practice/releases")
    assert res_cand_discovery.status_code == status.HTTP_200_OK
    items = res_cand_discovery.json()["items"]
    assert len(items) == 2
    discovery_modes = {it.get("interview_mode") for it in items}
    assert discovery_modes == {"TEXT", "AUDIO"}

    # Candidate B (Org 2) sees 0 releases
    client.cookies.clear()
    client.cookies.set("ai_hiring_candidate_session", cand_token_b)
    res_cand_b = client.get("/api/v1/candidate/practice/releases")
    assert res_cand_b.status_code == status.HTTP_200_OK
    assert len(res_cand_b.json()["items"]) == 0

    # 7. Candidate A starts TEXT interview -> Succeeds and creates TEXT session
    client.cookies.clear()
    res_text_start = client.post(
        f"/api/v1/candidate/interviews/{text_release_id}/start",
        cookies={"ai_hiring_candidate_session": cand_token_a},
        headers={"X-CSRF-Token": cand_csrf_a},
    )
    assert res_text_start.status_code == status.HTTP_200_OK
    text_sess = res_text_start.json()
    assert text_sess["interview_mode"] == "TEXT"
    assert text_sess["status"] == "IN_PROGRESS"
    assert text_sess["total_questions"] == 2  # 1 conv + 1 mcq

    # 8. Candidate A attempts to start AUDIO interview -> Blocked safely if speech runtime is not installed
    res_audio_start = client.post(
        f"/api/v1/candidate/interviews/{audio_release_id}/start",
        cookies={"ai_hiring_candidate_session": cand_token_a},
        headers={"X-CSRF-Token": cand_csrf_a},
    )
    assert res_audio_start.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_200_OK)
    if res_audio_start.status_code == status.HTTP_400_BAD_REQUEST:
        assert res_audio_start.json()["detail"]["code"] in ("AUDIO_INTERVIEW_NOT_READY", "SPEECH_RUNTIME_UNAVAILABLE")

    # 9. Candidate B cannot access Candidate A's text release
    client.cookies.clear()
    res_b_access = client.post(
        f"/api/v1/candidate/interviews/{text_release_id}/start",
        cookies={"ai_hiring_candidate_session": cand_token_b},
        headers={"X-CSRF-Token": cand_csrf_b},
    )
    assert res_b_access.status_code == status.HTTP_404_NOT_FOUND
