"""Candidate Application Service (Phase 4B.4 & Milestone 1 Resume Integration).

Implements transactional, deny-by-default application drafts, resume upload and extraction,
deterministic processing status, application submission with deterministic matching,
retrieval, listing, and withdrawal for authenticated candidates.
Freezes authoritative job revision and profile version snapshots upon submission.
Enforces single-active-application constraints and idempotency protection.
"""
import hashlib
import logging
import os
import shutil
import urllib.parse
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Tuple
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.models.auth import AuditEventType, AuditReasonCode, AuditResult
from app.models.candidate import CandidateProfileRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateApplicationRecord,
    CandidateApplicationStatus,
    CandidateBindingStatus,
    CandidatePracticeReleaseRecord,
)
from app.schemas.phase3_enums import PracticeReleaseStatus
from app.models.job import (
    JobLifecycleStatus,
    JobPublicationStatus,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
)
from app.models.matching import (
    MatchAuditEventType,
    MatchResultRecord,
    MatchRunLifecycleStatus,
    MatchRunRecord,
)
from app.models.resume import (
    ArtifactFormat,
    ArtifactValidationStatus,
    ProcessingJob,
    ProcessingJobStatus,
    ResumeArtifact,
)
from app.schemas.candidate_application import (
    CandidateApplicationDraftRequest,
    CandidateApplicationListResponse,
    CandidateApplicationResponse,
    CandidateApplicationSubmitRequest,
    CandidateResumeStatusResponse,
)
from app.schemas.candidate_job import CandidateJobSummaryResponse
from app.schemas.matching import (
    MATCH_INPUT_SCHEMA_VERSION,
    MATCHER_ALGORITHM_VERSION,
    verify_no_prohibited_keys,
)
from app.schemas.scoring import (
    CompatibilityScoreResult,
    SCORING_POLICY_VERSION,
)
from app.services.candidate_authorization_kernel import (
    CandidatePrincipal,
    _record_authz_audit,
    load_scoped_candidate_application,
)
from app.services.deterministic_matching import (
    MATCHER_RULES_VERSION,
    match_requisition_requirements,
)
from app.services.docx_validation import DOCXValidationError, validate_docx_file
from app.services.extraction_orchestration import (
    DirectTextUnavailableOrchestrationError,
    ExtractionUnavailableError,
    JobAlreadyRunningError,
    OrchestrationError,
    ResumeNotFoundError,
    UnsupportedDocumentContentError,
    orchestrate_resume_extraction,
)
from app.services.job_lifecycle import SafeProcessingErrorCode
from app.services.match_orchestration import (
    compute_candidate_input_fingerprint,
    compute_canonical_match_fingerprint,
    compute_deterministic_ranking,
    record_match_audit_event,
)
from app.services.matching_preflight import (
    RequisitionNotFoundError,
    RequisitionNotReadyError,
    prepare_match_input_snapshot,
)
from app.services.pdf_validation import PDFValidationError, validate_pdf_file
from app.services.scoring import calculate_compatibility_score

logger = logging.getLogger("ai_hiring.services.candidate_application")

CHUNK_SIZE_BYTES = 1024 * 1024  # 1 MiB chunk size

ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _normalize_filename(raw_filename: Optional[str]) -> str:
    """Normalize and validate filename metadata without exposing paths."""
    if raw_filename is None or not raw_filename.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_FILENAME", "message": "Filename is required and cannot be empty."},
        )

    # Check for raw or URL-encoded control characters / NUL bytes
    decoded_name = urllib.parse.unquote(raw_filename)
    if any(ord(c) < 32 or ord(c) == 127 for c in raw_filename) or any(ord(c) < 32 or ord(c) == 127 for c in decoded_name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_FILENAME", "message": "Filename contains invalid characters."},
        )

    # Extract basename handling both POSIX (/) and Windows (\) separators
    basename = raw_filename.replace("\\", "/").split("/")[-1].strip()

    if not basename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_FILENAME", "message": "Filename cannot be empty."},
        )

    if len(basename) > 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_FILENAME", "message": "Filename exceeds maximum length of 255 characters."},
        )

    return basename


def _validate_extension_and_mime(filename: str, content_type: Optional[str]) -> tuple[str, str]:
    """Preliminary extension and MIME validation returning (ext, declared_mime)."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "UNSUPPORTED_FILE_TYPE",
                "message": "Only PDF (.pdf) and DOCX (.docx) files are supported.",
            },
        )

    # Normalize declared MIME type (strip parameters like charset=utf-8)
    declared_mime = (content_type or "").split(";")[0].strip().lower()
    expected_mime = ALLOWED_EXTENSIONS[ext]

    if declared_mime != expected_mime:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "FILE_TYPE_MISMATCH",
                "message": "Declared MIME type does not match the file extension.",
            },
        )

    return ext, declared_mime


def _ensure_directory(dir_path: Path) -> None:
    """Ensure directory exists with restricted permissions."""
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        os.chmod(dir_path, 0o700)
    except OSError as e:
        logger.error("Failed to prepare storage directory", extra={"error_type": type(e).__name__})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "INTAKE_UNAVAILABLE",
                "message": "Storage is currently unavailable.",
            },
        )


def _cleanup_file(file_path: Path) -> None:
    """Tolerantly remove a file if it exists."""
    try:
        if file_path.exists():
            file_path.unlink()
    except OSError as e:
        logger.warning("Failed to remove file during cleanup", extra={"error_type": type(e).__name__})


def _build_candidate_job_summary(job: JobRequisition, version: JobVersion) -> CandidateJobSummaryResponse:
    published_time = version.approved_at or version.updated_at or version.created_at
    return CandidateJobSummaryResponse(
        job_id=job.job_id,
        job_version_id=version.job_version_id,
        version_number=version.version_number,
        revision=version.revision,
        title=version.title,
        department=version.department,
        location=version.location,
        employment_type=version.employment_type,
        workplace_type=version.workplace_type,
        openings_count=version.openings_count,
        summary=version.summary,
        published_at=published_time,
        updated_at=version.updated_at or version.created_at,
    )


def _build_candidate_application_response(app_rec: CandidateApplicationRecord) -> CandidateApplicationResponse:
    job_summary = _build_candidate_job_summary(app_rec.job, app_rec.job_version)
    can_withdraw = app_rec.status == CandidateApplicationStatus.SUBMITTED.value
    return CandidateApplicationResponse(
        application_id=app_rec.application_id,
        job_id=app_rec.job_id,
        job_version_id=app_rec.job_version_id,
        job_revision=app_rec.job_revision,
        artifact_id=app_rec.artifact_id,
        profile_version=app_rec.profile_version,
        status=CandidateApplicationStatus(app_rec.status),
        submitted_at=app_rec.submitted_at,
        withdrawn_at=app_rec.withdrawn_at,
        can_withdraw=can_withdraw,
        job=job_summary,
    )


def _resolve_released_job_and_version(job_id: UUID, db: Session) -> Tuple[JobRequisition, JobVersion]:
    """Resolves an active and published (RELEASED) job requisition and its released revision."""
    stmt_job = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements)
        )
        .where(
            JobRequisition.job_id == job_id,
            JobRequisition.status == JobLifecycleStatus.ACTIVE,
            JobRequisition.publication_status == JobPublicationStatus.RELEASED,
            JobRequisition.deleted_at.is_(None),
        )
    )
    job = db.execute(stmt_job).scalar_one_or_none()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job posting not found or unavailable."},
        )

    released_ver: Optional[JobVersion] = None
    if job.released_job_version_id:
        released_ver = next(
            (v for v in job.versions if v.job_version_id == job.released_job_version_id),
            None,
        )

    if not released_ver:
        candidate_versions = [
            v for v in job.versions if v.version_status in (JobVersionStatus.APPROVED, JobVersionStatus.READY_FOR_MATCHING)
        ]
        if not candidate_versions:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "JOB_NOT_FOUND", "message": "Job posting not found or unavailable."},
            )
        released_ver = sorted(
            candidate_versions,
            key=lambda v: (v.version_number, v.revision),
            reverse=True,
        )[0]

    return job, released_ver


def create_or_get_candidate_application_draft(
    principal: CandidatePrincipal,
    payload: CandidateApplicationDraftRequest,
    db: Session,
) -> CandidateApplicationResponse:
    """Creates or reopens an application draft for the authenticated candidate.

    Enforces:
    1. Candidate account and binding are ACTIVE.
    2. Job is RELEASED and active.
    3. Exactly one active application per (candidate, job). If already exists in DRAFT,
       RESUME_PROCESSING, READY_TO_SUBMIT, PROCESSING_FAILED, or SUBMITTED, returns existing record.
    """
    now = datetime.now(timezone.utc)

    if principal.binding_status and principal.binding_status != CandidateBindingStatus.ACTIVE.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "CANDIDATE_ACCESS_DENIED", "message": "Candidate account or binding is not active."},
        )

    # Resolve organization_id (default if none)
    org_id = principal.organization_id or uuid.UUID("00000000-0000-0000-0000-000000000001")

    job, released_ver = _resolve_released_job_and_version(payload.job_id, db)

    if payload.expected_revision is not None and payload.expected_revision != released_ver.revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "STALE_JOB_REVISION",
                "message": f"Job requisition revision has changed (current: {released_ver.revision}, expected: {payload.expected_revision}). Please refresh.",
            },
        )

    # Check for any existing active application (non-withdrawn) for this candidate and job
    conditions = [CandidateApplicationRecord.user_id == principal.user_id]
    if principal.binding_id:
        conditions.append(CandidateApplicationRecord.binding_id == principal.binding_id)

    stmt_existing = (
        select(CandidateApplicationRecord)
        .options(
            selectinload(CandidateApplicationRecord.job),
            selectinload(CandidateApplicationRecord.job_version),
            selectinload(CandidateApplicationRecord.profile),
            selectinload(CandidateApplicationRecord.artifact),
        )
        .where(
            or_(*conditions),
            CandidateApplicationRecord.job_id == job.job_id,
            CandidateApplicationRecord.status != CandidateApplicationStatus.WITHDRAWN.value,
        )
    )
    existing_app = db.execute(stmt_existing).scalar_one_or_none()
    if existing_app:
        return _build_candidate_application_response(existing_app)

    # Create new DRAFT application
    draft_record = CandidateApplicationRecord(
        application_id=uuid.uuid4(),
        organization_id=org_id,
        user_id=principal.user_id,
        binding_id=principal.binding_id,
        candidate_id=principal.candidate_id,
        job_id=job.job_id,
        job_version_id=released_ver.job_version_id,
        job_revision=released_ver.revision,
        artifact_id=None,
        profile_id=None,
        profile_version=None,
        status=CandidateApplicationStatus.DRAFT.value,
        submission_source="CANDIDATE_PORTAL",
        idempotency_key=None,
        submitted_at=None,
        withdrawn_at=None,
        created_at=now,
        updated_at=now,
    )
    db.add(draft_record)

    try:
        db.commit()
        db.refresh(draft_record)
    except IntegrityError as err:
        db.rollback()
        logger.warning(
            "Integrity error creating application draft",
            extra={"user_id": str(principal.user_id), "job_id": str(job.job_id), "error": str(err)},
        )
        # Reload existing on race condition
        stmt_reload = (
            select(CandidateApplicationRecord)
            .options(
                selectinload(CandidateApplicationRecord.job),
                selectinload(CandidateApplicationRecord.job_version),
                selectinload(CandidateApplicationRecord.profile),
                selectinload(CandidateApplicationRecord.artifact),
            )
            .where(
                or_(*conditions),
                CandidateApplicationRecord.job_id == job.job_id,
                CandidateApplicationRecord.status != CandidateApplicationStatus.WITHDRAWN.value,
            )
        )
        reloaded = db.execute(stmt_reload).scalar_one_or_none()
        if reloaded:
            return _build_candidate_application_response(reloaded)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "DUPLICATE_APPLICATION", "message": "Application draft already exists."},
        )

    draft_record.job = job
    draft_record.job_version = released_ver

    return _build_candidate_application_response(draft_record)


async def upload_candidate_application_resume(
    principal: CandidatePrincipal,
    application_id: UUID,
    file: UploadFile,
    settings: Settings,
    db: Session,
) -> CandidateResumeStatusResponse:
    """Streams, validates, extracts, and binds resume to an application draft using Milestone 1 pipeline.

    1. Validates candidate owns draft and draft is not SUBMITTED or WITHDRAWN.
    2. Streams file with 10 MiB limit, hashes SHA-256.
    3. Runs PDF/DOCX structure validator.
    4. Moves to quarantine.
    5. Creates ResumeArtifact and ProcessingJob.
    6. Updates application status to RESUME_PROCESSING.
    7. Synchronously runs orchestrate_resume_extraction and profile assembly.
    8. Updates application with artifact_id, profile_id, profile_version, and READY_TO_SUBMIT status.
       If extraction fails safely, marks application PROCESSING_FAILED.
    """
    now = datetime.now(timezone.utc)

    # 1. Scoped application load
    app_rec = load_scoped_candidate_application(
        principal=principal,
        application_id=application_id,
        db=db,
        for_update=True,
        action="UPLOAD_APPLICATION_RESUME",
    )

    if app_rec.status in (CandidateApplicationStatus.SUBMITTED.value, CandidateApplicationStatus.WITHDRAWN.value):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "APPLICATION_NOT_EDITABLE",
                "message": f"Resume cannot be uploaded or replaced on an application in status '{app_rec.status}'.",
            },
        )

    # 2. Preliminary filename and MIME validation
    normalized_filename = _normalize_filename(file.filename)
    ext, declared_mime = _validate_extension_and_mime(normalized_filename, file.content_type)

    # 3. Storage preparation
    intake_dir = settings.get_intake_path()
    _ensure_directory(intake_dir)

    artifact_id = uuid4()
    job_id = uuid4()

    storage_filename = f"{artifact_id}.upload"
    destination_path = (intake_dir / storage_filename).resolve()

    if not str(destination_path).startswith(str(intake_dir)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_FILENAME", "message": "Invalid storage path resolution."},
        )

    quarantine_key = f"resume_intake/{storage_filename}"
    max_bytes = settings.max_resume_upload_bytes
    total_bytes = 0
    sha256 = hashlib.sha256()

    file_created = False
    try:
        try:
            out_file = open(destination_path, "xb")
            file_created = True
        except FileExistsError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "INTAKE_UNAVAILABLE", "message": "Intake storage conflict."},
            )
        except OSError as e:
            logger.error("Failed to open destination file", extra={"error_type": type(e).__name__})
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "INTAKE_UNAVAILABLE", "message": "Intake storage is unavailable."},
            )

        with out_file:
            while True:
                chunk = await file.read(CHUNK_SIZE_BYTES)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail={
                            "code": "FILE_TOO_LARGE",
                            "message": f"File exceeds maximum allowed size of {max_bytes} bytes.",
                        },
                    )
                sha256.update(chunk)
                out_file.write(chunk)

        if total_bytes == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "EMPTY_FILE", "message": "Uploaded file is empty."},
            )
    except Exception:
        await file.close()
        if file_created:
            _cleanup_file(destination_path)
        raise

    await file.close()

    # 4. Format-specific validation & quarantine move
    detected_format: Optional[ArtifactFormat] = None
    validation_status = ArtifactValidationStatus.PENDING
    final_file_path = destination_path

    if ext == ".pdf":
        try:
            val_result = validate_pdf_file(destination_path, settings.max_resume_pages)
            detected_format = val_result.detected_format
            validation_status = ArtifactValidationStatus.VALIDATED

            quarantine_base = settings.get_quarantine_path()
            quarantine_pdf_dir = quarantine_base / "pdf"
            _ensure_directory(quarantine_pdf_dir)

            quarantine_filename = f"{artifact_id}.pdf"
            quarantine_dest = (quarantine_pdf_dir / quarantine_filename).resolve()

            if not str(quarantine_dest).startswith(str(quarantine_pdf_dir)):
                _cleanup_file(destination_path)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "INTAKE_UNAVAILABLE", "message": "Quarantine path containment error."},
                )

            if quarantine_dest.exists():
                _cleanup_file(destination_path)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "INTAKE_UNAVAILABLE", "message": "Quarantine destination conflict."},
                )

            try:
                shutil.move(destination_path, quarantine_dest)
                final_file_path = quarantine_dest
                quarantine_key = f"pdf/{quarantine_filename}"
            except OSError as e:
                _cleanup_file(destination_path)
                _cleanup_file(quarantine_dest)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "INTAKE_UNAVAILABLE", "message": "Failed to move document to quarantine."},
                )
        except PDFValidationError as e:
            _cleanup_file(destination_path)
            raise HTTPException(status_code=e.status_code, detail={"code": e.code, "message": e.message})
        except HTTPException:
            raise
        except Exception as e:
            _cleanup_file(destination_path)
            logger.error("Unexpected PDF validator failure", extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__})
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "PDF_VALIDATION_UNAVAILABLE", "message": "PDF validation is currently unavailable."},
            )

    elif ext == ".docx":
        try:
            val_result = validate_docx_file(
                file_path=destination_path,
                max_docx_expanded_bytes=settings.max_docx_expanded_bytes,
                max_docx_archive_entries=settings.max_docx_archive_entries,
                max_docx_compression_ratio=settings.max_docx_compression_ratio,
            )
            detected_format = val_result.detected_format
            validation_status = ArtifactValidationStatus.VALIDATED

            quarantine_base = settings.get_quarantine_path()
            quarantine_docx_dir = quarantine_base / "docx"
            _ensure_directory(quarantine_docx_dir)

            quarantine_filename = f"{artifact_id}.docx"
            quarantine_dest = (quarantine_docx_dir / quarantine_filename).resolve()

            if not str(quarantine_dest).startswith(str(quarantine_docx_dir)):
                _cleanup_file(destination_path)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "INTAKE_UNAVAILABLE", "message": "Quarantine path containment error."},
                )

            if quarantine_dest.exists():
                _cleanup_file(destination_path)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "INTAKE_UNAVAILABLE", "message": "Quarantine destination conflict."},
                )

            try:
                shutil.move(destination_path, quarantine_dest)
                final_file_path = quarantine_dest
                quarantine_key = f"docx/{quarantine_filename}"
            except OSError as e:
                _cleanup_file(destination_path)
                _cleanup_file(quarantine_dest)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "INTAKE_UNAVAILABLE", "message": "Failed to move document to quarantine."},
                )
        except DOCXValidationError as e:
            _cleanup_file(destination_path)
            raise HTTPException(status_code=e.status_code, detail={"code": e.code, "message": e.message})
        except HTTPException:
            raise
        except Exception as e:
            _cleanup_file(destination_path)
            logger.error("Unexpected DOCX validator failure", extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__})
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "DOCX_VALIDATION_UNAVAILABLE", "message": "DOCX validation is currently unavailable."},
            )

    # 5. Atomic persistence of ResumeArtifact, ProcessingJob, and linking to Application
    sha256_hex = sha256.hexdigest().lower()

    artifact = ResumeArtifact(
        artifact_id=artifact_id,
        original_filename=normalized_filename,
        declared_mime_type=declared_mime,
        detected_format=detected_format,
        size_bytes=total_bytes,
        sha256_hash=sha256_hex,
        validation_status=validation_status,
        quarantine_key=quarantine_key,
    )

    proc_job = ProcessingJob(
        job_id=job_id,
        artifact_id=artifact_id,
        status=ProcessingJobStatus.QUEUED,
        attempt_count=0,
    )

    app_rec.artifact_id = artifact_id
    app_rec.status = CandidateApplicationStatus.RESUME_PROCESSING.value
    app_rec.updated_at = now

    try:
        db.add(artifact)
        db.flush()
        db.add(proc_job)
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        _cleanup_file(final_file_path)
        logger.error("Database commit failed for candidate resume intake", extra={"artifact_id": str(artifact_id)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "INTAKE_UNAVAILABLE", "message": "Failed to persist resume intake record."},
        )

    # 6. Synchronous extraction and profile assembly
    safe_error_code = None
    safe_message = None

    try:
        orchestrate_resume_extraction(
            artifact_id=artifact_id,
            db=db,
            settings=settings,
        )

        # Lookup latest generated profile for this artifact
        stmt_prof = (
            select(CandidateProfileRecord)
            .where(CandidateProfileRecord.artifact_id == artifact_id)
            .order_by(desc(CandidateProfileRecord.profile_version))
        )
        profile_rec = db.execute(stmt_prof).scalars().first()
        if not profile_rec:
            raise ExtractionUnavailableError("Profile was not generated.")

        # Bind application to exact profile version and mark READY_TO_SUBMIT
        app_rec.candidate_id = profile_rec.identity_id
        app_rec.profile_id = profile_rec.profile_id
        app_rec.profile_version = profile_rec.profile_version
        app_rec.status = CandidateApplicationStatus.READY_TO_SUBMIT.value
        app_rec.updated_at = datetime.now(timezone.utc)

        # If user does not have a CandidateAccountBindingRecord yet, create one now linked to this candidate identity
        stmt_chk_b = select(CandidateAccountBindingRecord).where(
            CandidateAccountBindingRecord.user_id == principal.user_id
        )
        existing_b = db.execute(stmt_chk_b).scalar_one_or_none()
        if not existing_b:
            new_b = CandidateAccountBindingRecord(
                binding_id=uuid.uuid4(),
                user_id=principal.user_id,
                candidate_id=profile_rec.identity_id,
                organization_id=app_rec.organization_id,
                invitation_id=None,
                status=CandidateBindingStatus.ACTIVE.value,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(new_b)
            db.flush()
            app_rec.binding_id = new_b.binding_id
        elif not app_rec.binding_id:
            app_rec.binding_id = existing_b.binding_id

        db.commit()

        return CandidateResumeStatusResponse(
            application_id=app_rec.application_id,
            artifact_id=artifact_id,
            status=CandidateApplicationStatus.READY_TO_SUBMIT,
            processing_status=ProcessingJobStatus.SUCCEEDED.value,
            safe_error_code=None,
            safe_message="Resume parsed successfully. Ready to submit application.",
            is_ready_to_submit=True,
        )

    except DirectTextUnavailableOrchestrationError:
        safe_error_code = "NO_READABLE_TEXT"
        safe_message = "No machine-readable text was found. Scanned documents and image-only files are unsupported because OCR is not part of this project."
    except UnsupportedDocumentContentError:
        safe_error_code = "UNSUPPORTED_DOCUMENT_CONTENT"
        safe_message = "The document structure could not be parsed safely. Please upload a standard text PDF or DOCX."
    except OrchestrationError as oe:
        safe_error_code = oe.code
        safe_message = oe.message
    except Exception as e:
        logger.error("Extraction failed for candidate resume", extra={"artifact_id": str(artifact_id), "error": str(e)})
        safe_error_code = "PROCESSING_FAILED"
        safe_message = "Resume processing failed; please retry or upload another file."

    # Record failure state on application
    app_rec.status = CandidateApplicationStatus.PROCESSING_FAILED.value
    app_rec.updated_at = datetime.now(timezone.utc)
    db.commit()

    return CandidateResumeStatusResponse(
        application_id=app_rec.application_id,
        artifact_id=artifact_id,
        status=CandidateApplicationStatus.PROCESSING_FAILED,
        processing_status=ProcessingJobStatus.FAILED.value,
        safe_error_code=safe_error_code,
        safe_message=safe_message,
        is_ready_to_submit=False,
    )


def get_candidate_application_resume_status(
    principal: CandidatePrincipal,
    application_id: UUID,
    db: Session,
) -> CandidateResumeStatusResponse:
    """Returns safe, privacy-controlled processing status of the application's resume."""
    app_rec = load_scoped_candidate_application(
        principal=principal,
        application_id=application_id,
        db=db,
        action="GET_APPLICATION_RESUME_STATUS",
    )

    if not app_rec.artifact_id:
        return CandidateResumeStatusResponse(
            application_id=app_rec.application_id,
            artifact_id=None,
            status=CandidateApplicationStatus(app_rec.status),
            processing_status=None,
            safe_error_code=None,
            safe_message="No resume has been uploaded yet.",
            is_ready_to_submit=False,
        )

    stmt_job = select(ProcessingJob).where(ProcessingJob.artifact_id == app_rec.artifact_id)
    job = db.execute(stmt_job).scalar_one_or_none()

    proc_status = job.status.value if job else None
    safe_code = job.safe_error_code.value if job and job.safe_error_code else None

    safe_msg = None
    if app_rec.status == CandidateApplicationStatus.READY_TO_SUBMIT.value:
        safe_msg = "Resume processed successfully. Ready to submit application."
    elif app_rec.status == CandidateApplicationStatus.PROCESSING_FAILED.value:
        if safe_code == SafeProcessingErrorCode.DIRECT_TEXT_UNAVAILABLE.value:
            safe_msg = "No machine-readable text was found. Scanned documents and image-only files are unsupported."
        else:
            safe_msg = "Resume processing failed; please retry or upload another file."
    elif app_rec.status == CandidateApplicationStatus.RESUME_PROCESSING.value:
        safe_msg = "Extracting information from resume..."
    elif app_rec.status == CandidateApplicationStatus.SUBMITTED.value:
        safe_msg = "Application submitted."

    is_ready = app_rec.status == CandidateApplicationStatus.READY_TO_SUBMIT.value

    return CandidateResumeStatusResponse(
        application_id=app_rec.application_id,
        artifact_id=app_rec.artifact_id,
        status=CandidateApplicationStatus(app_rec.status),
        processing_status=proc_status,
        safe_error_code=safe_code,
        safe_message=safe_msg,
        is_ready_to_submit=is_ready,
    )


def _execute_application_deterministic_matching(
    app_rec: CandidateApplicationRecord,
    db: Session,
) -> None:
    """Executes deterministic matching pipeline atomically for a newly submitted application."""
    try:
        # 1. Prepare frozen pseudonymous snapshot (using system / service authorization)
        snapshot = prepare_match_input_snapshot(
            actor=None,
            job_id=app_rec.job_id,
            expected_revision=app_rec.job_revision,
            profile_version=app_rec.profile_version,
            profile_id=app_rec.profile_id,
            db=db,
        )

        now = datetime.now(timezone.utc)

        # 2. Run deterministic requirements matcher
        match_result_set = match_requisition_requirements(
            snapshot=snapshot,
            db=db,
            evaluation_date=now,
        )

        # 3. Calculate compatibility score and component diagnostics
        score_result = calculate_compatibility_score(
            result_set=match_result_set,
            snapshot=snapshot,
        )

        # 4. Dense ranking computation
        candidate_scores = [(app_rec.profile_id, app_rec.profile_version, score_result)]
        ranked_candidates = compute_deterministic_ranking(candidate_scores)

        # 5. MatchRun & MatchResult persistence
        run_id = uuid.uuid4()
        idempotency_key = f"app-submit-match-{app_rec.application_id}"

        # Resolve job version requirements for fingerprint
        target_version = app_rec.job_version
        requirements_data: List[dict] = []
        for r in (target_version.requirements or []):
            requirements_data.append({
                "requirement_id": str(r.requirement_id),
                "display_order": r.display_order,
                "category": r.category.value if hasattr(r.category, "value") else str(r.category),
                "necessity": r.necessity.value if hasattr(r.necessity, "value") else str(r.necessity),
                "priority": r.priority.value if hasattr(r.priority, "value") else str(r.priority),
                "approved_weight": float(r.custom_weight_override) if r.custom_weight_override is not None else None,
                "min_experience_months": r.min_experience_months,
                "recency_years": r.recency_years,
                "accepted_evidence_types": sorted([et.value if hasattr(et, "value") else str(et) for et in (r.accepted_evidence_types or [])]),
                "accepted_equivalents": sorted(list(r.accepted_equivalents or [])),
                "taxonomy_skill_concept_id": str(r.taxonomy_skill_concept_id) if r.taxonomy_skill_concept_id else None,
            })

        cand_keys = [(app_rec.profile_id, app_rec.profile_version)]
        cand_fingerprint = compute_candidate_input_fingerprint(cand_keys)
        canonical_fingerprint = compute_canonical_match_fingerprint(
            requisition_id=app_rec.job_id,
            job_version_id=target_version.job_version_id,
            revision=target_version.revision,
            version_number=target_version.version_number,
            taxonomy_version=target_version.taxonomy_version,
            occupation_concept_id=target_version.occupation_concept_id,
            requirements_data=requirements_data,
            candidates=cand_keys,
            evaluation_date=now,
        )

        run_record = MatchRunRecord(
            run_id=run_id,
            recruiter_id=app_rec.job.created_by_user_id,
            job_id=app_rec.job_id,
            job_version_id=target_version.job_version_id,
            revision=target_version.revision,
            evaluation_date=now,
            match_input_schema_version=MATCH_INPUT_SCHEMA_VERSION,
            matcher_algorithm_version=MATCHER_ALGORITHM_VERSION,
            matching_rule_version=MATCHER_RULES_VERSION,
            scoring_policy_version=SCORING_POLICY_VERSION,
            candidate_input_fingerprint=cand_fingerprint,
            input_fingerprint=canonical_fingerprint,
            idempotency_key=idempotency_key,
            lifecycle_status=MatchRunLifecycleStatus.SUCCEEDED,
            error_code=None,
            error_message=None,
            candidate_count=1,
            created_at=now,
            started_at=now,
            completed_at=now,
        )
        db.add(run_record)

        for rc in ranked_candidates:
            decomp = rc.score_result.model_dump(mode="json")
            verify_no_prohibited_keys(decomp)

            res_record = MatchResultRecord(
                result_id=uuid.uuid4(),
                run_id=run_id,
                profile_id=rc.profile_id,
                profile_version=rc.profile_version,
                raw_compatibility_score=rc.raw_compatibility_score,
                display_compatibility_score=rc.display_compatibility_score,
                confidence_tier=rc.score_result.confidence_diagnostics.confidence_tier.value,
                warning_count=len(rc.score_result.must_have_warnings) + len(rc.score_result.general_warnings),
                must_have_warning_count=len(rc.score_result.must_have_warnings),
                rank=rc.rank,
                is_tied=rc.is_tied,
                tie_group_id=rc.tie_group_id,
                score_decomposition=decomp,
                created_at=now,
            )
            db.add(res_record)

        record_match_audit_event(
            db=db,
            event_type=MatchAuditEventType.MATCH_RUN_CREATED,
            actor_user_id=None,
            job_id=app_rec.job_id,
            run_id=run_id,
            job_version_id=target_version.job_version_id,
            revision=target_version.revision,
            candidate_count=1,
            matcher_algorithm_version=MATCHER_ALGORITHM_VERSION,
            scoring_policy_version=SCORING_POLICY_VERSION,
            input_fingerprint=canonical_fingerprint,
            safe_metadata={"source": "CANDIDATE_SUBMISSION"},
        )
        record_match_audit_event(
            db=db,
            event_type=MatchAuditEventType.MATCH_RUN_SUCCEEDED,
            actor_user_id=None,
            job_id=app_rec.job_id,
            run_id=run_id,
            job_version_id=target_version.job_version_id,
            revision=target_version.revision,
            candidate_count=1,
            matcher_algorithm_version=MATCHER_ALGORITHM_VERSION,
            scoring_policy_version=SCORING_POLICY_VERSION,
            input_fingerprint=canonical_fingerprint,
            safe_metadata={"source": "CANDIDATE_SUBMISSION"},
        )

        db.commit()
    except Exception as e:
        logger.error(
            "Deterministic matching execution after submission failed safely",
            extra={"application_id": str(app_rec.application_id), "error": str(e)},
        )
        # Safe failure: preserve submitted application without losing draft or profile version


def submit_candidate_application_draft(
    principal: CandidatePrincipal,
    application_id: UUID,
    db: Session,
    payload: Optional[CandidateApplicationSubmitRequest] = None,
) -> CandidateApplicationResponse:
    """Finalizes and submits an application draft.

    Enforces:
    1. Candidate owns draft.
    2. Job is still RELEASED.
    3. Application is in READY_TO_SUBMIT state with processed resume and profile version.
    4. Idempotency protection.
    5. Freezes job revision and profile version at submission time.
    6. Triggers deterministic matching.
    """
    if payload is None:
        payload = CandidateApplicationSubmitRequest()

    now = datetime.now(timezone.utc)

    # 1. Candidate binding check
    if principal.binding_status != CandidateBindingStatus.ACTIVE.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "CANDIDATE_ACCESS_DENIED", "message": "Candidate account or binding is not active."},
        )

    # 2. Scoped load of application
    app_rec = load_scoped_candidate_application(
        principal=principal,
        application_id=application_id,
        db=db,
        for_update=True,
        action="SUBMIT_APPLICATION_DRAFT",
    )

    # If already submitted, check idempotency or reject duplicate active application
    if app_rec.status == CandidateApplicationStatus.SUBMITTED.value:
        if payload.idempotency_key and app_rec.idempotency_key == payload.idempotency_key:
            return _build_candidate_application_response(app_rec)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "DUPLICATE_APPLICATION",
                "message": "An active application for this job has already been submitted.",
            },
        )

    if app_rec.status == CandidateApplicationStatus.WITHDRAWN.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "APPLICATION_WITHDRAWN", "message": "Withdrawn applications cannot be submitted."},
        )

    if app_rec.status != CandidateApplicationStatus.READY_TO_SUBMIT.value or not app_rec.artifact_id or not app_rec.profile_version or not app_rec.profile_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "RESUME_REQUIRED",
                "message": "A processed resume is required before submitting your application.",
            },
        )

    # 3. Verify job is still RELEASED
    job, released_ver = _resolve_released_job_and_version(app_rec.job_id, db)

    # Stale expected revision check
    if payload.expected_revision is not None and payload.expected_revision != released_ver.revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "STALE_JOB_REVISION",
                "message": f"Job requisition revision has changed (current: {released_ver.revision}, expected: {payload.expected_revision}). Please refresh.",
            },
        )

    # 4. Check idempotency key if provided
    if payload.idempotency_key:
        stmt_idem = select(CandidateApplicationRecord).where(
            CandidateApplicationRecord.binding_id == principal.binding_id,
            CandidateApplicationRecord.idempotency_key == payload.idempotency_key,
        )
        existing_idem = db.execute(stmt_idem).scalar_one_or_none()
        if existing_idem and existing_idem.application_id != app_rec.application_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "IDEMPOTENCY_KEY_CONFLICT",
                    "message": "This idempotency key was previously used for a different application.",
                },
            )

    # 5. Atomically transition application to SUBMITTED
    app_rec.job_version_id = released_ver.job_version_id
    app_rec.job_revision = released_ver.revision
    app_rec.status = CandidateApplicationStatus.SUBMITTED.value
    app_rec.idempotency_key = payload.idempotency_key
    app_rec.submitted_at = now
    app_rec.updated_at = now

    _record_authz_audit(
        db=db,
        principal=principal,
        event_type=AuditEventType.CANDIDATE_APPLICATION_SUBMITTED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.APPLICATION_SUBMITTED,
        resource_type="candidate_application",
        resource_id=str(app_rec.application_id),
        safe_metadata={
            "action": "SUBMIT_APPLICATION",
            "job_id": str(job.job_id),
            "job_version_id": str(released_ver.job_version_id),
            "job_revision": released_ver.revision,
            "artifact_id": str(app_rec.artifact_id),
            "profile_version": app_rec.profile_version,
        },
    )

    db.commit()
    db.refresh(app_rec)

    # 6. Trigger deterministic matching
    _execute_application_deterministic_matching(app_rec, db)

    return _build_candidate_application_response(app_rec)


def submit_candidate_application(
    principal: CandidatePrincipal,
    job_id: uuid.UUID,
    payload: CandidateApplicationSubmitRequest,
    db: Session,
) -> CandidateApplicationResponse:
    """Legacy direct endpoint: reuses or creates draft and returns it for resume requirement flow."""
    draft_req = CandidateApplicationDraftRequest(
        job_id=job_id,
        expected_revision=payload.expected_revision,
    )
    draft_res = create_or_get_candidate_application_draft(
        principal=principal,
        payload=draft_req,
        db=db,
    )

    if draft_res.status == CandidateApplicationStatus.SUBMITTED:
        if payload.idempotency_key and draft_res.status == CandidateApplicationStatus.SUBMITTED:
            # reload application record to check idempotency key
            stmt_app_chk = select(CandidateApplicationRecord).where(
                CandidateApplicationRecord.application_id == draft_res.application_id
            )
            existing_sub = db.execute(stmt_app_chk).scalar_one_or_none()
            if existing_sub and existing_sub.idempotency_key == payload.idempotency_key:
                return draft_res
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "DUPLICATE_APPLICATION",
                "message": "An active application for this job has already been submitted.",
            },
        )

    if draft_res.status == CandidateApplicationStatus.WITHDRAWN:
        return draft_res

    # If draft is not ready yet, check if candidate already has an existing validated profile
    # (e.g. from existing profile/session setup or prior resume upload)
    stmt_app = select(CandidateApplicationRecord).where(
        CandidateApplicationRecord.application_id == draft_res.application_id
    )
    app_rec = db.execute(stmt_app).scalar_one_or_none()
    if app_rec and not app_rec.artifact_id and principal.candidate_id:
        # Check for latest candidate profile record linked to candidate identity
        stmt_prof = (
            select(CandidateProfileRecord)
            .where(CandidateProfileRecord.identity_id == principal.candidate_id)
            .order_by(desc(CandidateProfileRecord.profile_version))
        )
        existing_profile = db.execute(stmt_prof).scalars().first()
        if existing_profile:
            app_rec.candidate_id = existing_profile.identity_id
            app_rec.artifact_id = existing_profile.artifact_id
            app_rec.profile_id = existing_profile.profile_id
            app_rec.profile_version = existing_profile.profile_version
            app_rec.status = CandidateApplicationStatus.READY_TO_SUBMIT.value
            db.commit()
            db.refresh(app_rec)
            draft_res = _build_candidate_application_response(app_rec)

    if draft_res.status == CandidateApplicationStatus.READY_TO_SUBMIT:
        return submit_candidate_application_draft(
            principal=principal,
            application_id=draft_res.application_id,
            payload=payload,
            db=db,
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "RESUME_REQUIRED",
            "message": "Every application requires a resume. Please upload your resume first.",
        },
    )


def list_candidate_applications(
    principal: CandidatePrincipal,
    db: Session,
    page: int = 1,
    limit: int = 20,
) -> CandidateApplicationListResponse:
    """Lists all applications for the authenticated candidate principal."""
    page = max(1, page)
    limit = max(1, min(limit, 50))
    offset = (page - 1) * limit

    conditions = [CandidateApplicationRecord.user_id == principal.user_id]
    if principal.binding_id:
        conditions.append(CandidateApplicationRecord.binding_id == principal.binding_id)

    stmt = (
        select(CandidateApplicationRecord)
        .options(
            selectinload(CandidateApplicationRecord.job),
            selectinload(CandidateApplicationRecord.job_version),
            selectinload(CandidateApplicationRecord.profile),
            selectinload(CandidateApplicationRecord.artifact),
        )
        .where(
            or_(*conditions),
        )
        .order_by(desc(CandidateApplicationRecord.created_at))
    )

    all_records = db.execute(stmt).scalars().all()
    total_count = len(all_records)
    paginated_records = all_records[offset : offset + limit]

    items = [_build_candidate_application_response(r) for r in paginated_records]

    return CandidateApplicationListResponse(
        items=items,
        total=total_count,
        page=page,
        limit=limit,
    )


def get_candidate_application_detail(
    principal: CandidatePrincipal,
    application_id: uuid.UUID,
    db: Session,
) -> CandidateApplicationResponse:
    """Retrieves full safe details for a single candidate application owned by the candidate principal."""
    app_rec = load_scoped_candidate_application(
        principal=principal,
        application_id=application_id,
        db=db,
        action="GET_APPLICATION_DETAIL",
    )
    return _build_candidate_application_response(app_rec)


def withdraw_candidate_application(
    principal: CandidatePrincipal,
    application_id: uuid.UUID,
    db: Session,
) -> CandidateApplicationResponse:
    """Withdraws an eligible application owned by the authenticated candidate."""
    now = datetime.now(timezone.utc)
    app_rec = load_scoped_candidate_application(
        principal=principal,
        application_id=application_id,
        db=db,
        for_update=True,
        action="WITHDRAW_APPLICATION",
    )

    if app_rec.status == CandidateApplicationStatus.WITHDRAWN.value:
        return _build_candidate_application_response(app_rec)

    if app_rec.status not in (CandidateApplicationStatus.SUBMITTED.value, CandidateApplicationStatus.DRAFT.value, CandidateApplicationStatus.READY_TO_SUBMIT.value, CandidateApplicationStatus.PROCESSING_FAILED.value):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "APPLICATION_NOT_WITHDRAWABLE",
                "message": f"Application cannot be withdrawn from status '{app_rec.status}'.",
            },
        )

    app_rec.status = CandidateApplicationStatus.WITHDRAWN.value
    app_rec.withdrawn_at = now
    app_rec.updated_at = now

    # Cascade revocation to any active interview releases associated with this application or candidate/job
    stmt_releases = select(CandidatePracticeReleaseRecord).where(
        or_(
            CandidatePracticeReleaseRecord.application_id == app_rec.application_id,
            and_(
                CandidatePracticeReleaseRecord.candidate_id == app_rec.candidate_id,
                CandidatePracticeReleaseRecord.job_id == app_rec.job_id,
            ),
        ),
        CandidatePracticeReleaseRecord.status == PracticeReleaseStatus.RELEASED.value,
    )
    releases_to_revoke = db.execute(stmt_releases).scalars().all()
    for rel in releases_to_revoke:
        rel.status = PracticeReleaseStatus.REVOKED.value
        rel.revoked_at = now

    _record_authz_audit(
        db=db,
        principal=principal,
        event_type=AuditEventType.CANDIDATE_APPLICATION_WITHDRAWN,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.APPLICATION_WITHDRAWN,
        resource_type="candidate_application",
        resource_id=str(app_rec.application_id),
        safe_metadata={
            "action": "WITHDRAW_APPLICATION",
            "job_id": str(app_rec.job_id),
            "withdrawn_at": now.isoformat(),
        },
    )

    db.commit()
    db.refresh(app_rec)

    return _build_candidate_application_response(app_rec)
