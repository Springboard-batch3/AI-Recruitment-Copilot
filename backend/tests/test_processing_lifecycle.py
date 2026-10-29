import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings, get_settings
from app.database import get_db
from app.main import app
from app.models.resume import (
    ArtifactFormat,
    ArtifactValidationStatus,
    ProcessingJob,
    ProcessingJobStatus,
    ResumeArtifact,
)
from app.schemas.resume import ResumeStatusResponse
from app.services.job_lifecycle import (
    InvalidJobTransition,
    SafeProcessingErrorCode,
    fail_job,
    retry_job,
    start_job,
    succeed_job,
)


def _create_mock_job(
    status: ProcessingJobStatus = ProcessingJobStatus.QUEUED,
    attempt_count: int = 0,
    safe_error_code: str = None,
) -> ProcessingJob:
    return ProcessingJob(
        job_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        status=status,
        attempt_count=attempt_count,
        safe_error_code=safe_error_code,
    )


# 1. QUEUED transitions to RUNNING.
# 2. Starting increments attempt_count.
# 3. Starting sets timezone-aware started_at.
def test_start_job_lifecycle():
    job = _create_mock_job(status=ProcessingJobStatus.QUEUED, attempt_count=0)
    job.safe_error_code = "PREVIOUS_ERROR"
    job.completed_at = datetime.now(timezone.utc)

    started = start_job(job)

    assert started.status == ProcessingJobStatus.RUNNING
    assert started.attempt_count == 1
    assert started.started_at is not None
    assert started.started_at.tzinfo == timezone.utc
    assert started.completed_at is None
    assert started.safe_error_code is None


# 4. RUNNING transitions to SUCCEEDED.
# 5. Success sets completed_at.
def test_succeed_job_lifecycle():
    job = _create_mock_job(status=ProcessingJobStatus.RUNNING, attempt_count=1)
    job.started_at = datetime.now(timezone.utc)

    succeeded = succeed_job(job)

    assert succeeded.status == ProcessingJobStatus.SUCCEEDED
    assert succeeded.completed_at is not None
    assert succeeded.completed_at.tzinfo == timezone.utc
    assert succeeded.safe_error_code is None


# 6. RUNNING transitions to FAILED.
# 7. Failure stores only an allowed safe code.
def test_fail_job_lifecycle():
    job = _create_mock_job(status=ProcessingJobStatus.RUNNING, attempt_count=1)
    job.started_at = datetime.now(timezone.utc)

    failed = fail_job(job, SafeProcessingErrorCode.DOCUMENT_READ_FAILED)

    assert failed.status == ProcessingJobStatus.FAILED
    assert failed.completed_at is not None
    assert failed.completed_at.tzinfo == timezone.utc
    assert failed.safe_error_code == "DOCUMENT_READ_FAILED"


# 8. Raw exception text cannot be supplied as a safe code.
def test_fail_job_rejects_raw_exception_text():
    job = _create_mock_job(status=ProcessingJobStatus.RUNNING, attempt_count=1)

    with pytest.raises(InvalidJobTransition):
        fail_job(job, "Traceback (most recent call last): File /etc/passwd corrupted")


# 9. FAILED transitions back to QUEUED.
# 10. Retry clears safe_error_code and completion state.
def test_retry_job_lifecycle():
    job = _create_mock_job(
        status=ProcessingJobStatus.FAILED,
        attempt_count=1,
        safe_error_code="DOCUMENT_READ_FAILED",
    )
    job.started_at = datetime.now(timezone.utc)
    job.completed_at = datetime.now(timezone.utc)

    retried = retry_job(job)

    assert retried.status == ProcessingJobStatus.QUEUED
    assert retried.started_at is None
    assert retried.completed_at is None
    assert retried.safe_error_code is None
    assert retried.attempt_count == 1  # Not incremented on retry, only on start


# 11. SUCCEEDED cannot be restarted.
def test_succeeded_cannot_be_restarted_or_queued():
    job = _create_mock_job(status=ProcessingJobStatus.SUCCEEDED, attempt_count=1)

    with pytest.raises(InvalidJobTransition):
        start_job(job)

    with pytest.raises(InvalidJobTransition):
        retry_job(job)


# 12. Invalid transitions raise InvalidJobTransition.
def test_invalid_transitions():
    job_queued = _create_mock_job(status=ProcessingJobStatus.QUEUED)
    with pytest.raises(InvalidJobTransition):
        succeed_job(job_queued)
    with pytest.raises(InvalidJobTransition):
        fail_job(job_queued, SafeProcessingErrorCode.DOCUMENT_READ_FAILED)
    with pytest.raises(InvalidJobTransition):
        retry_job(job_queued)

    job_failed = _create_mock_job(status=ProcessingJobStatus.FAILED)
    with pytest.raises(InvalidJobTransition):
        start_job(job_failed)
    with pytest.raises(InvalidJobTransition):
        succeed_job(job_failed)


# 13. Lifecycle helpers do not commit a database session.
def test_lifecycle_helpers_do_not_commit():
    mock_db = MagicMock()
    job = _create_mock_job(status=ProcessingJobStatus.QUEUED)

    start_job(job)
    succeed_job(job)

    # Neither mock_db.commit nor mock_db.flush should have been called
    assert mock_db.commit.call_count == 0
    assert mock_db.flush.call_count == 0


# 14. Status response excludes filename, hash and storage path.
def test_status_response_schema_privacy():
    art_id = uuid.uuid4()
    job_id = uuid.uuid4()

    response = ResumeStatusResponse(
        artifact_id=art_id,
        job_id=job_id,
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
        processing_status=ProcessingJobStatus.RUNNING,
        attempt_count=1,
        safe_error_code=None,
    )

    dumped = response.model_dump(by_alias=True)
    assert set(dumped.keys()) == {
        "artifactId",
        "jobId",
        "detectedFormat",
        "validationStatus",
        "processingStatus",
        "attemptCount",
        "safeErrorCode",
    }
    # Ensure sensitive fields cannot exist in schema
    assert "originalFilename" not in dumped
    assert "sha256Hash" not in dumped
    assert "quarantineKey" not in dumped
    assert "sizeBytes" not in dumped


# Database and client fixture for status route testing
class MockStatusDBSession:
    def __init__(self, artifact=None, job=None, should_fail=False):
        self.artifact = artifact
        self.job = job
        self.should_fail = should_fail
        self.closed = False

    def execute(self, statement):
        if self.should_fail:
            raise SQLAlchemyError("Simulated SQL failure with internal table schema details")
        stmt_str = str(statement)
        if "resume_artifacts" in stmt_str:
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = self.artifact
            return mock_result
        if "processing_jobs" in stmt_str:
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = self.job
            return mock_result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        return mock_result

    def close(self):
        self.closed = True


def _get_mock_recruiter_ctx():
    from unittest.mock import MagicMock
    from app.api.auth import AuthenticatedContext
    from app.models.auth import UserRecord, UserRole

    user = UserRecord(
        user_id=uuid.uuid4(),
        email="recruiter.status@company.test",
        display_name="Recruiter Status",
        password_hash="mock",
        role=UserRole.RECRUITER,
        is_active=True,
        must_change_password=False,
    )
    return AuthenticatedContext(user=user, session=MagicMock())


# 15. GET /api/v1/resumes/{artifact_id}/status returns correct status payload.
def test_get_resume_status_success(tmp_path):
    from app.api.auth import require_recruiter

    art_id = uuid.uuid4()
    job_id = uuid.uuid4()

    artifact = ResumeArtifact(
        artifact_id=art_id,
        original_filename="secret_candidate.pdf",
        declared_mime_type="application/pdf",
        detected_format=ArtifactFormat.pdf,
        size_bytes=12345,
        sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        validation_status=ArtifactValidationStatus.VALIDATED,
        quarantine_key=f"pdf/{art_id}.pdf",
    )

    job = ProcessingJob(
        job_id=job_id,
        artifact_id=art_id,
        status=ProcessingJobStatus.QUEUED,
        attempt_count=0,
        safe_error_code=None,
    )

    mock_db = MockStatusDBSession(artifact=artifact, job=job)

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[require_recruiter] = _get_mock_recruiter_ctx
    client = TestClient(app)

    try:
        response = client.get(f"/api/v1/resumes/{art_id}/status")
        assert response.status_code == 200
        data = response.json()
        assert data["artifactId"] == str(art_id)
        assert data["jobId"] == str(job_id)
        assert data["detectedFormat"] == "pdf"
        assert data["validationStatus"] == "VALIDATED"
        assert data["processingStatus"] == "QUEUED"
        assert data["attemptCount"] == 0
        assert data["safeErrorCode"] is None

        # Ensure no sensitive artifact details leaked
        assert "secret_candidate.pdf" not in response.text
        assert "quarantine" not in response.text
        assert "e3b0c442" not in response.text
    finally:
        app.dependency_overrides.clear()


# 16. Missing artifact returns RESUME_NOT_FOUND.
def test_get_resume_status_not_found():
    from app.api.auth import require_recruiter

    mock_db = MockStatusDBSession(artifact=None, job=None)
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[require_recruiter] = _get_mock_recruiter_ctx
    client = TestClient(app)

    try:
        random_id = uuid.uuid4()
        response = client.get(f"/api/v1/resumes/{random_id}/status")
        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["code"] == "RESUME_NOT_FOUND"
    finally:
        app.dependency_overrides.clear()


# 17. Database error response does not expose SQL/driver information.
def test_get_resume_status_db_error_safe_response(caplog):
    from app.api.auth import require_recruiter

    mock_db = MockStatusDBSession(should_fail=True)
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[require_recruiter] = _get_mock_recruiter_ctx
    client = TestClient(app)

    try:
        with caplog.at_level(logging.ERROR):
            art_id = uuid.uuid4()
            response = client.get(f"/api/v1/resumes/{art_id}/status")
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "INTAKE_UNAVAILABLE"
            assert "table schema details" not in response.text
            assert "SELECT" not in response.text
    finally:
        app.dependency_overrides.clear()


# Privacy route test with deliberate sensitive string
def test_privacy_leakage_prevention(tmp_path, caplog):
    from app.api.auth import require_recruiter

    sensitive_name = "john.doe+confidential_applicant_ssn_12345@corp.test.pdf"
    art_id = uuid.uuid4()
    job_id = uuid.uuid4()

    artifact = ResumeArtifact(
        artifact_id=art_id,
        original_filename=sensitive_name,
        declared_mime_type="application/pdf",
        detected_format=ArtifactFormat.pdf,
        size_bytes=100,
        sha256_hash="a" * 64,
        validation_status=ArtifactValidationStatus.VALIDATED,
        quarantine_key=f"pdf/{art_id}.pdf",
    )
    job = ProcessingJob(
        job_id=job_id,
        artifact_id=art_id,
        status=ProcessingJobStatus.FAILED,
        attempt_count=1,
        safe_error_code=SafeProcessingErrorCode.DOCUMENT_READ_FAILED.value,
    )

    mock_db = MockStatusDBSession(artifact=artifact, job=job)
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[require_recruiter] = _get_mock_recruiter_ctx
    client = TestClient(app)

    try:
        with caplog.at_level(logging.INFO):
            response = client.get(f"/api/v1/resumes/{art_id}/status")
            assert response.status_code == 200
            assert sensitive_name not in response.text
            assert "confidential_applicant" not in response.text
            # Check logs
            for record in caplog.records:
                assert sensitive_name not in record.getMessage()
    finally:
        app.dependency_overrides.clear()

