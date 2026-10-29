from datetime import datetime
import logging
from typing import List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedContext, require_admin, require_recruiter
from app.database import get_db
from app.models.auth import UserRecord
from app.models.ats import (
    ATSConnectionRecord,
    ATSExportReceiptRecord,
    ATSHealthStatus,
    ATSImportItemResultRecord,
    ATSImportRunRecord,
    ATSReconciliationRecord,
)
from app.schemas.ats import (
    ATSConnectionCreateRequest,
    ATSConnectionListResponse,
    ATSConnectionResponse,
    ATSConnectionUpdateRequest,
    ATSExportConfirmRequest,
    ATSExportPreviewRequest,
    ATSExportPreviewResponse,
    ATSExportReceiptResponse,
    ATSImportItemResultResponse,
    ATSImportRunRequest,
    ATSImportRunResponse,
    ATSReconciliationResponse,
)
from app.services.ats.adapter_registry import get_ats_adapter
from app.services.ats.export_service import ATSExportService
from app.services.ats.import_service import ATSImportService

logger = logging.getLogger("ai_hiring.api.ats")

router = APIRouter(prefix="/api/v1/ats", tags=["ats"])

DEFAULT_WORKSPACE_ORG_ID = UUID("a0000000-0000-0000-0000-000000000001")


def mask_secret(secret_ref: Optional[str]) -> Optional[str]:
    if not secret_ref:
        return None
    if secret_ref.startswith("env:"):
        return secret_ref
    return "secret:••••••••"


def to_connection_response(conn: ATSConnectionRecord, is_recruiter: bool = False) -> ATSConnectionResponse:
    return ATSConnectionResponse(
        connection_id=conn.connection_id,
        organization_id=conn.organization_id,
        connection_name=conn.connection_name,
        adapter_type=conn.adapter_type,
        tenant_label=conn.tenant_label,
        scopes=conn.scopes or [],
        is_enabled=conn.is_enabled,
        health_status=conn.health_status,
        last_sync_at=conn.last_sync_at,
        secret_ref_masked=None if is_recruiter else mask_secret(conn.secret_ref),
        kill_switch_active=conn.kill_switch_active,
        created_at=conn.created_at,
        updated_at=conn.updated_at,
    )


def get_effective_org_id(user: Optional[UserRecord]) -> UUID:
    if user is None:
        return DEFAULT_WORKSPACE_ORG_ID
    org = getattr(user, "organization_id", None) or getattr(user, "org_id", None)
    if org is not None:
        if isinstance(org, str):
            try:
                return UUID(org)
            except ValueError:
                pass
        elif isinstance(org, UUID):
            return org
    return DEFAULT_WORKSPACE_ORG_ID


# ---------------------------------------------------------------------------
# Admin Endpoints (require_admin)
# ---------------------------------------------------------------------------

@router.post(
    "/connections",
    response_model=ATSConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create ATS connection (Admin only)",
)
def create_ats_connection(
    request: ATSConnectionCreateRequest,
    auth_ctx: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    # Ensure adapter is valid
    get_ats_adapter(request.adapter_type)
    org_id = get_effective_org_id(auth_ctx.user)

    conn = ATSConnectionRecord(
        connection_id=uuid4(),
        organization_id=org_id,
        connection_name=request.connection_name.strip(),
        adapter_type=request.adapter_type,
        tenant_label=request.tenant_label.strip() if request.tenant_label else None,
        scopes=request.scopes,
        is_enabled=True,
        health_status=ATSHealthStatus.UNKNOWN,
        secret_ref=request.secret_ref,
        encrypted_config=request.config or {},
        kill_switch_active=False,
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return to_connection_response(conn)


@router.get(
    "/connections",
    response_model=ATSConnectionListResponse,
    summary="List ATS connections for organization (Admin only)",
)
def list_ats_connections(
    auth_ctx: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    org_id = get_effective_org_id(auth_ctx.user)
    conns = db.execute(
        select(ATSConnectionRecord)
        .where(ATSConnectionRecord.organization_id == org_id)
        .order_by(ATSConnectionRecord.created_at.desc())
    ).scalars().all()

    return ATSConnectionListResponse(
        items=[to_connection_response(c) for c in conns],
        total_count=len(conns),
    )


@router.get(
    "/connections/{connection_id}",
    response_model=ATSConnectionResponse,
    summary="Get ATS connection details (Admin only)",
)
def get_ats_connection(
    connection_id: UUID,
    auth_ctx: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    org_id = get_effective_org_id(auth_ctx.user)
    conn = db.execute(
        select(ATSConnectionRecord).where(
            ATSConnectionRecord.connection_id == connection_id,
            ATSConnectionRecord.organization_id == org_id,
        )
    ).scalar_one_or_none()

    if not conn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ATS connection not found.",
        )
    return to_connection_response(conn)


@router.patch(
    "/connections/{connection_id}",
    response_model=ATSConnectionResponse,
    summary="Update ATS connection (Admin only)",
)
def update_ats_connection(
    connection_id: UUID,
    request: ATSConnectionUpdateRequest,
    auth_ctx: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    org_id = get_effective_org_id(auth_ctx.user)
    conn = db.execute(
        select(ATSConnectionRecord).where(
            ATSConnectionRecord.connection_id == connection_id,
            ATSConnectionRecord.organization_id == org_id,
        )
    ).scalar_one_or_none()

    if not conn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ATS connection not found.",
        )

    if request.connection_name is not None:
        conn.connection_name = request.connection_name.strip()
    if request.tenant_label is not None:
        conn.tenant_label = request.tenant_label.strip() if request.tenant_label else None
    if request.scopes is not None:
        conn.scopes = request.scopes
    if request.is_enabled is not None:
        conn.is_enabled = request.is_enabled
    if request.kill_switch_active is not None:
        conn.kill_switch_active = request.kill_switch_active
    if request.secret_ref is not None:
        conn.secret_ref = request.secret_ref
    if request.config is not None:
        conn.encrypted_config = request.config

    db.commit()
    db.refresh(conn)
    return to_connection_response(conn)


@router.post(
    "/connections/{connection_id}/test",
    response_model=ATSConnectionResponse,
    summary="Test ATS connection health (Admin only)",
)
def test_ats_connection(
    connection_id: UUID,
    auth_ctx: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
):
    org_id = get_effective_org_id(auth_ctx.user)
    conn = db.execute(
        select(ATSConnectionRecord).where(
            ATSConnectionRecord.connection_id == connection_id,
            ATSConnectionRecord.organization_id == org_id,
        )
    ).scalar_one_or_none()

    if not conn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ATS connection not found.",
        )

    adapter = get_ats_adapter(conn.adapter_type)
    health = adapter.healthcheck(conn)
    conn.health_status = health.status
    db.commit()
    db.refresh(conn)
    return to_connection_response(conn)


# ---------------------------------------------------------------------------
# Recruiter Endpoints (require_recruiter)
# ---------------------------------------------------------------------------

@router.get(
    "/active-connections",
    response_model=ATSConnectionListResponse,
    summary="List active ATS connections available for import/export",
)
def list_active_connections_for_recruiter(
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
):
    org_id = get_effective_org_id(auth_ctx.user)
    all_conns = db.execute(
        select(ATSConnectionRecord)
        .where(ATSConnectionRecord.organization_id == org_id)
        .order_by(ATSConnectionRecord.connection_name.asc())
    ).scalars().all()

    eligible_conns: List[ATSConnectionRecord] = []
    excluded_reasons: dict[str, int] = {
        "disabled": 0,
        "kill_switch_active": 0,
        "unsupported_adapter": 0,
    }

    for conn in all_conns:
        if not conn.is_enabled:
            excluded_reasons["disabled"] += 1
            continue
        if conn.kill_switch_active:
            excluded_reasons["kill_switch_active"] += 1
            continue
        try:
            get_ats_adapter(conn.adapter_type)
        except ValueError:
            excluded_reasons["unsupported_adapter"] += 1
            continue
        eligible_conns.append(conn)

    logger.info(
        "Recruiter ATS active connections evaluated",
        extra={
            "organization_id": str(org_id),
            "total_found": len(all_conns),
            "eligible_count": len(eligible_conns),
            "excluded_reasons": excluded_reasons,
            "result_category": "SUCCESS" if eligible_conns else "EMPTY",
        },
    )

    return ATSConnectionListResponse(
        items=[to_connection_response(c, is_recruiter=True) for c in eligible_conns],
        total_count=len(eligible_conns),
    )


@router.post(
    "/connections/{connection_id}/import",
    response_model=ATSImportRunResponse,
    summary="Execute ATS import or dry-run",
)
def trigger_ats_import(
    connection_id: UUID,
    request: ATSImportRunRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
):
    org_id = get_effective_org_id(auth_ctx.user)
    svc = ATSImportService(db)
    try:
        run = svc.execute_import(
            connection_id=connection_id,
            organization_id=org_id,
            user_id=auth_ctx.user.user_id,
            request=request,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Load items
    items = db.execute(
        select(ATSImportItemResultRecord)
        .where(ATSImportItemResultRecord.run_id == run.run_id)
        .order_by(ATSImportItemResultRecord.created_at.asc())
    ).scalars().all()

    resp = ATSImportRunResponse.model_validate(run)
    resp.items = [ATSImportItemResultResponse.model_validate(i) for i in items]
    if run.summary_json:
        if "destination_job_id" in run.summary_json and run.summary_json["destination_job_id"]:
            try:
                resp.destination_job_id = UUID(run.summary_json["destination_job_id"])
            except (ValueError, TypeError):
                pass
        if "destination_job_version_id" in run.summary_json and run.summary_json["destination_job_version_id"]:
            try:
                resp.destination_job_version_id = UUID(run.summary_json["destination_job_version_id"])
            except (ValueError, TypeError):
                pass
        resp.preview_token = run.summary_json.get("preview_token")
        resp.preview_hash = run.summary_json.get("preview_hash")
        if "expires_at" in run.summary_json and run.summary_json["expires_at"]:
            try:
                resp.expires_at = datetime.fromisoformat(run.summary_json["expires_at"])
            except (ValueError, TypeError):
                pass
    return resp


@router.get(
    "/import-runs/{run_id}",
    response_model=ATSImportRunResponse,
    summary="Get ATS import run results",
)
def get_import_run(
    run_id: UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
):
    org_id = get_effective_org_id(auth_ctx.user)
    run = db.execute(
        select(ATSImportRunRecord).where(
            ATSImportRunRecord.run_id == run_id,
            ATSImportRunRecord.organization_id == org_id,
        )
    ).scalar_one_or_none()

    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ATS import run not found.",
        )

    items = db.execute(
        select(ATSImportItemResultRecord)
        .where(ATSImportItemResultRecord.run_id == run.run_id)
        .order_by(ATSImportItemResultRecord.created_at.asc())
    ).scalars().all()

    resp = ATSImportRunResponse.model_validate(run)
    resp.items = [ATSImportItemResultResponse.model_validate(i) for i in items]
    if run.summary_json:
        if "destination_job_id" in run.summary_json and run.summary_json["destination_job_id"]:
            try:
                resp.destination_job_id = UUID(run.summary_json["destination_job_id"])
            except (ValueError, TypeError):
                pass
        resp.preview_token = run.summary_json.get("preview_token")
        resp.preview_hash = run.summary_json.get("preview_hash")
        if "expires_at" in run.summary_json and run.summary_json["expires_at"]:
            try:
                resp.expires_at = datetime.fromisoformat(run.summary_json["expires_at"])
            except (ValueError, TypeError):
                pass
    return resp


@router.post(
    "/export/preview",
    response_model=ATSExportPreviewResponse,
    summary="Preview outgoing recruiter note and compute preview hash",
)
def preview_ats_export_note(
    request: ATSExportPreviewRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
):
    org_id = get_effective_org_id(auth_ctx.user)
    svc = ATSExportService(db)
    try:
        return svc.generate_export_preview(org_id, request)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post(
    "/export/confirm",
    response_model=ATSExportReceiptResponse,
    summary="Confirm note export to ATS with verified preview hash",
)
def confirm_ats_export_note(
    request: ATSExportConfirmRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
):
    org_id = get_effective_org_id(auth_ctx.user)
    svc = ATSExportService(db)
    try:
        receipt = svc.confirm_export(org_id, auth_ctx.user.user_id, request)
        return ATSExportReceiptResponse.model_validate(receipt)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get(
    "/reconciliations",
    response_model=List[ATSReconciliationResponse],
    summary="List pending ATS reconciliations",
)
def list_reconciliations(
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
):
    org_id = get_effective_org_id(auth_ctx.user)
    recs = db.execute(
        select(ATSReconciliationRecord)
        .where(ATSReconciliationRecord.organization_id == org_id)
        .order_by(ATSReconciliationRecord.created_at.desc())
    ).scalars().all()

    return [ATSReconciliationResponse.model_validate(r) for r in recs]
