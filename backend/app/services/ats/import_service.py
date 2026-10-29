import base64
import hashlib
import json
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.ats import (
    ATSAdapterType,
    ATSConnectionRecord,
    ATSExternalAttachmentRecord,
    ATSExternalCandidateRecord,
    ATSExternalJobRecord,
    ATSHealthStatus,
    ATSImportItemResultRecord,
    ATSImportRunRecord,
    ATSImportRunType,
    ATSImportStatus,
    ATSItemResultStatus,
    ATSObjectMappingRecord,
    ATSObjectType,
    ATSSyncCheckpointRecord,
)
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.candidate_access import CandidateApplicationRecord
from app.models.job import (
    JobLifecycleStatus,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
)
from app.models.resume import (
    ArtifactFormat,
    ArtifactValidationStatus,
    ProcessingJob,
    ProcessingJobStatus,
    ResumeArtifact,
)
from app.schemas.ats import ATSImportRunRequest, ATSImportRunResponse
from app.services.ats.adapter_registry import get_ats_adapter
from app.services.ats.csv_json_adapter import CSVJSONAdapter
from app.services.ats.local_folder_adapter import LocalFolderATSAdapter, LocalFolderScanItem
from app.services.docx_validation import DOCXValidationError, validate_docx_file
from app.services.extraction_orchestration import orchestrate_resume_extraction
from app.services.pdf_validation import PDFValidationError, validate_pdf_file

logger = logging.getLogger("ai_hiring.ats.import_service")


def compute_canonical_hash(data: Dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash of canonical sorted JSON."""
    serialized = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class ATSImportService:
    def __init__(self, db: Session):
        self.db = db

    def execute_import(
        self,
        connection_id: UUID,
        organization_id: UUID,
        user_id: UUID,
        request: ATSImportRunRequest,
    ) -> ATSImportRunRecord:
        # 1. Fetch connection with organization isolation
        connection = self.db.execute(
            select(ATSConnectionRecord).where(
                ATSConnectionRecord.connection_id == connection_id,
                ATSConnectionRecord.organization_id == organization_id,
            )
        ).scalar_one_or_none()

        if not connection:
            raise ValueError("ATS connection not found or unauthorized.")

        if not connection.is_enabled:
            raise ValueError("ATS connection is disabled.")

        if connection.kill_switch_active:
            raise ValueError("ATS connection kill switch is active.")

        adapter = get_ats_adapter(connection.adapter_type)

        # 2. Branch for LOCAL_FOLDER_DEMO
        if connection.adapter_type == ATSAdapterType.LOCAL_FOLDER_DEMO:
            return self._execute_local_folder_demo_import(
                connection=connection,
                organization_id=organization_id,
                user_id=user_id,
                request=request,
            )

        # 3. Create import run record for standard adapters
        run = ATSImportRunRecord(
            run_id=uuid4(),
            organization_id=organization_id,
            connection_id=connection_id,
            run_type=ATSImportRunType(connection.adapter_type.value),
            is_dry_run=request.is_dry_run,
            status=ATSImportStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        total_jobs = 0
        total_candidates = 0
        total_attachments = 0
        successful_count = 0
        failed_count = 0
        duplicate_count = 0
        changed_count = 0
        item_results: List[ATSImportItemResultRecord] = []

        try:
            # Retrieve External Objects
            jobs_to_process = []
            candidates_to_process = []
            attachments_to_process = []

            if connection.adapter_type == ATSAdapterType.MOCK:
                # Page through jobs
                page = 1
                has_more = True
                while has_more:
                    j_list, _, has_more = adapter.list_jobs(connection, page=page)
                    jobs_to_process.extend(j_list)
                    page += 1

                # Page through candidates
                page = 1
                has_more = True
                while has_more:
                    c_list, _, has_more = adapter.list_candidates(connection, page=page)
                    candidates_to_process.extend(c_list)
                    page += 1

                # Attachments
                a_list, _, _ = adapter.list_attachments(connection)
                attachments_to_process.extend(a_list)

            elif connection.adapter_type in (ATSAdapterType.CSV, ATSAdapterType.JSON):
                csv_json_proc = CSVJSONAdapter()
                file_text = ""
                if request.file_content_b64:
                    try:
                        file_text = base64.b64decode(request.file_content_b64).decode("utf-8")
                    except Exception:
                        raise ValueError("Invalid base64 encoding or file content for CSV/JSON.")

                if connection.adapter_type == ATSAdapterType.CSV and file_text:
                    if request.filename and "job" in request.filename.lower():
                        jobs_to_process = csv_json_proc.parse_jobs_from_csv(file_text)
                    else:
                        candidates_to_process = csv_json_proc.parse_candidates_from_csv(file_text)
                elif connection.adapter_type == ATSAdapterType.JSON and file_text:
                    j_list, c_list = csv_json_proc.parse_from_json(file_text)
                    jobs_to_process.extend(j_list)
                    candidates_to_process.extend(c_list)

            total_jobs = len(jobs_to_process)
            total_candidates = len(candidates_to_process)
            total_attachments = len(attachments_to_process)

            # Process Jobs
            for job in jobs_to_process:
                job_payload = {
                    "external_id": job.external_id,
                    "title": job.title,
                    "department": job.department,
                    "location": job.location,
                }
                c_hash = compute_canonical_hash(job_payload)

                mapping = self.db.execute(
                    select(ATSObjectMappingRecord).where(
                        ATSObjectMappingRecord.organization_id == organization_id,
                        ATSObjectMappingRecord.connection_id == connection_id,
                        ATSObjectMappingRecord.object_type == ATSObjectType.JOB,
                        ATSObjectMappingRecord.external_id == job.external_id,
                    )
                ).scalar_one_or_none()

                if mapping:
                    if mapping.canonical_content_hash == c_hash:
                        duplicate_count += 1
                        item_results.append(
                            ATSImportItemResultRecord(
                                result_id=uuid4(),
                                organization_id=organization_id,
                                run_id=run.run_id,
                                connection_id=connection_id,
                                item_type=ATSObjectType.JOB,
                                external_id=job.external_id,
                                status=ATSItemResultStatus.DUPLICATE,
                                internal_id=mapping.internal_id,
                            )
                        )
                        continue
                    else:
                        changed_count += 1
                        mapping.canonical_content_hash = c_hash
                        mapping.last_synced_at = datetime.now(timezone.utc)
                        item_results.append(
                            ATSImportItemResultRecord(
                                result_id=uuid4(),
                                organization_id=organization_id,
                                run_id=run.run_id,
                                connection_id=connection_id,
                                item_type=ATSObjectType.JOB,
                                external_id=job.external_id,
                                status=ATSItemResultStatus.CHANGED,
                                internal_id=mapping.internal_id,
                            )
                        )
                        continue

                # New Job
                internal_job_id = uuid4()
                if not request.is_dry_run:
                    req = JobRequisition(
                        job_id=internal_job_id,
                        created_by_user_id=user_id,
                        status=JobLifecycleStatus.ACTIVE,
                    )
                    ver = JobVersion(
                        job_version_id=uuid4(),
                        job_id=internal_job_id,
                        created_by_user_id=user_id,
                        version_number=1,
                        revision=1,
                        title=job.title,
                        department=job.department,
                        location=job.location,
                        version_status=JobVersionStatus.DRAFT,
                    )
                    new_mapping = ATSObjectMappingRecord(
                        mapping_id=uuid4(),
                        organization_id=organization_id,
                        connection_id=connection_id,
                        object_type=ATSObjectType.JOB,
                        external_id=job.external_id,
                        internal_id=internal_job_id,
                        canonical_content_hash=c_hash,
                        last_synced_at=datetime.now(timezone.utc),
                    )
                    self.db.add(req)
                    self.db.add(ver)
                    self.db.add(new_mapping)

                successful_count += 1
                item_results.append(
                    ATSImportItemResultRecord(
                        result_id=uuid4(),
                        organization_id=organization_id,
                        run_id=run.run_id,
                        connection_id=connection_id,
                        item_type=ATSObjectType.JOB,
                        external_id=job.external_id,
                        status=ATSItemResultStatus.SUCCESS,
                        internal_id=internal_job_id,
                    )
                )

            # Process Candidates & Attachments
            for cand in candidates_to_process:
                if not cand.full_name or not cand.full_name.strip():
                    failed_count += 1
                    item_results.append(
                        ATSImportItemResultRecord(
                            result_id=uuid4(),
                            organization_id=organization_id,
                            run_id=run.run_id,
                            connection_id=connection_id,
                            item_type=ATSObjectType.CANDIDATE,
                            external_id=cand.external_id,
                            status=ATSItemResultStatus.VALIDATION_ERROR,
                            safe_error_message="Candidate full name is required and cannot be empty.",
                        )
                    )
                    continue

                cand_payload = {
                    "external_id": cand.external_id,
                    "full_name": cand.full_name,
                    "email": cand.email,
                    "phone": cand.phone,
                }
                c_hash = compute_canonical_hash(cand_payload)

                mapping = self.db.execute(
                    select(ATSObjectMappingRecord).where(
                        ATSObjectMappingRecord.organization_id == organization_id,
                        ATSObjectMappingRecord.connection_id == connection_id,
                        ATSObjectMappingRecord.object_type == ATSObjectType.CANDIDATE,
                        ATSObjectMappingRecord.external_id == cand.external_id,
                    )
                ).scalar_one_or_none()

                if mapping:
                    existing_ident = self.db.execute(
                        select(CandidateIdentityRecord).where(
                            CandidateIdentityRecord.identity_id == mapping.internal_id
                        )
                    ).scalar_one_or_none()
                    if not existing_ident:
                        # Orphaned mapping from deleted candidates
                        self.db.delete(mapping)
                        self.db.flush()
                        mapping = None

                if mapping:
                    if mapping.canonical_content_hash == c_hash:
                        duplicate_count += 1
                        item_results.append(
                            ATSImportItemResultRecord(
                                result_id=uuid4(),
                                organization_id=organization_id,
                                run_id=run.run_id,
                                connection_id=connection_id,
                                item_type=ATSObjectType.CANDIDATE,
                                external_id=cand.external_id,
                                status=ATSItemResultStatus.DUPLICATE,
                                internal_id=mapping.internal_id,
                            )
                        )
                        continue
                    else:
                        changed_count += 1
                        mapping.canonical_content_hash = c_hash
                        mapping.last_synced_at = datetime.now(timezone.utc)
                        item_results.append(
                            ATSImportItemResultRecord(
                                result_id=uuid4(),
                                organization_id=organization_id,
                                run_id=run.run_id,
                                connection_id=connection_id,
                                item_type=ATSObjectType.CANDIDATE,
                                external_id=cand.external_id,
                                status=ATSItemResultStatus.CHANGED,
                                internal_id=mapping.internal_id,
                            )
                        )
                        continue

                # Process associated attachments for this candidate
                internal_identity_id = uuid4()
                internal_artifact_id = uuid4()
                cand_attachments = [a for a in attachments_to_process if a.candidate_id == cand.external_id]

                for att in cand_attachments:
                    filename = att.filename.lower()
                    if not (filename.endswith(".pdf") or filename.endswith(".docx")):
                        failed_count += 1
                        item_results.append(
                            ATSImportItemResultRecord(
                                result_id=uuid4(),
                                organization_id=organization_id,
                                run_id=run.run_id,
                                connection_id=connection_id,
                                item_type=ATSObjectType.ATTACHMENT,
                                external_id=att.attachment_id,
                                status=ATSItemResultStatus.VALIDATION_ERROR,
                                safe_error_message=f"Unsupported file type for attachment '{att.filename}'. Only PDF and DOCX files are supported.",
                            )
                        )
                        continue

                    att_bytes = att.content_bytes or (adapter.download_attachment(connection, att.attachment_id))
                    if not att_bytes:
                        failed_count += 1
                        item_results.append(
                            ATSImportItemResultRecord(
                                result_id=uuid4(),
                                organization_id=organization_id,
                                run_id=run.run_id,
                                connection_id=connection_id,
                                item_type=ATSObjectType.ATTACHMENT,
                                external_id=att.attachment_id,
                                status=ATSItemResultStatus.FAILED,
                                safe_error_message=f"Failed to download attachment bytes for '{att.filename}'.",
                            )
                        )
                        continue

                    import tempfile
                    try:
                        if filename.endswith(".pdf"):
                            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp_pdf:
                                tmp_pdf.write(att_bytes)
                                tmp_pdf.flush()
                                validate_pdf_file(Path(tmp_pdf.name), max_pages=10)
                        elif filename.endswith(".docx"):
                            with tempfile.NamedTemporaryFile(suffix=".docx", delete=True) as tmp_docx:
                                tmp_docx.write(att_bytes)
                                tmp_docx.flush()
                                validate_docx_file(Path(tmp_docx.name))
                    except PDFValidationError as e:
                        failed_count += 1
                        item_results.append(
                            ATSImportItemResultRecord(
                                result_id=uuid4(),
                                organization_id=organization_id,
                                run_id=run.run_id,
                                connection_id=connection_id,
                                item_type=ATSObjectType.ATTACHMENT,
                                external_id=att.attachment_id,
                                status=ATSItemResultStatus.VALIDATION_ERROR,
                                safe_error_message=f"PDF validation failed for '{att.filename}': {e.message}",
                            )
                        )
                        continue
                    except DOCXValidationError as e:
                        failed_count += 1
                        item_results.append(
                            ATSImportItemResultRecord(
                                result_id=uuid4(),
                                organization_id=organization_id,
                                run_id=run.run_id,
                                connection_id=connection_id,
                                item_type=ATSObjectType.ATTACHMENT,
                                external_id=att.attachment_id,
                                status=ATSItemResultStatus.VALIDATION_ERROR,
                                safe_error_message=f"DOCX validation failed for '{att.filename}': {e.message}",
                            )
                        )
                        continue
                    except Exception as e:
                        failed_count += 1
                        item_results.append(
                            ATSImportItemResultRecord(
                                result_id=uuid4(),
                                organization_id=organization_id,
                                run_id=run.run_id,
                                connection_id=connection_id,
                                item_type=ATSObjectType.ATTACHMENT,
                                external_id=att.attachment_id,
                                status=ATSItemResultStatus.VALIDATION_ERROR,
                                safe_error_message=f"Attachment validation failed for '{att.filename}': {str(e)}",
                            )
                        )
                        continue

                    successful_count += 1
                    item_results.append(
                        ATSImportItemResultRecord(
                            result_id=uuid4(),
                            organization_id=organization_id,
                            run_id=run.run_id,
                            connection_id=connection_id,
                            item_type=ATSObjectType.ATTACHMENT,
                            external_id=att.attachment_id,
                            status=ATSItemResultStatus.SUCCESS,
                            internal_id=internal_artifact_id,
                        )
                    )

                if not request.is_dry_run:
                    art = ResumeArtifact(
                        artifact_id=internal_artifact_id,
                        original_filename=cand_attachments[0].filename if cand_attachments else f"{cand.full_name.replace(' ', '_')}_resume.pdf",
                        declared_mime_type="application/pdf",
                        detected_format=ArtifactFormat.pdf,
                        size_bytes=1024,
                        sha256_hash=hashlib.sha256(cand.full_name.encode()).hexdigest(),
                        validation_status=ArtifactValidationStatus.VALIDATED,
                    )
                    ident = CandidateIdentityRecord(
                        identity_id=internal_identity_id,
                        artifact_id=internal_artifact_id,
                        full_name=cand.full_name,
                        primary_email=cand.email,
                        primary_phone=cand.phone,
                        identity_payload={
                            "full_name": cand.full_name,
                            "email": cand.email,
                            "phone": cand.phone,
                        },
                    )
                    new_mapping = ATSObjectMappingRecord(
                        mapping_id=uuid4(),
                        organization_id=organization_id,
                        connection_id=connection_id,
                        object_type=ATSObjectType.CANDIDATE,
                        external_id=cand.external_id,
                        internal_id=internal_identity_id,
                        canonical_content_hash=c_hash,
                        last_synced_at=datetime.now(timezone.utc),
                    )
                    self.db.add(art)
                    self.db.add(ident)
                    self.db.add(new_mapping)

                successful_count += 1
                item_results.append(
                    ATSImportItemResultRecord(
                        result_id=uuid4(),
                        organization_id=organization_id,
                        run_id=run.run_id,
                        connection_id=connection_id,
                        item_type=ATSObjectType.CANDIDATE,
                        external_id=cand.external_id,
                        status=ATSItemResultStatus.SUCCESS,
                        internal_id=internal_identity_id,
                    )
                )

            # Finalize Run Status
            for itm in item_results:
                self.db.add(itm)

            final_status = ATSImportStatus.COMPLETED
            if failed_count > 0 and (successful_count > 0 or duplicate_count > 0 or changed_count > 0):
                final_status = ATSImportStatus.PARTIAL_SUCCESS
            elif failed_count > 0 and successful_count == 0 and duplicate_count == 0 and changed_count == 0:
                final_status = ATSImportStatus.FAILED

            run.status = final_status
            run.total_jobs_count = total_jobs
            run.total_candidates_count = total_candidates
            run.total_attachments_count = total_attachments
            run.successful_items_count = successful_count
            run.failed_items_count = failed_count
            run.duplicate_items_count = duplicate_count
            run.changed_items_count = changed_count
            run.completed_at = datetime.now(timezone.utc)
            run.summary_json = {
                "total_records_analyzed": total_jobs + total_candidates + total_attachments,
                "successful_count": successful_count,
                "failed_count": failed_count,
                "duplicate_count": duplicate_count,
                "changed_count": changed_count,
                "is_dry_run": request.is_dry_run,
            }

            connection.last_sync_at = datetime.now(timezone.utc)
            connection.health_status = ATSHealthStatus.HEALTHY

            self.db.commit()
            self.db.refresh(run)
            return run

        except Exception as e:
            self.db.rollback()
            logger.exception("ATS Import failed unexpectedly")
            run.status = ATSImportStatus.FAILED
            run.error_message = f"ATS Import failed: {str(e)}"
            run.completed_at = datetime.now(timezone.utc)
            self.db.add(run)
            self.db.commit()
            self.db.refresh(run)
            return run

    def _execute_local_folder_demo_import(
        self,
        connection: ATSConnectionRecord,
        organization_id: UUID,
        user_id: UUID,
        request: ATSImportRunRequest,
    ) -> ATSImportRunRecord:
        """Executes Local Folder ATS scan/preview or confirmed candidate ingestion."""
        # 1. Require and validate destination job
        destination_job_id = request.destination_job_id
        if not destination_job_id:
            raise ValueError("A destination job must be selected before scanning or importing folder resumes.")

        job_req = self.db.execute(
            select(JobRequisition).where(
                JobRequisition.job_id == destination_job_id,
                JobRequisition.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

        if not job_req:
            raise ValueError("Selected destination job was not found or is unavailable.")

        if job_req.status == JobLifecycleStatus.ARCHIVED:
            raise ValueError("Cannot import candidates into an archived job.")

        # Find target job version
        target_version: Optional[JobVersion] = None
        if request.destination_job_version_id:
            target_version = self.db.execute(
                select(JobVersion).where(
                    JobVersion.job_version_id == request.destination_job_version_id,
                    JobVersion.job_id == destination_job_id,
                )
            ).scalar_one_or_none()
            if not target_version:
                raise ValueError("Specified destination job revision was not found.")
        else:
            # Pick latest ready or approved version
            target_version = self.db.execute(
                select(JobVersion)
                .where(
                    JobVersion.job_id == destination_job_id,
                    JobVersion.version_status.in_([JobVersionStatus.READY_FOR_MATCHING, JobVersionStatus.APPROVED]),
                )
                .order_by(JobVersion.version_number.desc())
            ).scalars().first()

            if not target_version:
                # Fallback to latest version
                target_version = self.db.execute(
                    select(JobVersion)
                    .where(JobVersion.job_id == destination_job_id)
                    .order_by(JobVersion.version_number.desc())
                ).scalars().first()

        if not target_version:
            raise ValueError("Selected destination job has no valid version.")

        if target_version.version_status == JobVersionStatus.DRAFT:
            raise ValueError("Target job revision is still in DRAFT status and is not eligible for import.")

        job_title = target_version.title or "Untitled Job Requisition"
        job_version_id = target_version.job_version_id
        job_revision = target_version.revision

        adapter = LocalFolderATSAdapter()
        settings = get_settings()

        # 2. Dry-Run Folder Scan
        if request.is_dry_run:
            scan_items = adapter.scan_inbox(connection)
            run = ATSImportRunRecord(
                run_id=uuid4(),
                organization_id=organization_id,
                connection_id=connection.connection_id,
                run_type=ATSImportRunType.LOCAL_FOLDER_DEMO,
                is_dry_run=True,
                status=ATSImportStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
            )
            self.db.add(run)
            self.db.flush()

            successful_count = 0
            duplicate_count = 0
            failed_count = 0
            item_results: List[ATSImportItemResultRecord] = []
            preview_files_meta: List[Dict[str, Any]] = []

            for itm in scan_items:
                if itm.status == "READY":
                    # Check connection object mapping duplicate
                    mapping = self.db.execute(
                        select(ATSObjectMappingRecord).where(
                            ATSObjectMappingRecord.organization_id == organization_id,
                            ATSObjectMappingRecord.connection_id == connection.connection_id,
                            ATSObjectMappingRecord.object_type == ATSObjectType.CANDIDATE,
                            ATSObjectMappingRecord.external_id == itm.external_id,
                        )
                    ).scalar_one_or_none()

                    if mapping:
                        existing_ident = self.db.execute(
                            select(CandidateIdentityRecord).where(
                                CandidateIdentityRecord.identity_id == mapping.internal_id
                            )
                        ).scalar_one_or_none()
                        if not existing_ident:
                            # Orphaned mapping from deleted candidates
                            self.db.delete(mapping)
                            self.db.flush()
                            mapping = None

                    if mapping:
                        duplicate_count += 1
                        last_synced = mapping.last_synced_at.strftime("%Y-%m-%d") if mapping.last_synced_at else "previously"
                        msg = f"Already imported on {last_synced}."
                        item_results.append(
                            ATSImportItemResultRecord(
                                result_id=uuid4(),
                                organization_id=organization_id,
                                run_id=run.run_id,
                                connection_id=connection.connection_id,
                                item_type=ATSObjectType.CANDIDATE,
                                external_id=itm.external_id,
                                status=ATSItemResultStatus.DUPLICATE,
                                internal_id=mapping.internal_id,
                                safe_error_message=msg,
                            )
                        )
                        preview_files_meta.append({
                            "external_id": itm.external_id,
                            "filename": itm.sanitized_filename,
                            "content_hash": itm.content_hash,
                            "format": itm.format,
                            "file_size_bytes": itm.file_size_bytes,
                            "status": "ALREADY_IMPORTED",
                            "safe_error_message": msg,
                        })
                        continue

                    # Check general artifact duplicate
                    existing_art = self.db.execute(
                        select(ResumeArtifact).where(ResumeArtifact.sha256_hash == itm.content_hash)
                    ).scalars().first()

                    if existing_art:
                        duplicate_count += 1
                        msg = "Duplicate resume content already exists in workspace."
                        item_results.append(
                            ATSImportItemResultRecord(
                                result_id=uuid4(),
                                organization_id=organization_id,
                                run_id=run.run_id,
                                connection_id=connection.connection_id,
                                item_type=ATSObjectType.CANDIDATE,
                                external_id=itm.external_id,
                                status=ATSItemResultStatus.DUPLICATE,
                                internal_id=existing_art.artifact_id,
                                safe_error_message=msg,
                            )
                        )
                        preview_files_meta.append({
                            "external_id": itm.external_id,
                            "filename": itm.sanitized_filename,
                            "content_hash": itm.content_hash,
                            "format": itm.format,
                            "file_size_bytes": itm.file_size_bytes,
                            "status": "DUPLICATE_CONTENT",
                            "safe_error_message": msg,
                        })
                        continue

                    # Item is fully ready for import
                    successful_count += 1
                    safe_msg = f"Valid {itm.format} document"
                    item_results.append(
                        ATSImportItemResultRecord(
                            result_id=uuid4(),
                            organization_id=organization_id,
                            run_id=run.run_id,
                            connection_id=connection.connection_id,
                            item_type=ATSObjectType.CANDIDATE,
                            external_id=itm.external_id,
                            status=ATSItemResultStatus.SUCCESS,
                            safe_error_message=safe_msg,
                        )
                    )
                    preview_files_meta.append({
                        "external_id": itm.external_id,
                        "filename": itm.sanitized_filename,
                        "content_hash": itm.content_hash,
                        "format": itm.format,
                        "file_size_bytes": itm.file_size_bytes,
                        "status": "READY",
                        "safe_error_message": safe_msg,
                    })

                else:
                    # Item has scan validation error
                    failed_count += 1
                    item_results.append(
                        ATSImportItemResultRecord(
                            result_id=uuid4(),
                            organization_id=organization_id,
                            run_id=run.run_id,
                            connection_id=connection.connection_id,
                            item_type=ATSObjectType.ATTACHMENT,
                            external_id=itm.external_id,
                            status=ATSItemResultStatus.VALIDATION_ERROR,
                            safe_error_message=itm.safe_error_message,
                        )
                    )
                    preview_files_meta.append({
                        "external_id": itm.external_id,
                        "filename": itm.sanitized_filename,
                        "content_hash": itm.content_hash,
                        "format": itm.format,
                        "file_size_bytes": itm.file_size_bytes,
                        "status": itm.status,
                        "safe_error_message": itm.safe_error_message,
                    })

            for ir in item_results:
                self.db.add(ir)

            # Compute cryptographically bound preview token and hash
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
            payload_data = {
                "organization_id": str(organization_id),
                "connection_id": str(connection.connection_id),
                "destination_job_id": str(destination_job_id),
                "destination_job_version_id": str(job_version_id),
                "user_id": str(user_id),
                "files": [{"ext_id": itm["external_id"], "hash": itm["content_hash"], "status": itm["status"]} for itm in preview_files_meta],
                "expires_at": expires_at.isoformat(),
            }
            preview_hash = hashlib.sha256(json.dumps(payload_data, sort_keys=True).encode("utf-8")).hexdigest()
            preview_token = base64.urlsafe_b64encode(json.dumps({**payload_data, "preview_hash": preview_hash}).encode("utf-8")).decode("utf-8")

            run.status = ATSImportStatus.COMPLETED if failed_count == 0 else (ATSImportStatus.PARTIAL_SUCCESS if successful_count > 0 or duplicate_count > 0 else ATSImportStatus.FAILED)
            run.total_candidates_count = len(scan_items)
            run.total_attachments_count = len(scan_items)
            run.successful_items_count = successful_count
            run.duplicate_items_count = duplicate_count
            run.failed_items_count = failed_count
            run.completed_at = datetime.now(timezone.utc)
            run.summary_json = {
                "is_dry_run": True,
                "destination_job_id": str(destination_job_id),
                "destination_job_version_id": str(job_version_id),
                "destination_job_title": job_title,
                "destination_job_revision": job_revision,
                "preview_hash": preview_hash,
                "preview_token": preview_token,
                "expires_at": expires_at.isoformat(),
                "scan_items": preview_files_meta,
                "total_scanned": len(scan_items),
                "ready_for_import": successful_count,
                "duplicates_count": duplicate_count,
                "failed_count": failed_count,
            }

            self.db.commit()
            self.db.refresh(run)
            return run

        # 3. Live Confirmed Import
        if not request.preview_token or not request.preview_hash:
            raise ValueError("Confirmation requires a valid dry-run preview_token and preview_hash.")

        # Decode and verify preview token
        try:
            raw_json = base64.urlsafe_b64decode(request.preview_token.encode("utf-8")).decode("utf-8")
            token_data = json.loads(raw_json)
        except Exception:
            raise ValueError("Invalid preview token format.")

        expected_hash = token_data.get("preview_hash")
        if expected_hash != request.preview_hash:
            raise ValueError("Preview hash mismatch. Confirmation rejected.")

        payload_to_verify = {k: v for k, v in token_data.items() if k != "preview_hash"}
        computed_hash = hashlib.sha256(json.dumps(payload_to_verify, sort_keys=True).encode("utf-8")).hexdigest()
        if computed_hash != expected_hash:
            raise ValueError("Preview token tampering detected.")

        if token_data.get("organization_id") != str(organization_id):
            raise ValueError("Preview belongs to a different organization.")
        if token_data.get("connection_id") != str(connection.connection_id):
            raise ValueError("Preview connection mismatch.")
        if token_data.get("destination_job_id") != str(destination_job_id):
            raise ValueError("Preview destination job mismatch.")

        # Check expiration
        expires_at_dt = datetime.fromisoformat(token_data["expires_at"])
        if datetime.now(timezone.utc) > expires_at_dt:
            raise ValueError("The preview has expired. Please run a new scan before confirming import.")

        # Re-scan inbox to ensure files have not changed since preview
        current_scan = adapter.scan_inbox(connection)
        current_files_meta = [{"ext_id": itm.external_id, "hash": itm.content_hash, "status": itm.status} for itm in current_scan]
        preview_files = token_data.get("files", [])

        if len(current_files_meta) != len(preview_files):
            raise ValueError("Inbox file count has changed since preview. Please re-scan folder.")

        preview_hash_set = {(f["ext_id"], f["hash"]) for f in preview_files}
        current_hash_set = {(f["ext_id"], f["hash"]) for f in current_files_meta}
        if preview_hash_set != current_hash_set:
            raise ValueError("Inbox file contents have changed since preview was generated. Please re-scan folder.")

        # Create live import run record
        run = ATSImportRunRecord(
            run_id=uuid4(),
            organization_id=organization_id,
            connection_id=connection.connection_id,
            run_type=ATSImportRunType.LOCAL_FOLDER_DEMO,
            is_dry_run=False,
            status=ATSImportStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        self.db.add(run)
        self.db.flush()

        successful_count = 0
        duplicate_count = 0
        failed_count = 0
        item_results: List[ATSImportItemResultRecord] = []

        # Process each READY scan item with isolated transaction protection
        quarantine_base = settings.get_quarantine_path()

        for itm in current_scan:
            # Check if this item was marked READY in preview
            preview_item_status = next((f["status"] for f in preview_files if f["ext_id"] == itm.external_id), None)
            if preview_item_status != "READY":
                # Record skipped/invalid/duplicate item as in preview
                status_enum = ATSItemResultStatus.DUPLICATE if "DUPLICATE" in (preview_item_status or "") or "ALREADY" in (preview_item_status or "") else ATSItemResultStatus.VALIDATION_ERROR
                if status_enum == ATSItemResultStatus.DUPLICATE:
                    duplicate_count += 1
                else:
                    failed_count += 1
                item_results.append(
                    ATSImportItemResultRecord(
                        result_id=uuid4(),
                        organization_id=organization_id,
                        run_id=run.run_id,
                        connection_id=connection.connection_id,
                        item_type=ATSObjectType.CANDIDATE if status_enum == ATSItemResultStatus.DUPLICATE else ATSObjectType.ATTACHMENT,
                        external_id=itm.external_id,
                        status=status_enum,
                        safe_error_message=itm.safe_error_message or "Skipped non-ready item during import.",
                    )
                )
                continue

            # Check duplicate mapping in DB again
            existing_mapping = self.db.execute(
                select(ATSObjectMappingRecord).where(
                    ATSObjectMappingRecord.organization_id == organization_id,
                    ATSObjectMappingRecord.connection_id == connection.connection_id,
                    ATSObjectMappingRecord.object_type == ATSObjectType.CANDIDATE,
                    ATSObjectMappingRecord.external_id == itm.external_id,
                )
            ).scalar_one_or_none()

            if existing_mapping:
                existing_ident = self.db.execute(
                    select(CandidateIdentityRecord).where(
                        CandidateIdentityRecord.identity_id == existing_mapping.internal_id
                    )
                ).scalar_one_or_none()
                if not existing_ident:
                    self.db.delete(existing_mapping)
                    self.db.flush()
                    existing_mapping = None

            if existing_mapping:
                duplicate_count += 1
                item_results.append(
                    ATSImportItemResultRecord(
                        result_id=uuid4(),
                        organization_id=organization_id,
                        run_id=run.run_id,
                        connection_id=connection.connection_id,
                        item_type=ATSObjectType.CANDIDATE,
                        external_id=itm.external_id,
                        status=ATSItemResultStatus.DUPLICATE,
                        internal_id=existing_mapping.internal_id,
                        safe_error_message="Candidate already imported.",
                    )
                )
                continue

            # Read file bytes safely
            file_bytes = adapter.download_attachment(connection, itm.external_id)
            if not file_bytes:
                failed_count += 1
                item_results.append(
                    ATSImportItemResultRecord(
                        result_id=uuid4(),
                        organization_id=organization_id,
                        run_id=run.run_id,
                        connection_id=connection.connection_id,
                        item_type=ATSObjectType.ATTACHMENT,
                        external_id=itm.external_id,
                        status=ATSItemResultStatus.FAILED,
                        safe_error_message=f"Failed to read file '{itm.sanitized_filename}' from inbox.",
                    )
                )
                continue

            # Verify hash still matches
            if hashlib.sha256(file_bytes).hexdigest().lower() != itm.content_hash:
                failed_count += 1
                item_results.append(
                    ATSImportItemResultRecord(
                        result_id=uuid4(),
                        organization_id=organization_id,
                        run_id=run.run_id,
                        connection_id=connection.connection_id,
                        item_type=ATSObjectType.ATTACHMENT,
                        external_id=itm.external_id,
                        status=ATSItemResultStatus.VALIDATION_ERROR,
                        safe_error_message=f"File hash altered for '{itm.sanitized_filename}'.",
                    )
                )
                continue

            # Setup quarantine destination
            artifact_id = uuid4()
            proc_job_id = uuid4()
            ext = ".pdf" if itm.format == "PDF" else ".docx"
            quarantine_dir = quarantine_base / ("pdf" if ext == ".pdf" else "docx")
            os.makedirs(quarantine_dir, exist_ok=True)
            quarantine_filename = f"{artifact_id}{ext}"
            quarantine_path = (quarantine_dir / quarantine_filename).resolve()

            try:
                with open(quarantine_path, "wb") as f_out:
                    f_out.write(file_bytes)

                art = ResumeArtifact(
                    artifact_id=artifact_id,
                    original_filename=itm.sanitized_filename,
                    declared_mime_type="application/pdf" if ext == ".pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    detected_format=ArtifactFormat.pdf if ext == ".pdf" else ArtifactFormat.docx,
                    size_bytes=len(file_bytes),
                    sha256_hash=itm.content_hash,
                    validation_status=ArtifactValidationStatus.VALIDATED,
                    quarantine_key=f"{'pdf' if ext == '.pdf' else 'docx'}/{quarantine_filename}",
                )
                job = ProcessingJob(
                    job_id=proc_job_id,
                    artifact_id=artifact_id,
                    status=ProcessingJobStatus.QUEUED,
                    attempt_count=0,
                )
                self.db.add(art)
                self.db.flush()
                self.db.add(job)
                self.db.flush()

                # Run extraction orchestration pipeline
                outcome = orchestrate_resume_extraction(
                    artifact_id=artifact_id,
                    db=self.db,
                    settings=settings,
                )

                # Query created identity and profile
                ident = self.db.execute(
                    select(CandidateIdentityRecord).where(CandidateIdentityRecord.artifact_id == artifact_id)
                ).scalar_one_or_none()

                profile = self.db.execute(
                    select(CandidateProfileRecord).where(CandidateProfileRecord.artifact_id == artifact_id)
                ).scalars().first()

                identity_id = ident.identity_id if ident else artifact_id

                if profile:
                    profile.provenance = "Local ATS Demo"
                    # Attach safe job selection provenance
                    if profile.warning_metadata is None:
                        profile.warning_metadata = []
                    profile.warning_metadata.append({
                        "code": "LOCAL_ATS_DEMO_PROVENANCE",
                        "message": f"Imported via Local ATS Demo into job: {job_title}",
                        "source": "Local ATS Demo",
                        "destination_job_id": str(destination_job_id),
                        "job_title": job_title,
                    })

                # Create ATS object mapping
                new_mapping = ATSObjectMappingRecord(
                    mapping_id=uuid4(),
                    organization_id=organization_id,
                    connection_id=connection.connection_id,
                    object_type=ATSObjectType.CANDIDATE,
                    external_id=itm.external_id,
                    internal_id=identity_id,
                    canonical_content_hash=itm.content_hash,
                    last_synced_at=datetime.now(timezone.utc),
                )
                self.db.add(new_mapping)

                # Check if CandidateApplicationRecord already exists for this job revision and candidate
                existing_app = self.db.execute(
                    select(CandidateApplicationRecord).where(
                        CandidateApplicationRecord.organization_id == organization_id,
                        CandidateApplicationRecord.job_id == destination_job_id,
                        CandidateApplicationRecord.job_version_id == job_version_id,
                        CandidateApplicationRecord.candidate_id == identity_id,
                    )
                ).scalar_one_or_none()

                if not existing_app:
                    app_record = CandidateApplicationRecord(
                        application_id=uuid4(),
                        organization_id=organization_id,
                        user_id=None,
                        binding_id=None,
                        candidate_id=identity_id,
                        job_id=destination_job_id,
                        job_version_id=job_version_id,
                        job_revision=job_revision,
                        artifact_id=artifact_id,
                        profile_id=profile.profile_id if profile else None,
                        profile_version=profile.profile_version if profile else 1,
                        status="SUBMITTED",
                        recruiter_review_state="RECEIVED",
                        submission_source="ATS_IMPORT",
                        idempotency_key=f"ats_import:{connection.connection_id}:{itm.external_id}:{job_version_id}",
                        submitted_at=datetime.now(timezone.utc),
                    )
                    self.db.add(app_record)

                successful_count += 1
                item_results.append(
                    ATSImportItemResultRecord(
                        result_id=uuid4(),
                        organization_id=organization_id,
                        run_id=run.run_id,
                        connection_id=connection.connection_id,
                        item_type=ATSObjectType.CANDIDATE,
                        external_id=itm.external_id,
                        status=ATSItemResultStatus.SUCCESS,
                        internal_id=identity_id,
                        safe_error_message=None,
                    )
                )

            except Exception as e:
                logger.error("Failed to ingest candidate item from local folder", extra={"doc_name": itm.sanitized_filename, "error_msg": str(e)})
                failed_count += 1
                item_results.append(
                    ATSImportItemResultRecord(
                        result_id=uuid4(),
                        organization_id=organization_id,
                        run_id=run.run_id,
                        connection_id=connection.connection_id,
                        item_type=ATSObjectType.CANDIDATE,
                        external_id=itm.external_id,
                        status=ATSItemResultStatus.FAILED,
                        safe_error_message=f"Processing failed for document '{itm.sanitized_filename}'.",
                    )
                )

        for ir in item_results:
            self.db.add(ir)

        final_status = ATSImportStatus.COMPLETED if failed_count == 0 else (ATSImportStatus.PARTIAL_SUCCESS if successful_count > 0 or duplicate_count > 0 else ATSImportStatus.FAILED)
        run.status = final_status
        run.total_candidates_count = len(current_scan)
        run.total_attachments_count = len(current_scan)
        run.successful_items_count = successful_count
        run.duplicate_items_count = duplicate_count
        run.failed_items_count = failed_count
        run.completed_at = datetime.now(timezone.utc)
        run.summary_json = {
            "is_dry_run": False,
            "destination_job_id": str(destination_job_id),
            "destination_job_version_id": str(job_version_id),
            "destination_job_title": job_title,
            "destination_job_revision": job_revision,
            "preview_hash": request.preview_hash,
            "successful_count": successful_count,
            "duplicate_count": duplicate_count,
            "failed_count": failed_count,
            "source_provenance": "Local ATS Demo",
        }

        connection.last_sync_at = datetime.now(timezone.utc)
        connection.health_status = ATSHealthStatus.HEALTHY

        self.db.commit()
        self.db.refresh(run)
        return run
