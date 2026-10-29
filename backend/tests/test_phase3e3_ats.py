import base64
import uuid
from datetime import datetime, timezone
from typing import Generator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import AuthenticatedContext, require_admin, require_recruiter
from app.database import get_db
from app.main import app
from app.models.ats import (
    ATSAdapterType,
    ATSConnectionRecord,
    ATSExportReceiptRecord,
    ATSExportStatus,
    ATSHealthStatus,
    ATSImportItemResultRecord,
    ATSImportRunRecord,
    ATSImportStatus,
    ATSItemResultStatus,
    ATSObjectMappingRecord,
    ATSObjectType,
    ATSReconciliationRecord,
    ATSReconciliationStatus,
)
from app.models.auth import UserRecord, UserRole
from app.models.base import Base
from app.models.candidate import CandidateIdentityRecord
from app.models.job import JobLifecycleStatus, JobRequisition, JobVersion, JobVersionStatus
from app.schemas.ats import (
    ATSConnectionCreateRequest,
    ATSConnectionUpdateRequest,
    ATSExportConfirmRequest,
    ATSExportPreviewRequest,
    ATSImportRunRequest,
)
from app.services.ats.adapter_registry import get_ats_adapter
from app.services.ats.csv_json_adapter import CSVJSONAdapter, sanitize_formula_injection
from app.services.ats.export_service import ATSExportService, compute_export_preview_hash
from app.services.ats.import_service import ATSImportService, compute_canonical_hash
from app.services.ats.mock_adapter import MINIMAL_VALID_PDF_BYTES, MockATSAdapter


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
def setup_ats_data(test_db: Session):
    org_a_id = uuid.uuid4()
    org_b_id = uuid.uuid4()

    admin_user = UserRecord(
        user_id=org_a_id,
        email="admin@example.com",
        display_name="Admin User",
        role=UserRole.ADMIN,
        password_hash="fakehash",
    )
    admin_user.organization_id = org_a_id
    recruiter_user = UserRecord(
        user_id=uuid.uuid4(),
        email="recruiter@example.com",
        display_name="Recruiter User",
        role=UserRole.RECRUITER,
        password_hash="fakehash",
    )
    recruiter_user.organization_id = org_a_id
    test_db.add_all([admin_user, recruiter_user])
    test_db.flush()

    conn_a = ATSConnectionRecord(
        connection_id=uuid.uuid4(),
        organization_id=org_a_id,
        connection_name="Primary Mock ATS",
        adapter_type=ATSAdapterType.MOCK,
        tenant_label="sandbox-tenant-1",
        scopes=["jobs.read", "candidates.read", "notes.write"],
        is_enabled=True,
        health_status=ATSHealthStatus.HEALTHY,
        secret_ref="env:MOCK_ATS_API_KEY",
        encrypted_config={},
        kill_switch_active=False,
    )
    test_db.add(conn_a)
    test_db.commit()
    test_db.refresh(conn_a)

    return {
        "org_a_id": org_a_id,
        "org_b_id": org_b_id,
        "admin_user": admin_user,
        "recruiter_user": recruiter_user,
        "conn_a": conn_a,
    }


def test_unknown_adapter_type_rejected():
    with pytest.raises(ValueError, match="Unknown or unvetted ATS adapter type"):
        get_ats_adapter("UNVETTED_CUSTOM_ADAPTER")  # type: ignore


def test_organization_scoped_connection_access(test_db: Session, setup_ats_data):
    data = setup_ats_data
    svc = ATSImportService(test_db)

    # Attempting to access Org A's connection using Org B credentials must fail
    with pytest.raises(ValueError, match="ATS connection not found or unauthorized"):
        svc.execute_import(
            connection_id=data["conn_a"].connection_id,
            organization_id=data["org_b_id"],
            user_id=data["recruiter_user"].user_id,
            request=ATSImportRunRequest(is_dry_run=True),
        )


def test_credentials_never_serialize_or_log(test_db: Session, setup_ats_data):
    data = setup_ats_data
    client = TestClient(app)

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_admin] = lambda: AuthenticatedContext(
        user=data["admin_user"],
        session=None,  # type: ignore
    )

    try:
        res = client.get("/api/v1/ats/connections")
        assert res.status_code == 200
        data_json = res.json()
        assert len(data_json["items"]) >= 1
        item = data_json["items"][0]
        # Never plaintext secret
        assert "password" not in item
        assert item["secret_ref_masked"] == "env:MOCK_ATS_API_KEY"

        # Create connection with secret:
        create_res = client.post(
            "/api/v1/ats/connections",
            json={
                "connection_name": "Secured Secret Conn",
                "adapter_type": "MOCK",
                "secret_ref": "secret:sk-super-secret-key-12345",
                "scopes": ["jobs.read"],
            },
        )
        assert create_res.status_code == 201
        created_item = create_res.json()
        assert created_item["secret_ref_masked"] == "secret:••••••••"
        assert "sk-super-secret-key-12345" not in str(create_res.content)
    finally:
        app.dependency_overrides.clear()


def test_mock_pagination_and_checkpoint_resume(test_db: Session, setup_ats_data):
    data = setup_ats_data
    adapter = get_ats_adapter(ATSAdapterType.MOCK)

    # Page 1
    jobs_p1, cursor_p1, has_more_p1 = adapter.list_jobs(data["conn_a"], page=1)
    assert len(jobs_p1) == 2
    assert has_more_p1 is True
    assert cursor_p1 == "cursor-job-page-2"

    # Page 2
    jobs_p2, cursor_p2, has_more_p2 = adapter.list_jobs(data["conn_a"], page=2)
    assert len(jobs_p2) == 1
    assert has_more_p2 is False
    assert cursor_p2 is None


def test_duplicate_import_idempotency_and_changed_record(test_db: Session, setup_ats_data):
    data = setup_ats_data
    svc = ATSImportService(test_db)

    # First import run (Live)
    run_1 = svc.execute_import(
        connection_id=data["conn_a"].connection_id,
        organization_id=data["org_a_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(is_dry_run=False),
    )
    assert run_1.status in (ATSImportStatus.COMPLETED, ATSImportStatus.PARTIAL_SUCCESS)
    assert run_1.successful_items_count > 0
    assert run_1.duplicate_items_count == 0

    # Second import run without changes -> All existing records become duplicates
    run_2 = svc.execute_import(
        connection_id=data["conn_a"].connection_id,
        organization_id=data["org_a_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(is_dry_run=False),
    )
    assert run_2.status in (ATSImportStatus.COMPLETED, ATSImportStatus.PARTIAL_SUCCESS)
    assert run_2.duplicate_items_count > 0


def test_unsafe_attachment_rejected_by_phase1_intake(test_db: Session, setup_ats_data):
    data = setup_ats_data
    svc = ATSImportService(test_db)

    run = svc.execute_import(
        connection_id=data["conn_a"].connection_id,
        organization_id=data["org_a_id"],
        user_id=data["recruiter_user"].user_id,
        request=ATSImportRunRequest(is_dry_run=False),
    )

    # Check item results for unsafe attachment
    unsafe_item = next(
        (i for i in run.items if i.external_id == "mock-att-unsafe-003"), None
    )
    assert unsafe_item is not None
    assert unsafe_item.status == ATSItemResultStatus.VALIDATION_ERROR
    assert "Only PDF and DOCX files are supported" in (unsafe_item.safe_error_message or "")


def test_csv_invalid_headers_and_formula_injection_protection():
    adapter = CSVJSONAdapter()

    # Formula injection test
    assert sanitize_formula_injection("=cmd|'/C calc'!A0") == "'=cmd|'/C calc'!A0"
    assert sanitize_formula_injection("+12345") == "'+12345"
    assert sanitize_formula_injection("@SUM(A1:A10)") == "'@SUM(A1:A10)"
    assert sanitize_formula_injection("Regular String") == "Regular String"

    # Duplicate header rejection
    csv_dup = "title,department,TITLE\nSoftware Engineer,Eng,SE"
    with pytest.raises(ValueError, match="Duplicate column headers detected"):
        adapter.parse_jobs_from_csv(csv_dup)

    # Missing required header
    csv_no_title = "department,location\nEng,SF"
    with pytest.raises(ValueError, match="missing required 'title' column"):
        adapter.parse_jobs_from_csv(csv_no_title)

    # Valid CSV
    csv_valid = "title,department,location\nBackend Engineer,Platform,Remote"
    jobs = adapter.parse_jobs_from_csv(csv_valid)
    assert len(jobs) == 1
    assert jobs[0].title == "Backend Engineer"
    assert jobs[0].department == "Platform"


def test_export_preview_hash_and_single_logical_write(test_db: Session, setup_ats_data):
    data = setup_ats_data
    cand_id = uuid.uuid4()
    cand = CandidateIdentityRecord(
        identity_id=cand_id,
        artifact_id=uuid.uuid4(),
        full_name="Alex Mercer",
        primary_email="alex@example.com",
        identity_payload={"full_name": "Alex Mercer", "email": "alex@example.com"},
    )
    test_db.add(cand)
    test_db.commit()

    export_svc = ATSExportService(test_db)

    # 1. Preview
    preview_req = ATSExportPreviewRequest(
        connection_id=data["conn_a"].connection_id,
        candidate_id=cand_id,
        destination="ATS Candidate Notes - Alex Mercer",
        note_text="Candidate demonstrated strong distributed consensus knowledge.",
    )
    preview = export_svc.generate_export_preview(data["org_a_id"], preview_req)
    assert preview.preview_hash is not None
    assert len(preview.preview_hash) == 64

    # 2. Confirm with matching hash
    confirm_req = ATSExportConfirmRequest(
        connection_id=data["conn_a"].connection_id,
        candidate_id=cand_id,
        preview_hash=preview.preview_hash,
        destination="ATS Candidate Notes - Alex Mercer",
        note_text="Candidate demonstrated strong distributed consensus knowledge.",
    )
    receipt = export_svc.confirm_export(data["org_a_id"], data["recruiter_user"].user_id, confirm_req)
    assert receipt.status == ATSExportStatus.SUCCESS
    assert receipt.preview_hash == preview.preview_hash

    # 3. Confirm with tampered/changed note text must be rejected
    confirm_tampered = ATSExportConfirmRequest(
        connection_id=data["conn_a"].connection_id,
        candidate_id=cand_id,
        preview_hash=preview.preview_hash,
        destination="ATS Candidate Notes - Alex Mercer",
        note_text="Tampered text that does not match preview hash",
    )
    with pytest.raises(ValueError, match="Export preview hash mismatch"):
        export_svc.confirm_export(data["org_a_id"], data["recruiter_user"].user_id, confirm_tampered)


def test_timeout_after_write_enters_reconciliation(test_db: Session, setup_ats_data):
    data = setup_ats_data
    cand_id = uuid.uuid4()
    cand = CandidateIdentityRecord(
        identity_id=cand_id,
        artifact_id=uuid.uuid4(),
        full_name="Jordan Lee",
        primary_email="jordan@example.com",
        identity_payload={"full_name": "Jordan Lee", "email": "jordan@example.com"},
    )
    test_db.add(cand)

    # Configure connection to simulate timeout
    data["conn_a"].encrypted_config = {"simulate_timeout": True}
    test_db.commit()

    export_svc = ATSExportService(test_db)

    preview_req = ATSExportPreviewRequest(
        connection_id=data["conn_a"].connection_id,
        candidate_id=cand_id,
        destination="ATS Candidate Notes - Jordan Lee",
        note_text="Candidate note pending export.",
    )
    preview = export_svc.generate_export_preview(data["org_a_id"], preview_req)

    confirm_req = ATSExportConfirmRequest(
        connection_id=data["conn_a"].connection_id,
        candidate_id=cand_id,
        preview_hash=preview.preview_hash,
        destination="ATS Candidate Notes - Jordan Lee",
        note_text="Candidate note pending export.",
    )
    receipt = export_svc.confirm_export(data["org_a_id"], data["recruiter_user"].user_id, confirm_req)

    # Status must be RECONCILIATION_REQUIRED
    assert receipt.status == ATSExportStatus.RECONCILIATION_REQUIRED

    # Check reconciliation record was created
    reconciliation = test_db.query(ATSReconciliationRecord).filter_by(export_receipt_id=receipt.receipt_id).first()
    assert reconciliation is not None
    assert reconciliation.status == ATSReconciliationStatus.PENDING
    assert "Timeout during note export" in reconciliation.reason


def test_no_stage_or_rejection_operation_exists():
    adapter = get_ats_adapter(ATSAdapterType.MOCK)
    # Ensure forbidden ATS operations do NOT exist on the adapter interface
    assert not hasattr(adapter, "reject_candidate")
    assert not hasattr(adapter, "advance_candidate_stage")
    assert not hasattr(adapter, "schedule_interview")
    assert not hasattr(adapter, "send_candidate_email")


def test_recruiter_active_connections_discovery_and_safety(test_db: Session, setup_ats_data):
    data = setup_ats_data
    client = TestClient(app)

    # Attach org_a_id to recruiter_user
    recruiter = data["recruiter_user"]
    recruiter.organization_id = data["org_a_id"]

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_recruiter] = lambda: AuthenticatedContext(
        user=recruiter,
        session=None,  # type: ignore
    )

    try:
        res = client.get("/api/v1/ats/active-connections")
        assert res.status_code == 200
        body = res.json()
        assert body["total_count"] == 1
        conn = body["items"][0]
        assert conn["connection_id"] == str(data["conn_a"].connection_id)
        assert conn["connection_name"] == "Primary Mock ATS"
        assert conn["adapter_type"] == "MOCK"
        assert conn["is_enabled"] is True
        assert conn["health_status"] == "HEALTHY"
        # Secrets and secret references must NEVER be returned to recruiters
        assert conn["secret_ref_masked"] is None
        assert "password" not in conn
        assert "config" not in conn
    finally:
        app.dependency_overrides.clear()


def test_disabled_or_kill_switch_connection_not_returned_to_recruiter(test_db: Session, setup_ats_data):
    data = setup_ats_data
    client = TestClient(app)

    # Disable conn_a
    data["conn_a"].is_enabled = False
    data["conn_a"].health_status = ATSHealthStatus.HEALTHY
    test_db.commit()

    recruiter = data["recruiter_user"]
    recruiter.organization_id = data["org_a_id"]

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_recruiter] = lambda: AuthenticatedContext(
        user=recruiter,
        session=None,  # type: ignore
    )

    try:
        # Healthy but disabled -> 0 items
        res = client.get("/api/v1/ats/active-connections")
        assert res.status_code == 200
        assert res.json()["total_count"] == 0

        # Enable conn_a but activate kill switch -> 0 items
        data["conn_a"].is_enabled = True
        data["conn_a"].kill_switch_active = True
        test_db.commit()

        res_ks = client.get("/api/v1/ats/active-connections")
        assert res_ks.status_code == 200
        assert res_ks.json()["total_count"] == 0
    finally:
        app.dependency_overrides.clear()


def test_cross_organization_connection_isolation(test_db: Session, setup_ats_data):
    data = setup_ats_data
    client = TestClient(app)

    # Recruiter belongs to Org B
    other_recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email="other.recruiter@orgb.test",
        display_name="Other Recruiter",
        role=UserRole.RECRUITER,
        password_hash="fakehash",
    )
    other_recruiter.organization_id = data["org_b_id"]
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
        # Must receive 0 connections from Org A
        assert res.json()["total_count"] == 0
    finally:
        app.dependency_overrides.clear()


def test_admin_health_testing_preserves_activation_state(test_db: Session, setup_ats_data):
    data = setup_ats_data
    client = TestClient(app)

    # Connection starts disabled and with UNKNOWN health
    data["conn_a"].is_enabled = False
    data["conn_a"].health_status = ATSHealthStatus.UNKNOWN
    test_db.commit()

    admin = data["admin_user"]
    admin.organization_id = data["org_a_id"]

    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[require_admin] = lambda: AuthenticatedContext(
        user=admin,
        session=None,  # type: ignore
    )

    try:
        test_res = client.post(f"/api/v1/ats/connections/{data['conn_a'].connection_id}/test")
        assert test_res.status_code == 200
        tested_conn = test_res.json()
        assert tested_conn["health_status"] == "HEALTHY"
        # is_enabled must remain False!
        assert tested_conn["is_enabled"] is False

        # Verify DB state directly
        test_db.refresh(data["conn_a"])
        assert data["conn_a"].health_status == ATSHealthStatus.HEALTHY
        assert data["conn_a"].is_enabled is False
    finally:
        app.dependency_overrides.clear()


def test_shared_default_workspace_admin_and_recruiter(test_db: Session):
    client = TestClient(app)

    # Create admin and recruiter without explicit organization_id attribute
    admin = UserRecord(
        user_id=uuid.uuid4(),
        email="workspace.admin@example.com",
        display_name="Workspace Admin",
        role=UserRole.ADMIN,
        password_hash="fakehash",
    )
    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email="workspace.recruiter@example.com",
        display_name="Workspace Recruiter",
        role=UserRole.RECRUITER,
        password_hash="fakehash",
    )
    test_db.add_all([admin, recruiter])
    test_db.commit()

    app.dependency_overrides[get_db] = lambda: test_db

    try:
        # Admin creates connection
        app.dependency_overrides[require_admin] = lambda: AuthenticatedContext(
            user=admin,
            session=None,  # type: ignore
        )
        create_res = client.post(
            "/api/v1/ats/connections",
            json={
                "connection_name": "Shared Workspace Mock ATS",
                "adapter_type": "MOCK",
                "scopes": ["jobs.read", "candidates.read"],
            },
        )
        assert create_res.status_code == 201
        created_conn_id = create_res.json()["connection_id"]

        # Recruiter in the same workspace discovers the connection
        app.dependency_overrides[require_recruiter] = lambda: AuthenticatedContext(
            user=recruiter,
            session=None,  # type: ignore
        )
        active_res = client.get("/api/v1/ats/active-connections")
        assert active_res.status_code == 200
        active_items = active_res.json()["items"]
        assert len(active_items) >= 1
        found = next((c for c in active_items if c["connection_id"] == created_conn_id), None)
        assert found is not None
        assert found["connection_name"] == "Shared Workspace Mock ATS"
    finally:
        app.dependency_overrides.clear()

