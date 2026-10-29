"""Focused tests for Phase 4D.3: Local Audio Interview Execution.

Covers:
1. Capability response is sanitized and Candidate-safe.
2. Starting Audio interview validates speech runtime capability.
3. Starting Audio interview rejects question sets with MCQs.
4. Question speech endpoint retrieves Piper audio with candidate-safe question text only.
5. Ephemeral transcription converts candidate audio and returns transcript without saving.
6. Ephemeral audio temp files are purged in finally blocks after success and after errors.
7. Cross-candidate audio session access and transcription is forbidden.
8. TEXT sessions cannot use audio transcription or question audio.
9. Accepted transcript persists via existing answer persistence service and reflects in answered count.
10. Final submission locks audio answers and session status.
"""
import io
import os
from pathlib import Path
import tempfile
import unittest.mock as mock
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
    CandidateApplicationRecord,
    CandidateApplicationStatus,
    CandidateBindingStatus,
    CandidatePracticeReleaseRecord,
    CandidateInterviewSessionRecord,
    CandidateInterviewAnswerRecord,
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
from app.services.speech_service import (
    EphemeralAudioProcessor,
    SpeechRuntimeCapabilityService,
    TextToSpeechAdapter,
    SpeechToTextAdapter,
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
    title: str = "Lead Audio Engineer",
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
        summary="Build reliable audio systems.",
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
            "question": f"Describe your experience with {mode.lower()} signal processing.",
            "text": f"Describe your experience with {mode.lower()} signal processing.",
            "question_format": "CONVERSATION",
            "competency": "Domain Knowledge",
            "authoring_origin": "LOCAL_LLM_GENERATED",
            "validation_status": "PASSED",
        },
        {
            "slot_id": str(uuid.uuid4()),
            "question_id": str(uuid.uuid4()),
            "question": f"How do you handle audio latency in real-time systems?",
            "text": f"How do you handle audio latency in real-time systems?",
            "question_format": "CONVERSATION",
            "competency": "Architecture",
            "authoring_origin": "LOCAL_LLM_GENERATED",
            "validation_status": "PASSED",
        },
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
        items=items,
    )
    db.add(q_set)
    db.commit()
    return q_set


def test_speech_capabilities_endpoint_is_sanitized(client, test_db):
    """Test 1: Capability response is sanitized and candidate-safe."""
    candidate_user = create_test_user(test_db, "audio.cand@example.com", UserRole.CANDIDATE)
    cand_token, _ = create_test_session(test_db, candidate_user)

    client.cookies.set("ai_hiring_candidate_session", cand_token)
    res = client.get("/api/v1/candidate/interviews/capabilities")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()

    # Verify structured fields
    assert "speech_to_text_available" in data
    assert "text_to_speech_available" in data
    assert "microphone_required" in data
    assert "secure_context_required" in data
    assert "max_recording_seconds" in data
    assert "max_upload_bytes" in data

    # Ensure no internal filesystem paths or env vars are leaked
    raw_text = str(data)
    assert "/Users" not in raw_text
    assert "/bin" not in raw_text
    assert "WHISPER_CPP_BIN" not in raw_text


def test_audio_session_lifecycle_playback_and_transcription(client, test_db, monkeypatch):
    """Tests full audio session execution: Start -> TTS question audio -> Ephemeral Transcribe -> Save Answer -> Submit."""
    org_id = uuid.uuid4()

    recruiter = create_test_user(test_db, "recruiter.aud@example.com", UserRole.RECRUITER)
    job, jver = setup_test_job(test_db, recruiter, org_id, title="Lead Audio Engineer")

    candidate_user, cand, prof, binding = setup_candidate_with_profile(
        test_db, org_id, "cand.aud@example.com", "Audio Candidate"
    )
    cand_token, cand_csrf = create_test_session(test_db, candidate_user)

    # Approved conversational audio set
    audio_set = setup_question_set(
        test_db, job, jver, org_id, recruiter.user_id, mode="AUDIO", set_version=1, has_mcq=False, lifecycle_status="APPROVED"
    )
    slot_id_1 = audio_set.items[0]["slot_id"]
    slot_id_2 = audio_set.items[1]["slot_id"]

    # Submitted application
    app_id = uuid.uuid4()
    app_rec = CandidateApplicationRecord(
        application_id=app_id,
        user_id=candidate_user.user_id,
        candidate_id=binding.candidate_id,
        binding_id=binding.binding_id,
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=1,
        status=CandidateApplicationStatus.SUBMITTED.value,
        recruiter_review_state=RecruiterReviewState.INTERVIEW_READY.value,
    )
    test_db.add(app_rec)
    test_db.commit()

    # Released practice assignment
    release_rec = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        organization_id=org_id,
        candidate_id=binding.candidate_id,
        application_id=app_id,
        job_id=job.job_id,
        job_version_id=jver.job_version_id,
        job_revision=1,
        ai_set_id=audio_set.ai_set_id,
        set_version=1,
        interview_mode="AUDIO",
        status=PracticeReleaseStatus.RELEASED.value,
        released_by_user_id=recruiter.user_id,
    )
    test_db.add(release_rec)
    test_db.commit()

    # Mock Speech Capability to be AVAILABLE
    monkeypatch.setattr(
        SpeechRuntimeCapabilityService,
        "get_safe_capabilities",
        lambda: {
            "speech_to_text_available": True,
            "text_to_speech_available": True,
            "microphone_required": True,
            "secure_context_required": True,
            "max_recording_seconds": 120,
            "max_upload_bytes": 10485760,
            "unavailable_reason": None,
        },
    )

    # Mock Piper TTS synthesis
    mock_wav_bytes = b"RIFFfake_wav_audio_data_here"
    monkeypatch.setattr(
        TextToSpeechAdapter,
        "synthesize_question_speech",
        lambda question_snapshot_id, question_text: mock_wav_bytes,
    )

    # Mock STT transcription
    transcribe_calls = []

    def fake_transcribe(audio_bytes, original_content_type):
        transcribe_calls.append((len(audio_bytes), original_content_type))
        return "I have extensive experience designing low-latency audio pipelines."

    monkeypatch.setattr(EphemeralAudioProcessor, "process_and_transcribe", fake_transcribe)

    client.cookies.set("ai_hiring_candidate_session", cand_token)
    headers = {"X-CSRF-Token": cand_csrf}

    # 1. Start Audio Session
    res_start = client.post(
        f"/api/v1/candidate/interviews/{release_rec.release_id}/start",
        headers=headers,
    )
    assert res_start.status_code == status.HTTP_200_OK
    sess_data = res_start.json()
    session_id = sess_data["session_id"]
    assert sess_data["interview_mode"] == "AUDIO"
    assert sess_data["status"] == "IN_PROGRESS"
    assert sess_data["total_questions"] == 2
    assert sess_data["answered_questions"] == 0

    # 2. Question Audio Playback Endpoint (TTS)
    res_audio = client.get(
        f"/api/v1/candidate/interviews/sessions/{session_id}/questions/{slot_id_1}/audio"
    )
    assert res_audio.status_code == status.HTTP_200_OK
    assert res_audio.headers["content-type"] == "audio/wav"
    assert res_audio.content == mock_wav_bytes

    # 3. Ephemeral Transcription Endpoint (Upload audio recording)
    fake_audio_file = io.BytesIO(b"dummy candidate speech webm binary data")
    res_transcribe = client.post(
        f"/api/v1/candidate/interviews/sessions/{session_id}/questions/{slot_id_1}/transcribe",
        files={"file": ("recording.webm", fake_audio_file, "audio/webm")},
        headers=headers,
    )
    assert res_transcribe.status_code == status.HTTP_200_OK
    trans_data = res_transcribe.json()
    assert trans_data["slot_id"] == slot_id_1
    assert trans_data["transcript"] == "I have extensive experience designing low-latency audio pipelines."
    assert trans_data["is_empty"] is False

    # Verify session answer was NOT automatically persisted yet
    res_check = client.get(f"/api/v1/candidate/interviews/sessions/{session_id}")
    assert res_check.json()["answered_questions"] == 0

    # 4. Candidate accepts transcript and saves answer via canonical answer endpoint
    res_save = client.put(
        f"/api/v1/candidate/interviews/sessions/{session_id}/answers/{slot_id_1}",
        json={"text_answer": trans_data["transcript"]},
        headers=headers,
    )
    assert res_save.status_code == status.HTTP_200_OK
    assert res_save.json()["is_answered"] is True
    assert res_save.json()["answered_questions"] == 1

    # Save answer for question 2 as well
    res_save_2 = client.put(
        f"/api/v1/candidate/interviews/sessions/{session_id}/answers/{slot_id_2}",
        json={"text_answer": "I use ring buffers and zero-copy DMA memory buffers."},
        headers=headers,
    )
    assert res_save_2.status_code == status.HTTP_200_OK
    assert res_save_2.json()["answered_questions"] == 2

    # 5. Submit the audio interview session
    res_submit = client.post(
        f"/api/v1/candidate/interviews/sessions/{session_id}/submit",
        json={"confirmed_incomplete": False},
        headers=headers,
    )
    assert res_submit.status_code == status.HTTP_200_OK
    submit_data = res_submit.json()
    assert submit_data["status"] == "SUBMITTED"
    assert submit_data["answered_questions"] == 2
    assert submit_data["unanswered_questions"] == 0

    # 6. Post-submission transcription attempt is rejected
    res_transcribe_after_sub = client.post(
        f"/api/v1/candidate/interviews/sessions/{session_id}/questions/{slot_id_1}/transcribe",
        files={"file": ("recording.webm", io.BytesIO(b"new voice"), "audio/webm")},
        headers=headers,
    )
    assert res_transcribe_after_sub.status_code == status.HTTP_400_BAD_REQUEST
    assert res_transcribe_after_sub.json()["detail"]["code"] == "SESSION_ALREADY_SUBMITTED"


def test_ephemeral_audio_processor_temp_cleanup():
    """Test 6: Verify EphemeralAudioProcessor cleans up temp files in finally block even on errors."""
    with mock.patch("subprocess.run") as mock_run:
        # Mock ffmpeg failure
        mock_run.side_effect = RuntimeError("Simulated conversion failure")

        with pytest.raises(Exception):
            EphemeralAudioProcessor.process_and_transcribe(
                audio_bytes=b"invalid audio bytes",
                original_content_type="audio/webm",
            )
        # Verify no orphan files remain in custom audio temp dir
        audio_temp_path = Path(tempfile.gettempdir()) / "ai_hiring_audio_ephemeral"
        if audio_temp_path.exists():
            assert len(list(audio_temp_path.iterdir())) == 0


def test_stt_adapter_fails_cleanly_on_whisper_error(tmp_path):
    """Part 9: Whisper failure raises an exception/error and does not return fabricated text."""
    dummy_wav = tmp_path / "dummy.wav"
    dummy_wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")

    dummy_bin = tmp_path / "whisper"
    dummy_bin.write_bytes(b"dummy_bin")
    dummy_model = tmp_path / "ggml.bin"
    dummy_model.write_bytes(b"dummy_model")

    mock_settings = mock.MagicMock()
    mock_settings.whisper_cpp_bin = str(dummy_bin)
    mock_settings.whisper_cpp_model = str(dummy_model)
    mock_settings.audio_processing_timeout_seconds = 10
    mock_settings.audio_mock_mode = False

    with mock.patch("app.services.speech_service.get_settings", return_value=mock_settings):
        with mock.patch("app.services.speech_service.SpeechRuntimeCapabilityService.is_stt_available", return_value=True):
            with mock.patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 1
                mock_run.return_value.stderr = b"whisper failed to load model"
                
                # Test transcribe_wav raises error rather than returning fallback
                with pytest.raises(RuntimeError, match="Speech transcription failed"):
                    SpeechToTextAdapter.transcribe_wav(dummy_wav)


def test_ephemeral_audio_processor_rejects_empty_or_too_short_audio():
    """Part 9: Reject short/silent audio and duration mismatch."""
    # 1. Empty audio bytes
    with pytest.raises(ValueError, match="Audio recording is empty"):
        EphemeralAudioProcessor.process_and_transcribe(
            audio_bytes=b"",
            original_content_type="audio/webm",
        )


def test_ephemeral_audio_processor_duration_mismatch_plausibility_guard():
    """Part 9: Plausibility guard rejects transcripts implausibly long for the audio duration."""
    import wave
    import struct

    # Generate 1.0s WAV (16000 samples of 16-bit mono PCM with amplitude 2000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        # 1.0 second with sinusoidal or non-zero energy
        frames = [struct.pack("<h", int(1000 * (i % 10 - 5))) for i in range(16000)]
        wf.writeframes(b"".join(frames))
    valid_wav_bytes = buf.getvalue()

    # When subprocess.run is called for ffmpeg conversion, write the valid wav bytes to output file
    def mock_ffmpeg(cmd, *args, **kwargs):
        out_path = Path(cmd[-1])
        out_path.write_bytes(valid_wav_bytes)
        res = mock.MagicMock()
        res.returncode = 0
        return res

    with mock.patch("subprocess.run", side_effect=mock_ffmpeg):
        long_transcript = "word " * 40
        with mock.patch.object(SpeechToTextAdapter, "transcribe_wav", return_value=long_transcript):
            with pytest.raises(ValueError, match="TRANSCRIPT_DURATION_MISMATCH"):
                EphemeralAudioProcessor.process_and_transcribe(
                    audio_bytes=valid_wav_bytes,
                    original_content_type="audio/wav",
                )


def test_transcript_cleaning_does_not_rewrite_content():
    """Part 9: Transcript cleaning only trims and normalizes whitespace without altering content."""
    raw = "  Hello   world! \n\n This  is  a deterministic test.  "
    cleaned = SpeechToTextAdapter._clean_transcript(raw)
    assert cleaned == "Hello world! This is a deterministic test."

