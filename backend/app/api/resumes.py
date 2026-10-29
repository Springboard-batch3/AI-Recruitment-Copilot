import hashlib
import logging
import os
import shutil
import urllib.parse
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.api.auth import AuthenticatedContext, require_recruiter
from app.config import Settings, get_settings
from app.database import get_db
from app.models.extraction import ResumeExtraction
from app.models.resume import (
    ArtifactFormat,
    ArtifactValidationStatus,
    ProcessingJob,
    ProcessingJobStatus,
    ResumeArtifact,
)
from app.schemas.correction import (
    ProfileCorrectionRequest,
    ProfileCorrectionResponse,
    ProfileSummaryResponse,
)
from app.schemas.profile_assembly import CandidateProfileResponse
from app.schemas.resume import (
    CandidateListItemResponse,
    ResumeExtractionBlockResponse,
    ResumeExtractionResponse,
    ResumeProcessResponse,
    ResumeStatusResponse,
    ResumeUploadResponse,
)
from app.services.docx_validation import DOCXValidationError, validate_docx_file
from app.services.extraction_orchestration import (
    OrchestrationError,
    orchestrate_resume_extraction,
)
from app.services.pdf_validation import PDFValidationError, validate_pdf_file

logger = logging.getLogger("ai_hiring.api.resumes")

router = APIRouter(
    prefix="/api/v1/resumes",
    tags=["resumes"],
    dependencies=[Depends(require_recruiter)],
)

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


@router.post(
    "",
    response_model=ResumeUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload Resume Document",
    description=(
        "Accepts a PDF or DOCX file, performs preliminary intake checks, streams the file into "
        "intake storage, validates document structure and package safety, moves accepted files to quarantine, "
        "creates ResumeArtifact and ProcessingJob persistence records atomically, and queues the document."
    ),
)
@router.post(
    "/upload",
    response_model=ResumeUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
async def upload_resume(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> ResumeUploadResponse:
    # 1. Preliminary filename and MIME validation
    normalized_filename = _normalize_filename(file.filename)
    ext, declared_mime = _validate_extension_and_mime(normalized_filename, file.content_type)

    # 2. Storage preparation
    intake_dir = settings.get_intake_path()
    _ensure_directory(intake_dir)

    artifact_id = uuid4()
    job_id = uuid4()

    storage_filename = f"{artifact_id}.upload"
    destination_path = (intake_dir / storage_filename).resolve()

    # Verify containment within configured intake directory
    if not str(destination_path).startswith(str(intake_dir)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_FILENAME", "message": "Invalid storage path resolution."},
        )

    quarantine_key = f"resume_intake/{storage_filename}"
    max_bytes = settings.max_resume_upload_bytes
    total_bytes = 0
    sha256 = hashlib.sha256()

    # 3. Stream upload in chunks with bounded reads and hashing
    file_created = False
    try:
        try:
            # Open in exclusive creation mode 'xb'
            out_file = open(destination_path, "xb")
            file_created = True
        except FileExistsError:
            logger.error("Generated artifact storage file already exists", extra={"artifact_id": str(artifact_id)})
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

            # Quarantine directory setup
            quarantine_base = settings.get_quarantine_path()
            quarantine_pdf_dir = quarantine_base / "pdf"
            _ensure_directory(quarantine_pdf_dir)

            quarantine_filename = f"{artifact_id}.pdf"
            quarantine_dest = (quarantine_pdf_dir / quarantine_filename).resolve()

            # Verify containment
            if not str(quarantine_dest).startswith(str(quarantine_pdf_dir)):
                _cleanup_file(destination_path)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "INTAKE_UNAVAILABLE", "message": "Quarantine path containment error."},
                )

            # Move intake file to quarantine (prevent overwriting)
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
                logger.error("Failed to move PDF to quarantine", extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__})
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "INTAKE_UNAVAILABLE", "message": "Failed to move document to quarantine."},
                )

        except PDFValidationError as e:
            _cleanup_file(destination_path)
            raise HTTPException(
                status_code=e.status_code,
                detail={"code": e.code, "message": e.message},
            )
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

            # Quarantine directory setup
            quarantine_base = settings.get_quarantine_path()
            quarantine_docx_dir = quarantine_base / "docx"
            _ensure_directory(quarantine_docx_dir)

            quarantine_filename = f"{artifact_id}.docx"
            quarantine_dest = (quarantine_docx_dir / quarantine_filename).resolve()

            # Verify containment
            if not str(quarantine_dest).startswith(str(quarantine_docx_dir)):
                _cleanup_file(destination_path)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "INTAKE_UNAVAILABLE", "message": "Quarantine path containment error."},
                )

            # Move intake file to quarantine (prevent overwriting)
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
                logger.error("Failed to move DOCX to quarantine", extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__})
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "INTAKE_UNAVAILABLE", "message": "Failed to move document to quarantine."},
                )

        except DOCXValidationError as e:
            _cleanup_file(destination_path)
            raise HTTPException(
                status_code=e.status_code,
                detail={"code": e.code, "message": e.message},
            )
        except HTTPException:
            raise
        except Exception as e:
            _cleanup_file(destination_path)
            logger.error("Unexpected DOCX validator failure", extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__})
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "DOCX_VALIDATION_UNAVAILABLE", "message": "DOCX validation is currently unavailable."},
            )

    # 5. Atomic persistence of ResumeArtifact and ProcessingJob
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

    job = ProcessingJob(
        job_id=job_id,
        artifact_id=artifact_id,
        status=ProcessingJobStatus.QUEUED,
        attempt_count=0,
    )

    try:
        db.add(artifact)
        db.flush()
        db.add(job)
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        _cleanup_file(final_file_path)
        logger.error("Database commit failed for resume intake", extra={"artifact_id": str(artifact_id)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "INTAKE_UNAVAILABLE",
                "message": "Failed to persist resume intake record.",
            },
        )
    except Exception:
        db.rollback()
        _cleanup_file(final_file_path)
        raise

    # 6. Success response
    return ResumeUploadResponse(
        artifact_id=artifact_id,
        job_id=job_id,
        status=ProcessingJobStatus.QUEUED,
    )


@router.get(
    "/{artifact_id}/status",
    response_model=ResumeStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Resume Processing Status",
    description=(
        "Returns the privacy-safe processing status of a resume artifact and its associated job. "
        "Excludes filenames, hashes, paths, and document content."
    ),
)
async def get_resume_status(
    artifact_id: UUID,
    db: Session = Depends(get_db),
) -> ResumeStatusResponse:
    # NOTE: This status endpoint is local-development-only for Phase 1B.
    # Object-level authorization (tenant/user ownership checks) must be added when authentication is introduced.
    try:
        stmt_artifact = select(ResumeArtifact).where(ResumeArtifact.artifact_id == artifact_id)
        artifact = db.execute(stmt_artifact).scalar_one_or_none()

        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "RESUME_NOT_FOUND", "message": "Resume artifact or processing job not found."},
            )

        stmt_job = select(ProcessingJob).where(ProcessingJob.artifact_id == artifact_id)
        job = db.execute(stmt_job).scalar_one_or_none()

        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "RESUME_NOT_FOUND", "message": "Resume artifact or processing job not found."},
            )

        return ResumeStatusResponse(
            artifact_id=artifact.artifact_id,
            job_id=job.job_id,
            detected_format=artifact.detected_format,
            validation_status=artifact.validation_status,
            processing_status=job.status,
            attempt_count=job.attempt_count,
            safe_error_code=job.safe_error_code,
        )

    except HTTPException:
        raise
    except SQLAlchemyError as e:
        logger.error(
            "Database error querying resume status",
            extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "INTAKE_UNAVAILABLE", "message": "Database service is currently unavailable."},
        )
    except Exception as e:
        logger.error(
            "Unexpected error querying resume status",
            extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "INTAKE_UNAVAILABLE", "message": "Service is currently unavailable."},
        )


@router.post(
    "/{artifact_id}/process",
    response_model=ResumeProcessResponse,
    status_code=status.HTTP_200_OK,
    summary="Process Resume Artifact",
    description=(
        "Synchronously runs text extraction, normalization, and persistence for a validated resume artifact. "
        "Claims QUEUED job, runs format-specific direct text extraction, normalizes results, persists extraction blocks, "
        "and marks job SUCCEEDED. Idempotently returns existing extraction for SUCCEEDED jobs."
    ),
)
async def process_resume(
    artifact_id: UUID,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> ResumeProcessResponse:
    # NOTE: Processing endpoint is local-development-only until authentication
    # and object-level authorization (tenant/user ownership) are implemented.
    try:
        outcome = orchestrate_resume_extraction(
            artifact_id=artifact_id,
            db=db,
            settings=settings,
        )

        return ResumeProcessResponse(
            artifact_id=outcome.artifact_id,
            job_id=outcome.job_id,
            extraction_id=outcome.extraction_id,
            processing_status=outcome.processing_status,
            review_required=outcome.review_required,
            block_count=outcome.block_count,
        )

    except OrchestrationError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "message": e.message},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Unexpected failure in resume processing endpoint",
            extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "EXTRACTION_UNAVAILABLE", "message": "The extraction service is currently unavailable."},
        )


@router.get(
    "/{artifact_id}/extraction",
    response_model=ResumeExtractionResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Resume Extraction Results",
    description=(
        "Returns the complete evidence-addressable extraction blocks and quality metadata for a processed resume artifact. "
        "Excludes filenames, hashes, paths, and document internals."
    ),
)
async def get_resume_extraction(
    artifact_id: UUID,
    db: Session = Depends(get_db),
) -> ResumeExtractionResponse:
    # NOTE: Extraction retrieval endpoint is local-development-only until authentication
    # and object-level authorization (tenant/user ownership) are implemented.
    try:
        stmt = (
            select(ResumeExtraction)
            .options(selectinload(ResumeExtraction.blocks))
            .where(ResumeExtraction.artifact_id == artifact_id)
        )
        extraction = db.execute(stmt).scalar_one_or_none()

        if extraction is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "EXTRACTION_NOT_FOUND", "message": "No extraction found for this resume artifact."},
            )

        block_responses = [
            ResumeExtractionBlockResponse(
                block_id=b.block_id,
                sequence_index=b.sequence_index,
                page_number=b.page_number,
                structural_location=b.structural_location,
                block_type=b.block_type.value,
                extraction_method=b.extraction_method.value,
                raw_text=b.raw_text,
                normalized_text=b.normalized_text,
                confidence=float(b.confidence) if b.confidence is not None else None,
                review_required=b.review_required,
            )
            for b in extraction.blocks
        ]

        return ResumeExtractionResponse(
            artifact_id=extraction.artifact_id,
            extraction_id=extraction.extraction_id,
            extractor_version=extraction.extractor_version,
            review_required=extraction.review_required,
            block_count=len(block_responses),
            blocks=block_responses,
        )

    except HTTPException:
        raise
    except SQLAlchemyError as e:
        logger.error(
            "Database error querying resume extraction",
            exc_info=True,
            extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "INTAKE_UNAVAILABLE", "message": "Database service is currently unavailable."},
        )
    except Exception as e:
        logger.error(
            "Unexpected error querying resume extraction",
            exc_info=True,
            extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "INTAKE_UNAVAILABLE", "message": "Service is currently unavailable."},
        )


@router.get(
    "/{artifact_id}/profile",
    response_model=CandidateProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Candidate Profile",
    description=(
        "Returns the structured CandidateProfile v1, evidence spans, review requirements, "
        "and separated identity details for a processed resume artifact. Optionally accepts a specific version."
    ),
)
async def get_candidate_profile(
    artifact_id: UUID,
    version: Optional[int] = None,
    db: Session = Depends(get_db),
) -> CandidateProfileResponse:
    try:
        # Check artifact exists
        stmt_art = select(ResumeArtifact).where(ResumeArtifact.artifact_id == artifact_id)
        artifact = db.execute(stmt_art).scalar_one_or_none()
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "RESUME_NOT_FOUND", "message": "Resume artifact not found."},
            )

        from app.models.candidate import CandidateProfileRecord

        if version is not None:
            stmt_prof = (
                select(CandidateProfileRecord)
                .options(selectinload(CandidateProfileRecord.identity))
                .where(
                    CandidateProfileRecord.artifact_id == artifact_id,
                    CandidateProfileRecord.profile_version == version,
                )
            )
            profile_rec = db.execute(stmt_prof).scalars().first()
            if profile_rec is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "PROFILE_VERSION_NOT_FOUND", "message": f"Profile version {version} not found."},
                )
        else:
            # Query latest profile by profile_version
            stmt_prof = (
                select(CandidateProfileRecord)
                .options(selectinload(CandidateProfileRecord.identity))
                .where(CandidateProfileRecord.artifact_id == artifact_id)
                .order_by(CandidateProfileRecord.profile_version.desc())
            )
            profile_rec = db.execute(stmt_prof).scalars().first()

        if profile_rec is None:
            # Check if extraction exists to assemble profile idempotently on-demand
            stmt_ext = (
                select(ResumeExtraction)
                .options(selectinload(ResumeExtraction.blocks))
                .where(ResumeExtraction.artifact_id == artifact_id)
            )
            extraction = db.execute(stmt_ext).scalar_one_or_none()

            if extraction is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "PROFILE_NOT_READY",
                        "message": "Candidate profile is not ready. The document has not been extracted yet.",
                    },
                )

            # Assemble and persist profile on-demand
            from app.schemas.extraction import ExtractionBlockData
            from app.services.profile_assembly import (
                assemble_candidate_profile,
                persist_candidate_profile,
            )

            blocks_data = [
                ExtractionBlockData(
                    block_id=b.block_id,
                    sequence_index=b.sequence_index,
                    page_number=b.page_number,
                    structural_location=b.structural_location,
                    block_type=b.block_type,
                    raw_text=b.raw_text,
                    normalized_text=b.normalized_text,
                    extraction_method=b.extraction_method,
                    confidence=b.confidence,
                    review_required=b.review_required,
                )
                for b in extraction.blocks
            ]

            assembled = assemble_candidate_profile(
                blocks=blocks_data,
                artifact_id=str(artifact_id),
                extraction_review_required=extraction.review_required,
            )

            profile_rec = persist_candidate_profile(
                session=db,
                artifact_id=artifact_id,
                extraction_id=extraction.extraction_id,
                assembled_profile=assembled,
            )
            db.commit()

            # Reload with identity
            profile_rec = db.execute(stmt_prof).scalars().first()

        identity_payload = profile_rec.identity.identity_payload
        from app.schemas.candidate import (
            CandidateIdentity,
            CandidateProfessionalProfile,
            EvidenceSpan,
        )
        from app.schemas.profile_assembly import ProfileAssemblyWarning

        return CandidateProfileResponse(
            candidate_id=str(profile_rec.identity_id),
            artifact_id=str(profile_rec.artifact_id),
            profile_id=str(profile_rec.profile_id),
            profile_version=profile_rec.profile_version,
            schema_version=profile_rec.schema_version,
            parser_pipeline_version=profile_rec.parser_pipeline_version,
            provenance=profile_rec.provenance,
            parent_profile_id=str(profile_rec.parent_profile_id) if profile_rec.parent_profile_id else None,
            parent_profile_version=profile_rec.parent_profile_version,
            created_at=profile_rec.created_at.isoformat() if profile_rec.created_at else None,
            review_required=profile_rec.review_required,
            warnings=[ProfileAssemblyWarning.model_validate(w) for w in profile_rec.warning_metadata],
            identity=CandidateIdentity.model_validate(identity_payload),
            professional_profile=CandidateProfessionalProfile.model_validate(profile_rec.professional_profile),
            evidence=[EvidenceSpan.model_validate(ev) for ev in profile_rec.evidence_spans],
        )

    except HTTPException:
        raise
    except SQLAlchemyError as e:
        logger.error(
            "Database error querying candidate profile",
            exc_info=True,
            extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "INTAKE_UNAVAILABLE", "message": "Database service is currently unavailable."},
        )
    except Exception as e:
        logger.error(
            "Unexpected error querying candidate profile",
            exc_info=True,
            extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "INTAKE_UNAVAILABLE", "message": "Service is currently unavailable."},
        )


@router.get(
    "/{artifact_id}/profiles",
    response_model=list[ProfileSummaryResponse],
    status_code=status.HTTP_200_OK,
    summary="Get Profile History",
    description="Returns version history metadata for all profile versions generated or corrected for an artifact.",
)
async def get_candidate_profiles_history(
    artifact_id: UUID,
    db: Session = Depends(get_db),
) -> list[ProfileSummaryResponse]:
    try:
        stmt_art = select(ResumeArtifact).where(ResumeArtifact.artifact_id == artifact_id)
        artifact = db.execute(stmt_art).scalar_one_or_none()
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "RESUME_NOT_FOUND", "message": "Resume artifact not found."},
            )

        from app.models.candidate import CandidateProfileRecord, ProfileCorrectionRecord

        stmt = (
            select(CandidateProfileRecord)
            .where(CandidateProfileRecord.artifact_id == artifact_id)
            .order_by(CandidateProfileRecord.profile_version.asc())
        )
        profiles = db.execute(stmt).scalars().all()

        # Query corrections map
        stmt_corr = select(ProfileCorrectionRecord).where(ProfileCorrectionRecord.artifact_id == artifact_id)
        corrections = db.execute(stmt_corr).scalars().all()
        corr_map = {c.new_profile_id: c for c in corrections}

        results = []
        from app.schemas.correction import ProfileProvenance, ProfileSummaryResponse
        for p in profiles:
            corr = corr_map.get(p.profile_id)
            corr_summary = None
            if corr:
                corr_summary = {
                    "correctionId": str(corr.correction_id),
                    "reasonCategory": corr.reason_category,
                    "reviewerNote": corr.reviewer_note,
                    "operationCount": len(corr.operations_payload),
                }

            provenance_enum = ProfileProvenance.PARSER_EXTRACTED
            try:
                provenance_enum = ProfileProvenance(p.provenance)
            except (ValueError, KeyError):
                pass

            results.append(
                ProfileSummaryResponse(
                    profile_id=str(p.profile_id),
                    profile_version=p.profile_version,
                    schema_version=p.schema_version,
                    parser_pipeline_version=p.parser_pipeline_version,
                    provenance=provenance_enum,
                    review_required=p.review_required,
                    created_at=p.created_at.isoformat(),
                    parent_profile_id=str(p.parent_profile_id) if p.parent_profile_id else None,
                    parent_profile_version=p.parent_profile_version,
                    correction_summary=corr_summary,
                )
            )
        return results

    except HTTPException:
        raise
    except SQLAlchemyError as e:
        logger.error(
            "Database error querying candidate profile history",
            exc_info=True,
            extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "INTAKE_UNAVAILABLE", "message": "Database service is currently unavailable."},
        )


@router.post(
    "/{artifact_id}/profile-corrections",
    response_model=ProfileCorrectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Correct Candidate Profile",
    description="Submits an audited batch of human corrections to create a new profile version without modifying original extractions or historical profile versions.",
)
async def correct_candidate_profile(
    artifact_id: UUID,
    correction_req: ProfileCorrectionRequest,
    db: Session = Depends(get_db),
) -> ProfileCorrectionResponse:
    try:
        from app.services.profile_correction import (
            CorrectionError,
            apply_profile_correction,
        )

        response = apply_profile_correction(
            session=db,
            artifact_id=artifact_id,
            correction_req=correction_req,
        )
        db.commit()
        return response

    except CorrectionError as e:
        db.rollback()
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "message": e.message},
        )
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(
            "Database error applying candidate profile correction",
            exc_info=True,
            extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "INTAKE_UNAVAILABLE", "message": "Database service is currently unavailable."},
        )
    except Exception as e:
        db.rollback()
        logger.error(
            "Unexpected error applying candidate profile correction",
            exc_info=True,
            extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "CORRECTION_FAILED", "message": "Failed to apply profile correction."},
        )


@router.get(
    "",
    response_model=list[CandidateListItemResponse],
    status_code=status.HTTP_200_OK,
    summary="List Candidate Resumes",
    description="Returns recent candidate resume documents and their latest profile status for the recruiter workspace.",
)
async def list_candidate_resumes(
    db: Session = Depends(get_db),
) -> list[CandidateListItemResponse]:
    try:
        from app.models.candidate import CandidateProfileRecord
        from app.models.candidate_access import CandidateApplicationRecord, CandidateApplicationStatus

        stmt_art = select(ResumeArtifact).options(selectinload(ResumeArtifact.extraction)).order_by(ResumeArtifact.created_at.desc())
        artifacts = db.execute(stmt_art).scalars().all()

        results = []
        for art in artifacts:
            stmt_prof = (
                select(CandidateProfileRecord)
                .options(selectinload(CandidateProfileRecord.identity))
                .where(CandidateProfileRecord.artifact_id == art.artifact_id)
                .order_by(CandidateProfileRecord.profile_version.desc())
            )
            latest_prof = db.execute(stmt_prof).scalars().first()

            # Check if this artifact or profile is associated with candidate application(s)
            app_conditions = [CandidateApplicationRecord.artifact_id == art.artifact_id]
            if latest_prof and latest_prof.identity_id:
                app_conditions.append(CandidateApplicationRecord.candidate_id == latest_prof.identity_id)

            from sqlalchemy import or_
            stmt_apps = select(CandidateApplicationRecord).where(or_(*app_conditions))
            associated_apps = db.execute(stmt_apps).scalars().all()

            if associated_apps:
                # If there are candidate applications associated with this artifact/candidate,
                # only keep the candidate in recruiter directory if at least one application is active/submitted
                # (i.e. not all applications are WITHDRAWN or non-submitted)
                has_active_submission = any(
                    a.status == CandidateApplicationStatus.SUBMITTED.value for a in associated_apps
                )
                if not has_active_submission:
                    # All associated applications are withdrawn or drafts; exclude from active candidate directory
                    continue

            has_ext = getattr(art, "extraction", None) is not None
            full_name = None
            headline = None
            rev_req = False
            prov = None
            latest_v = None
            latest_pid = None

            if latest_prof:
                latest_v = latest_prof.profile_version
                latest_pid = latest_prof.profile_id
                rev_req = latest_prof.review_required
                prov = latest_prof.provenance
                if latest_prof.identity:
                    full_name = latest_prof.identity.full_name
                if latest_prof.professional_profile:
                    hl_obj = latest_prof.professional_profile.get("headline")
                    if isinstance(hl_obj, dict):
                        headline = hl_obj.get("value")
            elif getattr(art, "extraction", None):
                rev_req = art.extraction.review_required

            created_at_str = ""
            if hasattr(art, "created_at") and art.created_at:
                created_at_str = art.created_at.isoformat() if hasattr(art.created_at, "isoformat") else str(art.created_at)

            results.append(
                CandidateListItemResponse(
                    artifact_id=art.artifact_id,
                    original_filename=art.original_filename,
                    detected_format=art.detected_format,
                    validation_status=art.validation_status,
                    created_at=created_at_str,
                    has_extraction=has_ext,
                    latest_profile_version=latest_v,
                    latest_profile_id=latest_pid,
                    full_name=full_name,
                    headline=headline,
                    review_required=rev_req,
                    provenance=prov,
                )
            )
        return results

    except SQLAlchemyError as e:
        logger.error(
            "Database error listing candidate resumes",
            exc_info=True,
            extra={"error_type": type(e).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "INTAKE_UNAVAILABLE", "message": "Database service is currently unavailable."},
        )

