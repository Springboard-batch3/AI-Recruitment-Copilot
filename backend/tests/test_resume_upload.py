import hashlib
import io
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy.exc import SQLAlchemyError

import zipfile

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
from app.schemas.resume import ResumeUploadResponse


def _create_valid_pdf_bytes(page_count: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


MINIMAL_DOCX_CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

MINIMAL_DOCX_RELS = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

MINIMAL_DOCX_DOCUMENT_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Confidential Candidate Resume</w:t></w:r></w:p>
  </w:body>
</w:document>
"""


def _create_valid_docx_bytes(extra_files: dict[str, bytes] = None) -> bytes:
    buf = io.BytesIO()
    files = {
        "[Content_Types].xml": MINIMAL_DOCX_CONTENT_TYPES,
        "_rels/.rels": MINIMAL_DOCX_RELS,
        "word/document.xml": MINIMAL_DOCX_DOCUMENT_XML,
    }
    if extra_files:
        files.update(extra_files)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class MockDBSession:
    def __init__(self, should_fail_commit: bool = False):
        self.added_records = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.should_fail_commit = should_fail_commit

    def add(self, instance):
        self.added_records.append(instance)

    def flush(self):
        if self.should_fail_commit:
            raise SQLAlchemyError("Simulated database failure")

    def commit(self):
        if self.should_fail_commit:
            raise SQLAlchemyError("Simulated database failure")
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True

    def execute(self, statement, *args, **kwargs):
        class MockResult:
            def __init__(self, records):
                self._records = records

            def scalars(self):
                return self

            def all(self):
                return self._records

            def first(self):
                return self._records[0] if self._records else None

            def scalar_one_or_none(self):
                return self._records[0] if self._records else None

        from app.models.resume import ResumeArtifact
        from app.models.candidate import CandidateProfileRecord
        from app.models.candidate_access import CandidateApplicationRecord

        stmt_str = str(statement).lower()
        if "candidate_profiles" in stmt_str:
            records = [r for r in self.added_records if isinstance(r, CandidateProfileRecord)]
        elif "candidate_applications" in stmt_str:
            records = [r for r in self.added_records if isinstance(r, CandidateApplicationRecord)]
        else:
            records = [r for r in self.added_records if isinstance(r, ResumeArtifact)]
        return MockResult(records)


@pytest.fixture
def mock_db_session():
    return MockDBSession()


@pytest.fixture
def test_client(tmp_path, mock_db_session):
    intake_dir = tmp_path / "intake"
    quarantine_dir = tmp_path / "quarantine"

    test_settings = Settings(
        database_url="postgresql+psycopg://test:test@localhost:5432/test_db",
        max_resume_upload_bytes=1024 * 1024,  # 1 MiB for tests
        max_resume_pages=5,
        resume_intake_directory=str(intake_dir),
        resume_quarantine_directory=str(quarantine_dir),
    )

    def override_get_settings():
        return test_settings

    def override_get_db():
        try:
            yield mock_db_session
        finally:
            mock_db_session.close()

    from app.api.auth import AuthenticatedContext, require_recruiter
    from app.models.auth import UserRecord, UserRole

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

    app.dependency_overrides[get_settings] = override_get_settings
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_recruiter] = lambda: mock_ctx

    client = TestClient(app)
    yield client, intake_dir, quarantine_dir, mock_db_session, test_settings

    app.dependency_overrides.clear()


# 1. A small .pdf upload with application/pdf returns 202.
# 13. A valid PDF returns 202.
# 14. Its artifact has detected_format PDF.
# 15. Its validation status is VALIDATED.
# 16. Its job remains QUEUED.
# 17. The intake file was moved to quarantine/pdf.
# 18. The database key is relative and opaque.
# 19. The physical name does not contain the original filename.
# 20. No intake copy remains after success.
def test_upload_pdf_success_and_quarantine(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    content = _create_valid_pdf_bytes(1)
    expected_hash = hashlib.sha256(content).hexdigest().lower()
    orig_filename = "candidate_resume_confidential.pdf"

    response = client.post(
        "/api/v1/resumes",
        files={"file": (orig_filename, io.BytesIO(content), "application/pdf")},
    )

    assert response.status_code == 202
    data = response.json()
    assert "artifactId" in data
    assert "jobId" in data
    assert data["status"] == "QUEUED"

    art_id_str = data["artifactId"]
    job_id_str = data["jobId"]

    # Intake directory should have no lingering file
    if intake_dir.exists():
        assert len(list(intake_dir.iterdir())) == 0

    # Quarantine directory should contain the moved file
    quarantine_pdf_dir = quarantine_dir / "pdf"
    assert quarantine_pdf_dir.exists()
    quarantined_files = list(quarantine_pdf_dir.iterdir())
    assert len(quarantined_files) == 1
    quarantined_file = quarantined_files[0]

    assert quarantined_file.name == f"{art_id_str}.pdf"
    assert orig_filename not in quarantined_file.name
    assert quarantined_file.read_bytes() == content

    # Database records verification
    assert len(db_session.added_records) == 2
    artifact = next(r for r in db_session.added_records if isinstance(r, ResumeArtifact))
    job = next(r for r in db_session.added_records if isinstance(r, ProcessingJob))

    assert str(artifact.artifact_id) == art_id_str
    assert artifact.original_filename == orig_filename
    assert artifact.declared_mime_type == "application/pdf"
    assert artifact.detected_format == ArtifactFormat.pdf
    assert artifact.validation_status == ArtifactValidationStatus.VALIDATED
    assert artifact.size_bytes == len(content)
    assert artifact.sha256_hash == expected_hash
    assert artifact.quarantine_key == f"pdf/{art_id_str}.pdf"

    assert str(job.job_id) == job_id_str
    assert str(job.artifact_id) == art_id_str
    assert job.status == ProcessingJobStatus.QUEUED
    assert job.attempt_count == 0

    assert db_session.committed is True
    assert db_session.closed is True


# 2. A small .docx upload with the official DOCX MIME returns 202.
# 27. Valid DOCX returns 202.
# 28. Artifact format becomes DOCX.
# 29. Artifact status becomes VALIDATED.
# 30. Job remains QUEUED.
# 31. Intake copy is removed.
# 32. Opaque file appears under quarantine/docx.
def test_upload_docx_success_and_quarantine(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    docx_bytes = _create_valid_docx_bytes()
    expected_hash = hashlib.sha256(docx_bytes).hexdigest().lower()
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    orig_name = "candidate_resume_confidential.docx"

    response = client.post(
        "/api/v1/resumes",
        files={"file": (orig_name, io.BytesIO(docx_bytes), docx_mime)},
    )

    assert response.status_code == 202
    data = response.json()
    assert "artifactId" in data
    assert "jobId" in data
    assert data["status"] == "QUEUED"

    art_id_str = data["artifactId"]
    job_id_str = data["jobId"]

    # Intake directory should have no lingering file
    if intake_dir.exists():
        assert len(list(intake_dir.iterdir())) == 0

    # Quarantine directory should contain the moved file under docx/
    quarantine_docx_dir = quarantine_dir / "docx"
    assert quarantine_docx_dir.exists()
    quarantined_files = list(quarantine_docx_dir.iterdir())
    assert len(quarantined_files) == 1
    quarantined_file = quarantined_files[0]

    assert quarantined_file.name == f"{art_id_str}.docx"
    assert orig_name not in quarantined_file.name
    assert quarantined_file.read_bytes() == docx_bytes

    # Database records verification
    assert len(db_session.added_records) == 2
    artifact = next(r for r in db_session.added_records if isinstance(r, ResumeArtifact))
    job = next(r for r in db_session.added_records if isinstance(r, ProcessingJob))

    assert str(artifact.artifact_id) == art_id_str
    assert artifact.original_filename == orig_name
    assert artifact.declared_mime_type == docx_mime
    assert artifact.detected_format == ArtifactFormat.docx
    assert artifact.validation_status == ArtifactValidationStatus.VALIDATED
    assert artifact.size_bytes == len(docx_bytes)
    assert artifact.sha256_hash == expected_hash
    assert artifact.quarantine_key == f"docx/{art_id_str}.docx"

    assert str(job.job_id) == job_id_str
    assert str(job.artifact_id) == art_id_str
    assert job.status == ProcessingJobStatus.QUEUED
    assert job.attempt_count == 0

    assert db_session.committed is True
    assert db_session.closed is True


# 33. Invalid DOCX leaves no file or database records.
def test_invalid_signature_docx_returns_415(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    fake_content = b"NOT_A_ZIP_HEADER"
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("fake.docx", io.BytesIO(fake_content), docx_mime)},
    )

    assert response.status_code == 415
    detail = response.json().get("detail", {})
    assert detail.get("code") == "INVALID_DOCX_SIGNATURE"
    if intake_dir.exists():
        assert list(intake_dir.iterdir()) == []
    if quarantine_dir.exists():
        assert list(quarantine_dir.rglob("*.*")) == []
    assert len(db_session.added_records) == 0


def test_unsafe_content_docx_returns_422(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    unsafe_content = _create_valid_docx_bytes(extra_files={"word/vbaProject.bin": b"VBA_BINARY"})
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("macro.docx", io.BytesIO(unsafe_content), docx_mime)},
    )

    assert response.status_code == 422
    detail = response.json().get("detail", {})
    assert detail.get("code") == "UNSAFE_DOCX_CONTENT"
    if intake_dir.exists():
        assert list(intake_dir.iterdir()) == []
    if quarantine_dir.exists():
        assert list(quarantine_dir.rglob("*.*")) == []
    assert len(db_session.added_records) == 0


# 34. Database failure removes the quarantined DOCX.
def test_db_failure_deletes_quarantined_docx(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    db_session.should_fail_commit = True
    docx_bytes = _create_valid_docx_bytes()
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("resume.docx", io.BytesIO(docx_bytes), docx_mime)},
    )

    assert response.status_code == 503
    detail = response.json().get("detail", {})
    assert detail.get("code") == "INTAKE_UNAVAILABLE"
    assert db_session.rolled_back is True
    if intake_dir.exists():
        assert list(intake_dir.iterdir()) == []
    if quarantine_dir.exists():
        assert list(quarantine_dir.rglob("*.*")) == []


# 3. Successful JSON contains only artifactId, jobId and status.
# 4. status equals QUEUED.
def test_response_payload_contract(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    content = _create_valid_pdf_bytes(1)

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("sample.pdf", io.BytesIO(content), "application/pdf")},
    )

    assert response.status_code == 202
    data = response.json()
    assert set(data.keys()) == {"artifactId", "jobId", "status"}
    assert data["status"] == "QUEUED"
    uuid.UUID(data["artifactId"])
    uuid.UUID(data["jobId"])


# 12. Unsupported extension returns 415.
def test_unsupported_extension_returns_415(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    content = b"plain text resume"

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("resume.txt", io.BytesIO(content), "text/plain")},
    )

    assert response.status_code == 415
    detail = response.json().get("detail", {})
    assert detail.get("code") == "UNSUPPORTED_FILE_TYPE"
    if intake_dir.exists():
        assert len(list(intake_dir.iterdir())) == 0


# 13. Extension/MIME mismatch returns 415.
def test_extension_mime_mismatch_returns_415(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    content = _create_valid_pdf_bytes(1)

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("resume.pdf", io.BytesIO(content), "text/html")},
    )

    assert response.status_code == 415
    detail = response.json().get("detail", {})
    assert detail.get("code") == "FILE_TYPE_MISMATCH"
    if intake_dir.exists():
        assert len(list(intake_dir.iterdir())) == 0


# 14. Empty file returns 400.
def test_empty_file_returns_400(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    content = b""

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("empty_resume.pdf", io.BytesIO(content), "application/pdf")},
    )

    assert response.status_code == 400
    detail = response.json().get("detail", {})
    assert detail.get("code") == "EMPTY_FILE"
    if intake_dir.exists():
        assert len(list(intake_dir.iterdir())) == 0


# 15. Oversized upload returns 413.
# 16. Oversized partial file is deleted.
def test_oversized_file_returns_413_and_cleans_up(test_client):
    client, intake_dir, quarantine_dir, db_session, test_settings = test_client
    oversized_content = b"0" * (1024 * 1024 + 512 * 1024)

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("big_resume.pdf", io.BytesIO(oversized_content), "application/pdf")},
    )

    assert response.status_code == 413
    detail = response.json().get("detail", {})
    assert detail.get("code") == "FILE_TOO_LARGE"
    if intake_dir.exists():
        assert list(intake_dir.iterdir()) == []
    assert len(db_session.added_records) == 0


# 21. Invalid-signature PDF returns 415.
# 26. Every rejected PDF leaves no intake/quarantine file.
# 27. Every rejected PDF creates no artifact or job.
def test_invalid_signature_pdf_returns_415(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    fake_content = b"NOT_A_PDF_BUT_CLAIMING_IT"

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("fake.pdf", io.BytesIO(fake_content), "application/pdf")},
    )

    assert response.status_code == 415
    detail = response.json().get("detail", {})
    assert detail.get("code") == "INVALID_PDF_SIGNATURE"
    if intake_dir.exists():
        assert list(intake_dir.iterdir()) == []
    if quarantine_dir.exists():
        assert list(quarantine_dir.rglob("*.*")) == []
    assert len(db_session.added_records) == 0


# 22. Malformed PDF returns 422.
def test_malformed_pdf_returns_422(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    truncated_content = b"%PDF-1.4 header without trailer"

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("malformed.pdf", io.BytesIO(truncated_content), "application/pdf")},
    )

    assert response.status_code == 422
    detail = response.json().get("detail", {})
    assert detail.get("code") == "MALFORMED_PDF"
    if intake_dir.exists():
        assert list(intake_dir.iterdir()) == []
    if quarantine_dir.exists():
        assert list(quarantine_dir.rglob("*.*")) == []
    assert len(db_session.added_records) == 0


# 23. Encrypted PDF returns 422.
def test_encrypted_pdf_returns_422(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("mypassword")
    buf = io.BytesIO()
    writer.write(buf)

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("locked.pdf", io.BytesIO(buf.getvalue()), "application/pdf")},
    )

    assert response.status_code == 422
    detail = response.json().get("detail", {})
    assert detail.get("code") == "ENCRYPTED_PDF"
    if intake_dir.exists():
        assert list(intake_dir.iterdir()) == []
    if quarantine_dir.exists():
        assert list(quarantine_dir.rglob("*.*")) == []
    assert len(db_session.added_records) == 0


# 24. Over-page-limit PDF returns 422.
def test_over_page_limit_pdf_returns_422(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    content = _create_valid_pdf_bytes(6)  # limit is 5

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("too_long.pdf", io.BytesIO(content), "application/pdf")},
    )

    assert response.status_code == 422
    detail = response.json().get("detail", {})
    assert detail.get("code") == "PDF_PAGE_LIMIT_EXCEEDED"
    if intake_dir.exists():
        assert list(intake_dir.iterdir()) == []
    if quarantine_dir.exists():
        assert list(quarantine_dir.rglob("*.*")) == []
    assert len(db_session.added_records) == 0


# 25. Unsafe-content PDF returns 422.
def test_unsafe_content_pdf_returns_422(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_js("app.alert('evil');")
    buf = io.BytesIO()
    writer.write(buf)

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("unsafe.pdf", io.BytesIO(buf.getvalue()), "application/pdf")},
    )

    assert response.status_code == 422
    detail = response.json().get("detail", {})
    assert detail.get("code") == "UNSAFE_PDF_CONTENT"
    if intake_dir.exists():
        assert list(intake_dir.iterdir()) == []
    if quarantine_dir.exists():
        assert list(quarantine_dir.rglob("*.*")) == []
    assert len(db_session.added_records) == 0


# 17. A traversal-style filename cannot escape tmp_path.
def test_traversal_filename_normalized_safely(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    content = _create_valid_pdf_bytes(1)

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("../../../../etc/passwd.pdf", io.BytesIO(content), "application/pdf")},
    )

    assert response.status_code == 202
    quarantine_pdf_dir = quarantine_dir / "pdf"
    saved_files = list(quarantine_pdf_dir.iterdir())
    assert len(saved_files) == 1
    assert saved_files[0].parent == quarantine_pdf_dir
    artifact = next(r for r in db_session.added_records if isinstance(r, ResumeArtifact))
    assert artifact.original_filename == "passwd.pdf"


# 28. Database failure after quarantine deletes the quarantined file.
def test_db_failure_deletes_quarantined_file(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    db_session.should_fail_commit = True
    content = _create_valid_pdf_bytes(1)

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("resume.pdf", io.BytesIO(content), "application/pdf")},
    )

    assert response.status_code == 503
    detail = response.json().get("detail", {})
    assert detail.get("code") == "INTAKE_UNAVAILABLE"
    assert db_session.rolled_back is True
    # Ensure both intake and quarantine are clean
    if intake_dir.exists():
        assert list(intake_dir.iterdir()) == []
    if quarantine_dir.exists():
        assert list(quarantine_dir.rglob("*.*")) == []


# 20. Error responses do not contain paths or submitted byte content.
def test_error_responses_safe(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client
    secret_bytes = b"SECRET_INTERNAL_INFO"

    response = client.post(
        "/api/v1/resumes",
        files={"file": ("bad_type.exe", io.BytesIO(secret_bytes), "application/octet-stream")},
    )

    body_str = response.text
    assert str(intake_dir) not in body_str
    assert str(quarantine_dir) not in body_str
    assert "SECRET_INTERNAL_INFO" not in body_str


# Invalid filenames testing
def test_invalid_filenames(test_client):
    client, intake_dir, quarantine_dir, db_session, _ = test_client

    # Whitespace only
    resp = client.post(
        "/api/v1/resumes",
        files={"file": ("   ", io.BytesIO(_create_valid_pdf_bytes(1)), "application/pdf")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_FILENAME"

    # Control chars
    resp = client.post(
        "/api/v1/resumes",
        files={"file": ("bad\x00name.pdf", io.BytesIO(_create_valid_pdf_bytes(1)), "application/pdf")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "INVALID_FILENAME"


def test_upload_reportlab_pdf_with_trans_and_ascii85_endpoint_success(test_client):
    """Exact ReportLab PDF structure with /PageMode, /Trans <<>>, and /ASCII85Decode is accepted by POST /api/v1/resumes."""
    from pypdf.generic import DictionaryObject, NameObject, NumberObject
    client, intake_dir, quarantine_dir, db_session, _ = test_client

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Trans")] = DictionaryObject({})
    page[NameObject("/Rotate")] = NumberObject(0)
    writer._root_object[NameObject("/PageMode")] = NameObject("/UseNone")

    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()

    resp = client.post(
        "/api/v1/resumes",
        files={"file": ("resume_test_B_two_column.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "artifactId" in data
    assert "jobId" in data
    assert data["status"] == "QUEUED"


def test_list_candidate_resumes_endpoint(test_client):
    """GET /api/v1/resumes returns list of candidates with correct status and metadata."""
    client, intake_dir, quarantine_dir, db_session, _ = test_client

    # Add a mock artifact
    from datetime import datetime, timezone
    art = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="sample_candidate.pdf",
        declared_mime_type="application/pdf",
        size_bytes=1234,
        sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(art)

    resp = client.get("/api/v1/resumes")
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)
    assert len(items) >= 1
    found = next((i for i in items if i["artifactId"] == str(art.artifact_id)), None)
    assert found is not None
    assert found["originalFilename"] == "sample_candidate.pdf"
    assert found["validationStatus"] == "VALIDATED"

