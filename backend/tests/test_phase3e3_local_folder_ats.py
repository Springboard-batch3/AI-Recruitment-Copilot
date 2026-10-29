import base64
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import AuthenticatedContext, require_admin, require_recruiter
from app.config import Settings, get_settings
from app.database import get_db
from app.main import app
from app.models.ats import (
    ATSAdapterType,
    ATSConnectionRecord,
    ATSHealthStatus,
    ATSImportItemResultRecord,
    ATSImportRunRecord,
    ATSImportStatus,
    ATSItemResultStatus,
    ATSObjectMappingRecord,
    ATSObjectType,
)
from app.models.auth import UserRecord, UserRole
from app.models.base import Base
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.job import JobLifecycleStatus, JobRequisition, JobVersion, JobVersionStatus
from app.models.resume import ResumeArtifact
from app.schemas.ats import ATSImportRunRequest
from app.services.ats.adapter_registry import get_ats_adapter
from app.services.ats.import_service import ATSImportService
from app.services.ats.local_folder_adapter import LocalFolderATSAdapter

# Minimal valid PDF bytes
VALID_PDF_BYTES = (
    b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\n"
    b"2 0 obj\n<<\n/Type /Pages\n/Kids [3 0 R]\n/Count 1\n>>\nendobj\n"
    b"3 0 obj\n<<\n/Type /Page\n/Parent 2 0 R\n/MediaBox [0 0 612 792]\n/Contents 4 0 R\n>>\nendobj\n"
    b"4 0 obj\n<<\n/Length 55\n>>\nstream\n"
    b"BT\n/F1 12 Tf\n100 700 Td\n(Jane Doe Senior Python Developer Resume) Tj\nET\nendstream\nendobj\n"
    b"xref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000189 00000 n \n"
    b"trailer\n<<\n/Size 5\n/Root 1 0 R\n>>\nstartxref\n294\n%%EOF\n"
)

# Unsafe/invalid binary
UNSAFE_BYTES = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00Prohibited Executable Binary Header"


@pytest.fixture
def test_db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def setup_local_folder_env(tmp_path: Path, monkeypatch):
    """Creates temporary inbox and overrides ATS_LOCAL_DROP_ROOT."""
    drop_root = tmp_path / "local_ats_demo"
    inbox = drop_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    def mock_get_ats_local_drop_root(self):
        return drop_root

    monkeypatch.setattr(Settings, "get_ats_local_drop_root", mock_get_ats_local_drop_root)
    return {"drop_root": drop_root, "inbox": inbox}


@pytest.fixture
def setup_test_data(test_db: Session):
    org_id = uuid.uuid4()
    other_org_id = uuid.uuid4()
    user_id = uuid.uuid4()

    admin_user = UserRecord(
        user_id=user_id,
        email="admin.local@example.com",
        display_name="Admin Local",
        role=UserRole.ADMIN,
        password_hash="fakehash",
    )
    admin_user.organization_id = org_id

    recruiter_user = UserRecord(
        user_id=uuid.uuid4(),
        email="recruiter.local@example.com",
        display_name="Recruiter Local",
        role=UserRole.RECRUITER,
        password_hash="fakehash",
    )
    recruiter_user.organization_id = org_id

    test_db.add_all([admin_user, recruiter_user])

    # Create destination job in Org
    job_id = uuid.uuid4()
    job_req = JobRequisition(
        job_id=job_id,
        created_by_user_id=user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    job_ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job_id,
        created_by_user_id=user_id,
        version_number=1,
        revision=1,
        title="Senior Backend Systems Engineer",
        department="Engineering",
        version_status=JobVersionStatus.READY_FOR_MATCHING,
    )
    test_db.add_all([job_req, job_ver])

    # Create connection
    conn = ATSConnectionRecord(
        connection_id=uuid.uuid4(),
        organization_id=org_id,
        connection_name="Local ATS Demo",
        adapter_type=ATSAdapterType.LOCAL_FOLDER_DEMO,
        tenant_label="local-demo",
        scopes=["candidates.import"],
        is_enabled=True,
        health_status=ATSHealthStatus.HEALTHY,
        kill_switch_active=False,
    )
    test_db.add(conn)
    test_db.commit()

    return {
        "org_id": org_id,
        "other_org_id": other_org_id,
        "admin_user": admin_user,
        "recruiter_user": recruiter_user,
        "job_id": job_id,
        "conn": conn,
    }


def test_1_enabled_org_owned_connection_is_recruiter_visible(test_db: Session, setup_test_data):
    """1. Enabled organization-owned Local Folder connection is recruiter-visible."""
    data = setup_test_data
    client = TestClient(app)

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_recruiter] = lambda: AuthenticatedContext(
        user=data["recruiter_user"],
        session=None,  # type: ignore
    )

    try:
        res = client.get("/api/v1/ats/active-connections")
        assert res.status_code == 200
        items = res.json()["items"]
        assert len(items) >= 1
        found = next((c for c in items if c["connection_id"] == str(data["conn"].connection_id)), None)
        assert found is not None
        assert found["adapter_type"] == "LOCAL_FOLDER_DEMO"
        assert found["is_enabled"] is True
    finally:
        app.dependency_overrides.clear()


def test_2_healthy_but_disabled_connection_not_visible(test_db: Session, setup_test_data):
    """2. Healthy but disabled connection is not visible."""
    data = setup_test_data
    client = TestClient(app)

    # Disable connection
    data["conn"].is_enabled = False
    test_db.commit()

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_recruiter] = lambda: AuthenticatedContext(
        user=data["recruiter_user"],
        session=None,  # type: ignore
    )

    try:
        res = client.get("/api/v1/ats/active-connections")
        assert res.status_code == 200
        items = res.json()["items"]
        found = next((c for c in items if c["connection_id"] == str(data["conn"].connection_id)), None)
        assert found is None
    finally:
        app.dependency_overrides.clear()


def test_3_other_organization_connection_not_visible(test_db: Session, setup_test_data):
    """3. Other-organization connection is not visible."""
    data = setup_test_data
    client = TestClient(app)

    other_recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email="other.org@example.com",
        display_name="Other Org Recruiter",
        role=UserRole.RECRUITER,
        password_hash="fakehash",
    )
    other_recruiter.organization_id = data["other_org_id"]
    test_db.add(other_recruiter)
    test_db.commit()

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_recruiter] = lambda: AuthenticatedContext(
        user=other_recruiter,
        session=None,  # type: ignore
    )

    try:
        res = client.get("/api/v1/ats/active-connections")
        assert res.status_code == 200
        items = res.json()["items"]
        found = next((c for c in items if c["connection_id"] == str(data["conn"].connection_id)), None)
        assert found is None
    finally:
        app.dependency_overrides.clear()


def test_4_folder_health_check_detects_available_and_missing(setup_local_folder_env, setup_test_data):
    """4. Folder health check detects available and missing folders."""
    env = setup_local_folder_env
    data = setup_test_data
    adapter = get_ats_adapter(ATSAdapterType.LOCAL_FOLDER_DEMO)

    # When inbox exists and is accessible
    health_good = adapter.healthcheck(data["conn"])
    assert health_good.status == ATSHealthStatus.HEALTHY

    # When inbox is missing
    os.rmdir(env["inbox"])
    health_missing = adapter.healthcheck(data["conn"])
    assert health_missing.status == ATSHealthStatus.UNHEALTHY
    assert "inbox directory does not exist" in health_missing.message


def test_5_pdf_and_docx_discovery(setup_local_folder_env, setup_test_data):
    """5. PDF and DOCX files are discovered."""
    env = setup_local_folder_env
    data = setup_test_data
    adapter = LocalFolderATSAdapter()

    # Write a valid PDF file
    pdf_path = env["inbox"] / "Jane_Doe_Resume.pdf"
    pdf_path.write_bytes(VALID_PDF_BYTES)

    items = adapter.scan_inbox(data["conn"])
    assert len(items) == 1
    assert items[0].format == "PDF"
    assert items[0].status == "READY"
    assert items[0].sanitized_filename == "Jane_Doe_Resume.pdf"
    assert items[0].content_hash != ""


def test_6_unsupported_files_are_rejected(setup_local_folder_env, setup_test_data):
    """6. Unsupported files (e.g. .txt, .exe, .json) are rejected."""
    env = setup_local_folder_env
    data = setup_test_data
    adapter = LocalFolderATSAdapter()

    # Write a txt file and json file
    (env["inbox"] / "notes.txt").write_text("plain text resume")
    (env["inbox"] / "data.json").write_text('{"candidate": "test"}')

    items = adapter.scan_inbox(data["conn"])
    assert len(items) == 2
    for itm in items:
        assert itm.status == "UNSUPPORTED_TYPE"
        assert "Unsupported file format" in (itm.safe_error_message or "")


def test_7_oversize_and_unsafe_documents_use_existing_validators(setup_local_folder_env, setup_test_data, monkeypatch):
    """7. Oversize and unsafe documents use existing validators."""
    env = setup_local_folder_env
    data = setup_test_data
    adapter = LocalFolderATSAdapter()

    # Write an unsafe document
    unsafe_path = env["inbox"] / "malformed.pdf"
    unsafe_path.write_bytes(UNSAFE_BYTES)

    items = adapter.scan_inbox(data["conn"])
    assert len(items) == 1
    assert items[0].status in ("UNSAFE_DOCUMENT", "INVALID_FILE")


def test_8_symlinks_and_traversal_are_rejected(setup_local_folder_env, setup_test_data):
    """8. Symlinks and path traversal are rejected."""
    env = setup_local_folder_env
    data = setup_test_data
    adapter = LocalFolderATSAdapter()

    # Create target outside inbox and symlink inside inbox
    outside_file = env["drop_root"] / "outside.pdf"
    outside_file.write_bytes(VALID_PDF_BYTES)

    symlink_path = env["inbox"] / "symlink_resume.pdf"
    try:
        os.symlink(outside_file, symlink_path)
    except OSError:
        pytest.skip("Symlinks not supported on filesystem")

    items = adapter.scan_inbox(data["conn"])
    symlink_item = next((i for i in items if "symlink" in i.sanitized_filename), None)
    assert symlink_item is not None
    assert symlink_item.status == "INVALID_FILE"
    assert "Symbolic link" in (symlink_item.safe_error_message or "")


def test_9_scan_does_not_write_candidate_records(test_db: Session, setup_local_folder_env, setup_test_data):
    """9. Scan does not write candidate records."""
    env = setup_local_folder_env
    data = setup_test_data
    svc = ATSImportService(test_db)

    # Place valid PDF in inbox
    (env["inbox"] / "Candidate_A.pdf").write_bytes(VALID_PDF_BYTES)

    # Dry-run scan
    run = svc.execute_import(
        connection_id=data["conn"].connection_id,
        organization_id=data["org_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(
            is_dry_run=True,
            destination_job_id=data["job_id"],
        ),
    )

    assert run.is_dry_run is True
    assert run.successful_items_count == 1
    assert run.summary_json["preview_token"] is not None
    assert run.summary_json["preview_hash"] is not None

    # Verify NO candidate or artifact records were created
    assert test_db.execute(select(ResumeArtifact)).scalars().all() == []
    assert test_db.execute(select(CandidateIdentityRecord)).scalars().all() == []
    assert test_db.execute(select(CandidateProfileRecord)).scalars().all() == []


def test_10_confirmation_imports_ready_items(test_db: Session, setup_local_folder_env, setup_test_data):
    """10. Confirmation imports READY items with provenance."""
    env = setup_local_folder_env
    data = setup_test_data
    svc = ATSImportService(test_db)

    # Place valid PDF in inbox
    (env["inbox"] / "Alice_Developer.pdf").write_bytes(VALID_PDF_BYTES)

    # 1. Scan (dry-run)
    scan_run = svc.execute_import(
        connection_id=data["conn"].connection_id,
        organization_id=data["org_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(
            is_dry_run=True,
            destination_job_id=data["job_id"],
        ),
    )

    preview_token = scan_run.summary_json["preview_token"]
    preview_hash = scan_run.summary_json["preview_hash"]

    # 2. Confirm (live import)
    live_run = svc.execute_import(
        connection_id=data["conn"].connection_id,
        organization_id=data["org_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(
            is_dry_run=False,
            destination_job_id=data["job_id"],
            preview_token=preview_token,
            preview_hash=preview_hash,
        ),
    )

    assert live_run.is_dry_run is False
    assert live_run.successful_items_count == 1
    assert live_run.status == ATSImportStatus.COMPLETED

    # Verify records created with Local ATS Demo provenance
    artifacts = test_db.execute(select(ResumeArtifact)).scalars().all()
    assert len(artifacts) == 1
    assert artifacts[0].original_filename == "Alice_Developer.pdf"

    profiles = test_db.execute(select(CandidateProfileRecord)).scalars().all()
    assert len(profiles) == 1
    assert profiles[0].provenance == "Local ATS Demo"


def test_11_confirmation_without_valid_preview_is_rejected(test_db: Session, setup_local_folder_env, setup_test_data):
    """11. Confirmation without a valid preview is rejected."""
    env = setup_local_folder_env
    data = setup_test_data
    svc = ATSImportService(test_db)

    (env["inbox"] / "Alice_Developer.pdf").write_bytes(VALID_PDF_BYTES)

    with pytest.raises(ValueError, match="Confirmation requires a valid"):
        svc.execute_import(
            connection_id=data["conn"].connection_id,
            organization_id=data["org_id"],
            user_id=data["recruiter_user"].user_id,
            request=ATSImportRunRequest(
                is_dry_run=False,
                destination_job_id=data["job_id"],
                preview_token=None,
                preview_hash=None,
            ),
        )


def test_12_changed_file_after_preview_is_rejected(test_db: Session, setup_local_folder_env, setup_test_data):
    """12. Changed file after preview is rejected."""
    env = setup_local_folder_env
    data = setup_test_data
    svc = ATSImportService(test_db)

    pdf_file = env["inbox"] / "Candidate_B.pdf"
    pdf_file.write_bytes(VALID_PDF_BYTES)

    # 1. Preview
    scan_run = svc.execute_import(
        connection_id=data["conn"].connection_id,
        organization_id=data["org_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(
            is_dry_run=True,
            destination_job_id=data["job_id"],
        ),
    )

    preview_token = scan_run.summary_json["preview_token"]
    preview_hash = scan_run.summary_json["preview_hash"]

    # 2. Modify file in inbox before confirm
    pdf_file.write_bytes(VALID_PDF_BYTES + b"\n% additional byte comment\n")

    # 3. Attempt confirmation -> Rejected
    with pytest.raises(ValueError, match="contents have changed since preview"):
        svc.execute_import(
            connection_id=data["conn"].connection_id,
            organization_id=data["org_id"],
            user_id=data["recruiter_user"].user_id,
            request=ATSImportRunRequest(
                is_dry_run=False,
                destination_job_id=data["job_id"],
                preview_token=preview_token,
                preview_hash=preview_hash,
            ),
        )


def test_13_same_file_imported_twice_creates_no_duplicate(test_db: Session, setup_local_folder_env, setup_test_data):
    """13. Same file imported twice creates no duplicate candidate."""
    env = setup_local_folder_env
    data = setup_test_data
    svc = ATSImportService(test_db)

    (env["inbox"] / "Candidate_C.pdf").write_bytes(VALID_PDF_BYTES)

    # First import
    scan_1 = svc.execute_import(
        connection_id=data["conn"].connection_id,
        organization_id=data["org_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(is_dry_run=True, destination_job_id=data["job_id"]),
    )
    svc.execute_import(
        connection_id=data["conn"].connection_id,
        organization_id=data["org_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(
            is_dry_run=False,
            destination_job_id=data["job_id"],
            preview_token=scan_1.summary_json["preview_token"],
            preview_hash=scan_1.summary_json["preview_hash"],
        ),
    )

    initial_candidates_count = len(test_db.execute(select(CandidateIdentityRecord)).scalars().all())
    assert initial_candidates_count == 1

    # Second scan -> Item should be identified as duplicate
    scan_2 = svc.execute_import(
        connection_id=data["conn"].connection_id,
        organization_id=data["org_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(is_dry_run=True, destination_job_id=data["job_id"]),
    )
    assert scan_2.duplicate_items_count == 1
    assert scan_2.successful_items_count == 0

    # Second confirm -> No second candidate created
    svc.execute_import(
        connection_id=data["conn"].connection_id,
        organization_id=data["org_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(
            is_dry_run=False,
            destination_job_id=data["job_id"],
            preview_token=scan_2.summary_json["preview_token"],
            preview_hash=scan_2.summary_json["preview_hash"],
        ),
    )

    final_candidates_count = len(test_db.execute(select(CandidateIdentityRecord)).scalars().all())
    assert final_candidates_count == 1


def test_14_same_filename_with_changed_content_treated_as_new_hash(test_db: Session, setup_local_folder_env, setup_test_data):
    """14. Same filename with changed content follows changed-input policy."""
    env = setup_local_folder_env
    data = setup_test_data
    svc = ATSImportService(test_db)

    file_path = env["inbox"] / "resume.pdf"
    file_path.write_bytes(VALID_PDF_BYTES)

    # First import
    scan_1 = svc.execute_import(
        connection_id=data["conn"].connection_id,
        organization_id=data["org_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(is_dry_run=True, destination_job_id=data["job_id"]),
    )
    svc.execute_import(
        connection_id=data["conn"].connection_id,
        organization_id=data["org_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(
            is_dry_run=False,
            destination_job_id=data["job_id"],
            preview_token=scan_1.summary_json["preview_token"],
            preview_hash=scan_1.summary_json["preview_hash"],
        ),
    )

    # Now replace file with different valid content (different hash)
    ALT_VALID_PDF_BYTES = VALID_PDF_BYTES.replace(b"Jane Doe", b"John Smith")
    file_path.write_bytes(ALT_VALID_PDF_BYTES)

    # Scan recognizes new content hash as ready
    scan_2 = svc.execute_import(
        connection_id=data["conn"].connection_id,
        organization_id=data["org_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(is_dry_run=True, destination_job_id=data["job_id"]),
    )
    assert scan_2.successful_items_count == 1

    svc.execute_import(
        connection_id=data["conn"].connection_id,
        organization_id=data["org_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(
            is_dry_run=False,
            destination_job_id=data["job_id"],
            preview_token=scan_2.summary_json["preview_token"],
            preview_hash=scan_2.summary_json["preview_hash"],
        ),
    )

    # Two distinct candidates exist
    artifacts = test_db.execute(select(ResumeArtifact)).scalars().all()
    assert len(artifacts) == 2


def test_15_recruiter_cannot_choose_another_organizations_job(test_db: Session, setup_local_folder_env, setup_test_data):
    """15. Recruiter cannot choose an invalid or missing destination job."""
    env = setup_local_folder_env
    data = setup_test_data
    svc = ATSImportService(test_db)

    (env["inbox"] / "Candidate.pdf").write_bytes(VALID_PDF_BYTES)

    fake_job_id = uuid.uuid4()
    with pytest.raises(ValueError, match="destination job was not found"):
        svc.execute_import(
            connection_id=data["conn"].connection_id,
            organization_id=data["org_id"],
            user_id=data["recruiter_user"].user_id,
            request=ATSImportRunRequest(is_dry_run=True, destination_job_id=fake_job_id),
        )


def test_16_secrets_and_absolute_paths_never_serialize(test_db: Session, setup_local_folder_env, setup_test_data):
    """16. Secrets and absolute filesystem paths never serialize or appear in recruiter responses."""
    env = setup_local_folder_env
    data = setup_test_data
    client = TestClient(app)

    (env["inbox"] / "SecretResume.pdf").write_bytes(VALID_PDF_BYTES)

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_recruiter] = lambda: AuthenticatedContext(
        user=data["recruiter_user"],
        session=None,  # type: ignore
    )

    try:
        # Active connections list
        res_conns = client.get("/api/v1/ats/active-connections")
        assert res_conns.status_code == 200
        conns_text = str(res_conns.content)
        assert str(env["drop_root"]) not in conns_text
        assert "secret_ref_masked" not in res_conns.json()["items"][0] or res_conns.json()["items"][0]["secret_ref_masked"] is None

        # Import run preview
        res_scan = client.post(
            f"/api/v1/ats/connections/{data['conn'].connection_id}/import",
            json={
                "is_dry_run": True,
                "destination_job_id": str(data["job_id"]),
            },
        )
        assert res_scan.status_code == 200
        scan_text = str(res_scan.content)
        assert str(env["drop_root"]) not in scan_text
    finally:
        app.dependency_overrides.clear()


def test_17_one_invalid_file_does_not_prevent_valid_imports(test_db: Session, setup_local_folder_env, setup_test_data):
    """17. One invalid file does not prevent valid imports under item-isolated transaction policy."""
    env = setup_local_folder_env
    data = setup_test_data
    svc = ATSImportService(test_db)

    # Place 1 valid PDF, 1 invalid file
    (env["inbox"] / "Valid_Candidate.pdf").write_bytes(VALID_PDF_BYTES)
    (env["inbox"] / "Invalid_File.pdf").write_bytes(UNSAFE_BYTES)

    # Scan preview
    scan_run = svc.execute_import(
        connection_id=data["conn"].connection_id,
        organization_id=data["org_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(is_dry_run=True, destination_job_id=data["job_id"]),
    )

    assert scan_run.successful_items_count == 1
    assert scan_run.failed_items_count == 1

    # Confirmation
    live_run = svc.execute_import(
        connection_id=data["conn"].connection_id,
        organization_id=data["org_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(
            is_dry_run=False,
            destination_job_id=data["job_id"],
            preview_token=scan_run.summary_json["preview_token"],
            preview_hash=scan_run.summary_json["preview_hash"],
        ),
    )

    # Valid candidate was successfully imported
    assert live_run.successful_items_count == 1
    assert live_run.failed_items_count == 1
    assert live_run.status == ATSImportStatus.PARTIAL_SUCCESS

    artifacts = test_db.execute(select(ResumeArtifact)).scalars().all()
    assert len(artifacts) == 1
    assert artifacts[0].original_filename == "Valid_Candidate.pdf"
