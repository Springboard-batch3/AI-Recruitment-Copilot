import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.ats import (
    ATSAdapterType,
    ATSExportStatus,
    ATSHealthStatus,
    ATSImportRunType,
    ATSImportStatus,
    ATSItemResultStatus,
    ATSObjectType,
    ATSReconciliationStatus,
)


class ATSConnectionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_name: str = Field(min_length=2, max_length=100)
    adapter_type: ATSAdapterType
    tenant_label: Optional[str] = Field(default=None, max_length=100)
    scopes: List[str] = Field(default_factory=lambda: ["jobs.read", "candidates.read", "notes.write"])
    secret_ref: Optional[str] = Field(default=None, max_length=255)
    config: Optional[Dict[str, Any]] = Field(default_factory=dict)

    @field_validator("secret_ref")
    @classmethod
    def validate_secret_ref(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return None
        if not cleaned.startswith("env:") and not cleaned.startswith("secret:"):
            raise ValueError("secret_ref must start with 'env:' or 'secret:'.")
        return cleaned


class ATSConnectionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    tenant_label: Optional[str] = Field(default=None, max_length=100)
    scopes: Optional[List[str]] = None
    is_enabled: Optional[bool] = None
    kill_switch_active: Optional[bool] = None
    secret_ref: Optional[str] = Field(default=None, max_length=255)
    config: Optional[Dict[str, Any]] = None

    @field_validator("secret_ref")
    @classmethod
    def validate_secret_ref(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return None
        if not cleaned.startswith("env:") and not cleaned.startswith("secret:"):
            raise ValueError("secret_ref must start with 'env:' or 'secret:'.")
        return cleaned


class ATSConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    connection_id: UUID
    organization_id: UUID
    connection_name: str
    adapter_type: ATSAdapterType
    tenant_label: Optional[str] = None
    scopes: List[str]
    is_enabled: bool
    health_status: ATSHealthStatus
    last_sync_at: Optional[datetime] = None
    secret_ref_masked: Optional[str] = None
    kill_switch_active: bool
    created_at: datetime
    updated_at: datetime


class ATSConnectionListResponse(BaseModel):
    items: List[ATSConnectionResponse]
    total_count: int


class ATSImportRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_dry_run: bool = False
    destination_job_id: Optional[UUID] = None
    destination_job_version_id: Optional[UUID] = None
    preview_token: Optional[str] = None
    preview_hash: Optional[str] = None
    raw_payload: Optional[Dict[str, Any]] = None
    file_content_b64: Optional[str] = None
    filename: Optional[str] = None


class ATSImportItemResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    result_id: UUID
    item_type: ATSObjectType
    external_id: str
    status: ATSItemResultStatus
    safe_error_message: Optional[str] = None
    internal_id: Optional[UUID] = None
    created_at: datetime


class ATSImportRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    connection_id: UUID
    run_type: ATSImportRunType
    is_dry_run: bool
    status: ATSImportStatus
    destination_job_id: Optional[UUID] = None
    destination_job_version_id: Optional[UUID] = None
    preview_token: Optional[str] = None
    preview_hash: Optional[str] = None
    expires_at: Optional[datetime] = None
    total_jobs_count: int
    total_candidates_count: int
    total_attachments_count: int
    successful_items_count: int
    failed_items_count: int
    duplicate_items_count: int
    changed_items_count: int
    summary_json: Dict[str, Any]
    error_message: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    items: List[ATSImportItemResultResponse] = Field(default_factory=list)


class ATSExportPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    candidate_id: UUID
    note_text: str = Field(min_length=1, max_length=10000)
    destination: str = Field(min_length=1, max_length=255)


class ATSExportPreviewResponse(BaseModel):
    preview_hash: str
    connection_id: UUID
    connection_name: str
    candidate_id: UUID
    candidate_name: str
    destination: str
    note_text: str
    warning: Optional[str] = None


class ATSExportConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    candidate_id: UUID
    preview_hash: str
    destination: str
    note_text: str = Field(min_length=1, max_length=10000)


class ATSExportReceiptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    receipt_id: UUID
    connection_id: UUID
    internal_candidate_id: UUID
    external_candidate_id: str
    preview_hash: str
    destination: str
    exported_note_text: str
    status: ATSExportStatus
    response_receipt: Dict[str, Any]
    created_at: datetime


class ATSReconciliationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reconciliation_id: UUID
    connection_id: UUID
    export_receipt_id: UUID
    status: ATSReconciliationStatus
    reason: str
    resolution_notes: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None
