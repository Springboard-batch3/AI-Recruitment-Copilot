import hashlib
import io
import os
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pypdf
from pypdf import PdfWriter
from docx import Document
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.models import Base
from app.models.extraction import BlockType, ExtractionBlock, ExtractionMethod, ResumeExtraction
from app.models.resume import (
    ArtifactFormat,
    ArtifactValidationStatus,
    ProcessingJob,
    ProcessingJobStatus,
    ResumeArtifact,
)
from app.schemas.extraction import ExtractionBlockData, ExtractionResult
from app.services.extraction_orchestration import (
    DirectTextUnavailableOrchestrationError,
    ExtractionStateConflictError,
    JobAlreadyRunningError,
    OrchestrationError,
    OrchestrationSuccess,
    ResumeNotFoundError,
    orchestrate_resume_extraction,
)
from app.services.job_lifecycle import SafeProcessingErrorCode


@pytest.fixture
def db_session():
    """In-memory SQLite database session for orchestration testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _setup_artifact_and_job(
    session,
    tmp_path: Path,
    file_bytes: bytes,
    detected_format: ArtifactFormat = ArtifactFormat.pdf,
    job_status: ProcessingJobStatus = ProcessingJobStatus.QUEUED,
    validation_status: ArtifactValidationStatus = ArtifactValidationStatus.VALIDATED,
) -> tuple[ResumeArtifact, ProcessingJob, Settings]:
    quarantine_dir = tmp_path / "quarantine"
    fmt_dir = quarantine_dir / detected_format.value
    fmt_dir.mkdir(parents=True, exist_ok=True)

    art_id = uuid.uuid4()
    job_id = uuid.uuid4()
    ext = "pdf" if detected_format == ArtifactFormat.pdf else "docx"
    file_path = fmt_dir / f"{art_id}.{ext}"
    file_path.write_bytes(file_bytes)

    actual_size = len(file_bytes)
    actual_hash = hashlib.sha256(file_bytes).hexdigest().lower()

    artifact = ResumeArtifact(
        artifact_id=art_id,
        original_filename=f"resume.{ext}",
        declared_mime_type="application/pdf" if ext == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        detected_format=detected_format,
        size_bytes=actual_size,
        sha256_hash=actual_hash,
        validation_status=validation_status,
        quarantine_key=f"{ext}/{art_id}.{ext}",
    )
    job = ProcessingJob(
        job_id=job_id,
        artifact_id=art_id,
        status=job_status,
        attempt_count=0 if job_status == ProcessingJobStatus.QUEUED else 1,
    )

    session.add(artifact)
    session.add(job)
    session.commit()

    settings = Settings(
        database_url="sqlite:///:memory:",
        resume_quarantine_directory=str(quarantine_dir),
    )

    return artifact, job, settings


# =========================================================================
# 1. QUEUED job processes successfully to SUCCEEDED
# =========================================================================

def test_queued_job_processes_successfully(db_session, tmp_path):
    # Create simple valid PDF bytes
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)

    # PDF with real text
    doc = Document()
    doc.add_heading("Jane Doe Resume", level=1)
    doc.add_paragraph("Senior Python Engineer with cloud experience.")
    buf_docx = io.BytesIO()
    doc.save(buf_docx)
    docx_bytes = buf_docx.getvalue()

    artifact, job, settings = _setup_artifact_and_job(
        db_session,
        tmp_path,
        docx_bytes,
        detected_format=ArtifactFormat.docx,
        job_status=ProcessingJobStatus.QUEUED,
    )

    outcome = orchestrate_resume_extraction(
        artifact_id=artifact.artifact_id,
        db=db_session,
        settings=settings,
    )

    assert isinstance(outcome, OrchestrationSuccess)
    assert outcome.artifact_id == artifact.artifact_id
    assert outcome.processing_status == ProcessingJobStatus.SUCCEEDED
    assert outcome.block_count == 2
    assert outcome.review_required is False

    # Check DB state
    db_session.refresh(job)
    assert job.status == ProcessingJobStatus.SUCCEEDED
    assert job.attempt_count == 1
    assert job.completed_at is not None
    assert job.safe_error_code is None

    # Check ResumeExtraction in DB
    ext = db_session.execute(
        select(ResumeExtraction).where(ResumeExtraction.artifact_id == artifact.artifact_id)
    ).scalar_one()
    assert ext.extractor_version == "docx-structural-text-v1.0+normalize-v1"
    assert len(ext.blocks) == 2
    assert ext.blocks[0].sequence_index == 0
    assert ext.blocks[0].normalized_text == "Jane Doe Resume"
    assert ext.blocks[1].sequence_index == 1
    assert ext.blocks[1].normalized_text == "Senior Python Engineer with cloud experience."


# =========================================================================
# 2. RUNNING job cannot be processed twice (409 Conflict)
# =========================================================================

def test_running_job_raises_job_already_running(db_session, tmp_path):
    artifact, job, settings = _setup_artifact_and_job(
        db_session,
        tmp_path,
        b"dummy",
        detected_format=ArtifactFormat.pdf,
        job_status=ProcessingJobStatus.RUNNING,
    )

    with pytest.raises(JobAlreadyRunningError) as exc:
        orchestrate_resume_extraction(artifact.artifact_id, db_session, settings)
    assert exc.value.status_code == 409
    assert exc.value.code == "JOB_ALREADY_RUNNING"


# =========================================================================
# 3. SUCCEEDED processing request is idempotent
# =========================================================================

def test_succeeded_job_returns_idempotent_result(db_session, tmp_path):
    doc = Document()
    doc.add_paragraph("Existing Success")
    buf = io.BytesIO()
    doc.save(buf)
    docx_bytes = buf.getvalue()

    artifact, job, settings = _setup_artifact_and_job(
        db_session,
        tmp_path,
        docx_bytes,
        detected_format=ArtifactFormat.docx,
        job_status=ProcessingJobStatus.QUEUED,
    )

    # First call -> SUCCEEDED
    outcome1 = orchestrate_resume_extraction(artifact.artifact_id, db_session, settings)
    assert outcome1.processing_status == ProcessingJobStatus.SUCCEEDED

    # Second call -> Returns identical summary without re-extracting
    outcome2 = orchestrate_resume_extraction(artifact.artifact_id, db_session, settings)
    assert outcome2.extraction_id == outcome1.extraction_id
    assert outcome2.block_count == outcome1.block_count
    assert outcome2.processing_status == ProcessingJobStatus.SUCCEEDED


# =========================================================================
# 4. FAILED job can retry safely
# =========================================================================

def test_failed_job_retries_safely(db_session, tmp_path):
    doc = Document()
    doc.add_paragraph("Retry Success Paragraph")
    buf = io.BytesIO()
    doc.save(buf)
    docx_bytes = buf.getvalue()

    artifact, job, settings = _setup_artifact_and_job(
        db_session,
        tmp_path,
        docx_bytes,
        detected_format=ArtifactFormat.docx,
        job_status=ProcessingJobStatus.FAILED,
    )
    job.safe_error_code = SafeProcessingErrorCode.DOCUMENT_READ_FAILED.value
    job.attempt_count = 1
    db_session.commit()

    outcome = orchestrate_resume_extraction(artifact.artifact_id, db_session, settings)

    assert outcome.processing_status == ProcessingJobStatus.SUCCEEDED
    db_session.refresh(job)
    assert job.status == ProcessingJobStatus.SUCCEEDED
    assert job.attempt_count == 2
    assert job.safe_error_code is None


# =========================================================================
# 5. Direct text unavailable maps to controlled failure and safe error code
# =========================================================================

def test_direct_text_unavailable_marks_job_failed(db_session, tmp_path):
    # Empty docx without text
    doc = Document()
    buf = io.BytesIO()
    doc.save(buf)
    docx_bytes = buf.getvalue()

    artifact, job, settings = _setup_artifact_and_job(
        db_session,
        tmp_path,
        docx_bytes,
        detected_format=ArtifactFormat.docx,
        job_status=ProcessingJobStatus.QUEUED,
    )

    with pytest.raises(DirectTextUnavailableOrchestrationError) as exc:
        orchestrate_resume_extraction(artifact.artifact_id, db_session, settings)

    assert exc.value.status_code == 422
    assert exc.value.code == "DIRECT_TEXT_UNAVAILABLE"

    # Verify job in DB is marked FAILED with controlled code
    db_session.refresh(job)
    assert job.status == ProcessingJobStatus.FAILED
    assert job.safe_error_code == SafeProcessingErrorCode.DIRECT_TEXT_UNAVAILABLE.value
    assert job.completed_at is not None

    # No extraction blocks created
    ext_count = db_session.execute(
        select(ResumeExtraction).where(ResumeExtraction.artifact_id == artifact.artifact_id)
    ).scalar_one_or_none()
    assert ext_count is None


# =========================================================================
# 6. Non-existent artifact raises 404 RESUME_NOT_FOUND
# =========================================================================

def test_nonexistent_artifact_raises_not_found(db_session, tmp_path):
    settings = Settings(database_url="sqlite:///:memory:")
    with pytest.raises(ResumeNotFoundError) as exc:
        orchestrate_resume_extraction(uuid.uuid4(), db_session, settings)
    assert exc.value.status_code == 404
    assert exc.value.code == "RESUME_NOT_FOUND"


# =========================================================================
# 7. Unvalidated artifact raises 422
# =========================================================================

def test_unvalidated_artifact_rejected(db_session, tmp_path):
    artifact, job, settings = _setup_artifact_and_job(
        db_session,
        tmp_path,
        b"dummy",
        detected_format=ArtifactFormat.pdf,
        job_status=ProcessingJobStatus.QUEUED,
        validation_status=ArtifactValidationStatus.PENDING,
    )

    with pytest.raises(OrchestrationError) as exc:
        orchestrate_resume_extraction(artifact.artifact_id, db_session, settings)
    assert exc.value.status_code == 422
    assert exc.value.code == "ARTIFACT_NOT_VALIDATED"


# =========================================================================
# 8. POST /process endpoint tests
# =========================================================================

@pytest.fixture
def api_client(tmp_path, db_session):
    quarantine_dir = tmp_path / "quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        database_url="sqlite:///:memory:",
        resume_quarantine_directory=str(quarantine_dir),
    )

    def override_settings():
        return settings

    def override_db():
        try:
            yield db_session
        finally:
            pass

    from app.main import app
    from fastapi.testclient import TestClient
    from unittest.mock import MagicMock
    from app.api.auth import AuthenticatedContext, require_recruiter
    from app.models.auth import UserRecord, UserRole
    from app.config import get_settings
    from app.database import get_db

    mock_recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email="mock.recruiter@company.test",
        display_name="Mock Recruiter",
        password_hash="mock",
        role=UserRole.RECRUITER,
        is_active=True,
        must_change_password=False,
    )
    mock_ctx = AuthenticatedContext(user=mock_recruiter, session=MagicMock())

    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_recruiter] = lambda: mock_ctx

    client = TestClient(app)
    yield client, db_session, settings, quarantine_dir
    app.dependency_overrides.clear()

    app.dependency_overrides.clear()


def test_process_resume_endpoint_success(api_client):
    client, db, settings, quarantine_dir = api_client

    # Create DOCX in quarantine
    doc = Document()
    doc.add_heading("Jane Doe", level=1)
    doc.add_paragraph("Senior Engineer with Python experience.")
    buf = io.BytesIO()
    doc.save(buf)
    docx_bytes = buf.getvalue()

    docx_dir = quarantine_dir / "docx"
    docx_dir.mkdir(parents=True, exist_ok=True)

    art_id = uuid.uuid4()
    job_id = uuid.uuid4()
    file_path = docx_dir / f"{art_id}.docx"
    file_path.write_bytes(docx_bytes)

    h = hashlib.sha256(docx_bytes).hexdigest().lower()

    artifact = ResumeArtifact(
        artifact_id=art_id,
        original_filename="resume.docx",
        declared_mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        detected_format=ArtifactFormat.docx,
        size_bytes=len(docx_bytes),
        sha256_hash=h,
        validation_status=ArtifactValidationStatus.VALIDATED,
        quarantine_key=f"docx/{art_id}.docx",
    )
    job = ProcessingJob(
        job_id=job_id,
        artifact_id=art_id,
        status=ProcessingJobStatus.QUEUED,
        attempt_count=0,
    )
    db.add(artifact)
    db.add(job)
    db.commit()

    # Call POST /api/v1/resumes/{artifact_id}/process
    response = client.post(f"/api/v1/resumes/{art_id}/process")

    assert response.status_code == 200
    data = response.json()
    assert data["artifactId"] == str(art_id)
    assert data["jobId"] == str(job_id)
    assert "extractionId" in data
    assert data["processingStatus"] == "SUCCEEDED"
    assert data["reviewRequired"] is False
    assert data["blockCount"] == 2


def test_process_resume_endpoint_404(api_client):
    client, _, _, _ = api_client
    random_id = uuid.uuid4()
    response = client.post(f"/api/v1/resumes/{random_id}/process")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "RESUME_NOT_FOUND"


# =========================================================================
# 9. GET /extraction endpoint tests
# =========================================================================

def test_get_extraction_endpoint_success(api_client):
    client, db, _, _ = api_client

    art_id = uuid.uuid4()
    ext_id = uuid.uuid4()
    b0_id = uuid.uuid4()
    b1_id = uuid.uuid4()

    artifact = ResumeArtifact(
        artifact_id=art_id,
        original_filename="resume.pdf",
        declared_mime_type="application/pdf",
        detected_format=ArtifactFormat.pdf,
        size_bytes=100,
        sha256_hash="a" * 64,
        validation_status=ArtifactValidationStatus.VALIDATED,
        quarantine_key=f"pdf/{art_id}.pdf",
    )
    extraction = ResumeExtraction(
        extraction_id=ext_id,
        artifact_id=art_id,
        extractor_version="pdf-direct-text-v1.0+normalize-v1",
        review_required=False,
    )
    b0 = ExtractionBlock(
        block_id=b0_id,
        extraction_id=ext_id,
        sequence_index=0,
        page_number=1,
        structural_location=None,
        block_type=BlockType.HEADING,
        raw_text="Jane Doe",
        normalized_text="Jane Doe",
        extraction_method=ExtractionMethod.PDF_TEXT,
        confidence=Decimal("1.0"),
        review_required=False,
    )
    b1 = ExtractionBlock(
        block_id=b1_id,
        extraction_id=ext_id,
        sequence_index=1,
        page_number=1,
        structural_location=None,
        block_type=BlockType.PARAGRAPH,
        raw_text="Developer with 5 years experience.",
        normalized_text="Developer with 5 years experience.",
        extraction_method=ExtractionMethod.PDF_TEXT,
        confidence=Decimal("0.95"),
        review_required=False,
    )

    db.add(artifact)
    db.add(extraction)
    db.add(b0)
    db.add(b1)
    db.commit()

    response = client.get(f"/api/v1/resumes/{art_id}/extraction")

    assert response.status_code == 200
    data = response.json()
    assert data["artifactId"] == str(art_id)
    assert data["extractionId"] == str(ext_id)
    assert data["extractorVersion"] == "pdf-direct-text-v1.0+normalize-v1"
    assert data["reviewRequired"] is False
    assert data["blockCount"] == 2
    assert len(data["blocks"]) == 2

    # Block 0
    b0_data = data["blocks"][0]
    assert b0_data["blockId"] == str(b0_id)
    assert b0_data["sequenceIndex"] == 0
    assert b0_data["pageNumber"] == 1
    assert b0_data["blockType"] == "HEADING"
    assert b0_data["rawText"] == "Jane Doe"
    assert b0_data["normalizedText"] == "Jane Doe"

    # Block 1
    b1_data = data["blocks"][1]
    assert b1_data["blockId"] == str(b1_id)
    assert b1_data["sequenceIndex"] == 1
    assert b1_data["pageNumber"] == 1
    assert b1_data["blockType"] == "PARAGRAPH"


def test_get_extraction_endpoint_404(api_client):
    client, _, _, _ = api_client
    random_id = uuid.uuid4()
    response = client.get(f"/api/v1/resumes/{random_id}/extraction")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EXTRACTION_NOT_FOUND"


# =========================================================================
# 10. GET /status endpoint excludes extraction text
# =========================================================================

def test_status_endpoint_never_includes_extraction_text(api_client):
    client, db, _, _ = api_client

    art_id = uuid.uuid4()
    job_id = uuid.uuid4()
    artifact = ResumeArtifact(
        artifact_id=art_id,
        original_filename="secret_resume.pdf",
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
        status=ProcessingJobStatus.SUCCEEDED,
        attempt_count=1,
    )
    db.add(artifact)
    db.add(job)
    db.commit()

    response = client.get(f"/api/v1/resumes/{art_id}/status")

    assert response.status_code == 200
    data = response.json()
    assert "blocks" not in data
    assert "rawText" not in data
    assert "normalizedText" not in data
    assert data["processingStatus"] == "SUCCEEDED"
