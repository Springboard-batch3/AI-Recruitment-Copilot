import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ats import (
    ATSConnectionRecord,
    ATSExportReceiptRecord,
    ATSExportStatus,
    ATSObjectMappingRecord,
    ATSObjectType,
    ATSReconciliationRecord,
    ATSReconciliationStatus,
)
from app.models.candidate import CandidateIdentityRecord
from app.schemas.ats import (
    ATSExportConfirmRequest,
    ATSExportPreviewRequest,
    ATSExportPreviewResponse,
    ATSExportReceiptResponse,
)
from app.services.ats.adapter_registry import get_ats_adapter

logger = logging.getLogger("ai_hiring.ats.export_service")


def compute_export_preview_hash(
    connection_id: UUID, candidate_id: UUID, destination: str, note_text: str
) -> str:
    payload = f"{connection_id}:{candidate_id}:{destination.strip()}:{note_text.strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ATSExportService:
    def __init__(self, db: Session):
        self.db = db

    def generate_export_preview(
        self, organization_id: UUID, request: ATSExportPreviewRequest
    ) -> ATSExportPreviewResponse:
        # 1. Fetch connection
        conn = self.db.execute(
            select(ATSConnectionRecord).where(
                ATSConnectionRecord.connection_id == request.connection_id,
                ATSConnectionRecord.organization_id == organization_id,
            )
        ).scalar_one_or_none()

        if not conn:
            raise ValueError("ATS connection not found or unauthorized.")

        if not conn.is_enabled:
            raise ValueError("ATS connection is disabled.")

        if conn.kill_switch_active:
            raise ValueError("ATS connection kill switch is active.")

        # 2. Fetch candidate
        cand = self.db.execute(
            select(CandidateIdentityRecord).where(
                CandidateIdentityRecord.identity_id == request.candidate_id
            )
        ).scalar_one_or_none()

        cand_name = cand.full_name if cand else "Candidate"

        preview_hash = compute_export_preview_hash(
            request.connection_id, request.candidate_id, request.destination, request.note_text
        )

        return ATSExportPreviewResponse(
            preview_hash=preview_hash,
            connection_id=request.connection_id,
            connection_name=conn.connection_name,
            candidate_id=request.candidate_id,
            candidate_name=cand_name,
            destination=request.destination.strip(),
            note_text=request.note_text.strip(),
            warning="Review outgoing note carefully. Exporting will push this note to the external ATS tenant.",
        )

    def confirm_export(
        self, organization_id: UUID, user_id: UUID, request: ATSExportConfirmRequest
    ) -> ATSExportReceiptRecord:
        # 1. Validate hash
        expected_hash = compute_export_preview_hash(
            request.connection_id, request.candidate_id, request.destination, request.note_text
        )

        if request.preview_hash != expected_hash:
            raise ValueError(
                "Export preview hash mismatch: Content or destination has changed since preview. Please generate a new preview."
            )

        # 2. Fetch connection
        conn = self.db.execute(
            select(ATSConnectionRecord).where(
                ATSConnectionRecord.connection_id == request.connection_id,
                ATSConnectionRecord.organization_id == organization_id,
            )
        ).scalar_one_or_none()

        if not conn or not conn.is_enabled or conn.kill_switch_active:
            raise ValueError("ATS connection is unavailable or disabled.")

        adapter = get_ats_adapter(conn.adapter_type)

        # 3. Lookup external ID mapping for candidate
        mapping = self.db.execute(
            select(ATSObjectMappingRecord).where(
                ATSObjectMappingRecord.organization_id == organization_id,
                ATSObjectMappingRecord.connection_id == request.connection_id,
                ATSObjectMappingRecord.object_type == ATSObjectType.CANDIDATE,
                ATSObjectMappingRecord.internal_id == request.candidate_id,
            )
        ).scalar_one_or_none()

        ext_candidate_id = mapping.external_id if mapping else str(request.candidate_id)

        # 4. Invoke adapter push_note
        push_res = adapter.push_note(
            connection=conn,
            external_candidate_id=ext_candidate_id,
            note_text=request.note_text,
            preview_hash=request.preview_hash,
        )

        receipt_id = uuid4()

        if push_res.is_timeout:
            # Uncertain write state -> RECONCILIATION_REQUIRED
            receipt = ATSExportReceiptRecord(
                receipt_id=receipt_id,
                organization_id=organization_id,
                connection_id=request.connection_id,
                internal_candidate_id=request.candidate_id,
                external_candidate_id=ext_candidate_id,
                preview_hash=request.preview_hash,
                destination=request.destination,
                exported_note_text=request.note_text,
                status=ATSExportStatus.RECONCILIATION_REQUIRED,
                response_receipt={"error": push_res.error_message, "is_timeout": True},
                created_at=datetime.now(timezone.utc),
            )
            reconciliation = ATSReconciliationRecord(
                reconciliation_id=uuid4(),
                organization_id=organization_id,
                connection_id=request.connection_id,
                export_receipt_id=receipt_id,
                status=ATSReconciliationStatus.PENDING,
                reason="Timeout during note export to external ATS. Manual verification required to confirm if note was created.",
                created_at=datetime.now(timezone.utc),
            )
            self.db.add(receipt)
            self.db.add(reconciliation)
            self.db.commit()
            self.db.refresh(receipt)
            return receipt

        if not push_res.success:
            receipt = ATSExportReceiptRecord(
                receipt_id=receipt_id,
                organization_id=organization_id,
                connection_id=request.connection_id,
                internal_candidate_id=request.candidate_id,
                external_candidate_id=ext_candidate_id,
                preview_hash=request.preview_hash,
                destination=request.destination,
                exported_note_text=request.note_text,
                status=ATSExportStatus.FAILED,
                response_receipt={"error": push_res.error_message},
                created_at=datetime.now(timezone.utc),
            )
            self.db.add(receipt)
            self.db.commit()
            self.db.refresh(receipt)
            return receipt

        # Success
        receipt = ATSExportReceiptRecord(
            receipt_id=receipt_id,
            organization_id=organization_id,
            connection_id=request.connection_id,
            internal_candidate_id=request.candidate_id,
            external_candidate_id=ext_candidate_id,
            preview_hash=request.preview_hash,
            destination=request.destination,
            exported_note_text=request.note_text,
            status=ATSExportStatus.SUCCESS,
            response_receipt=push_res.receipt,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(receipt)
        self.db.commit()
        self.db.refresh(receipt)
        return receipt
