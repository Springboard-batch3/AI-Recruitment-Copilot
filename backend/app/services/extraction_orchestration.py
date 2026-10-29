import dataclasses
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.models.extraction import ExtractionBlock, ResumeExtraction
from app.schemas.extraction import ExtractionBlockData
from app.models.resume import (
    ArtifactFormat,
    ArtifactValidationStatus,
    ProcessingJob,
    ProcessingJobStatus,
    ResumeArtifact,
)
from app.services.docx_text_extraction import (
    ArtifactIntegrityError as DOCXIntegrityError,
    DOCXExtractionError,
    DOCXExtractionFailedError,
    DOCXTextLimitExceededError,
    DOCXTextUnavailableError,
    extract_docx_text,
)
from app.services.extraction_persistence import (
    ArtifactNotValidated,
    ExtractionAlreadyExists,
    persist_extraction,
)
from app.services.job_lifecycle import (
    InvalidJobTransition,
    SafeProcessingErrorCode,
    fail_job,
    retry_job,
    start_job,
    succeed_job,
)
from app.services.pdf_text_extraction import (
    ArtifactIntegrityError as PDFIntegrityError,
    PDFDirectTextUnavailableError,
    PDFExtractionError,
    PDFExtractionFailedError,
    PDFTextLimitExceededError,
    extract_pdf_direct_text,
)
from app.services.text_normalization import (
    NormalizedTextUnavailableError,
    TextNormalizationError,
    normalize_extraction_result,
)

logger = logging.getLogger("ai_hiring.services.extraction_orchestration")


class OrchestrationError(Exception):
    """Base exception for extraction orchestration failures with HTTP status mapping."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ResumeNotFoundError(OrchestrationError):
    def __init__(self, message: str = "Resume artifact or processing job not found."):
        super().__init__(code="RESUME_NOT_FOUND", message=message, status_code=404)


class JobAlreadyRunningError(OrchestrationError):
    def __init__(self, message: str = "A processing job is already running for this document."):
        super().__init__(code="JOB_ALREADY_RUNNING", message=message, status_code=409)


class ExtractionStateConflictError(OrchestrationError):
    def __init__(self, message: str = "An extraction already exists in an inconsistent state."):
        super().__init__(code="EXTRACTION_ALREADY_EXISTS", message=message, status_code=409)


class DirectTextUnavailableOrchestrationError(OrchestrationError):
    def __init__(
        self,
        message: str = "No readable digital text was found. Please upload a text-based PDF or DOCX document.",
    ):
        super().__init__(code="DIRECT_TEXT_UNAVAILABLE", message=message, status_code=422)


class UnsupportedDocumentContentError(OrchestrationError):
    def __init__(self, message: str = "The document contains unsupported or unreadable content."):
        super().__init__(code="UNSUPPORTED_DOCUMENT_CONTENT", message=message, status_code=422)


class ExtractionUnavailableError(OrchestrationError):
    def __init__(self, message: str = "The extraction service is currently unavailable."):
        super().__init__(code="EXTRACTION_UNAVAILABLE", message=message, status_code=503)


@dataclasses.dataclass(frozen=True)
class OrchestrationSuccess:
    artifact_id: UUID
    job_id: UUID
    extraction_id: UUID
    processing_status: ProcessingJobStatus
    review_required: bool
    block_count: int


def _claim_job_transaction(
    db: Session,
    artifact_id: UUID,
) -> tuple[ResumeArtifact, ProcessingJob]:
    """
    Transaction 1:
    Locks artifact & job, checks state, handles retry if FAILED, transitions QUEUED -> RUNNING,
    and commits the RUNNING state.
    """
    # Query artifact
    stmt_art = (
        select(ResumeArtifact)
        .where(ResumeArtifact.artifact_id == artifact_id)
        .with_for_update()
    )
    artifact = db.execute(stmt_art).scalar_one_or_none()
    if artifact is None:
        raise ResumeNotFoundError()

    # Query job
    stmt_job = (
        select(ProcessingJob)
        .where(ProcessingJob.artifact_id == artifact_id)
        .with_for_update()
    )
    job = db.execute(stmt_job).scalar_one_or_none()
    if job is None:
        raise ResumeNotFoundError()

    # Check if already SUCCEEDED -> Idempotent check
    if job.status == ProcessingJobStatus.SUCCEEDED:
        # Verify extraction exists
        stmt_ext = select(ResumeExtraction).where(ResumeExtraction.artifact_id == artifact_id)
        existing_ext = db.execute(stmt_ext).scalar_one_or_none()
        if existing_ext is None:
            # Inconsistent state
            raise ExtractionStateConflictError("Job marked SUCCEEDED but extraction is missing.")
        return artifact, job

    # Check if RUNNING -> 409 Conflict
    if job.status == ProcessingJobStatus.RUNNING:
        raise JobAlreadyRunningError()

    # Check if extraction already exists while job is NOT SUCCEEDED
    stmt_ext_check = select(ResumeExtraction).where(ResumeExtraction.artifact_id == artifact_id)
    existing_ext_unexp = db.execute(stmt_ext_check).scalar_one_or_none()
    if existing_ext_unexp is not None:
        raise ExtractionStateConflictError("Extraction already exists for an uncompleted job.")

    # Check validation status
    if artifact.validation_status != ArtifactValidationStatus.VALIDATED:
        raise OrchestrationError(
            code="ARTIFACT_NOT_VALIDATED",
            message="Resume artifact must be in VALIDATED state before processing.",
            status_code=422,
        )

    # If FAILED -> retry
    if job.status == ProcessingJobStatus.FAILED:
        retry_job(job)

    # Transition QUEUED -> RUNNING
    start_job(job)
    db.commit()

    return artifact, job


def _fail_job_transaction(
    db: Session,
    artifact_id: UUID,
    error_code: SafeProcessingErrorCode,
) -> None:
    """
    Failure Transaction:
    Safely transitions RUNNING job to FAILED, records controlled safe_error_code, and commits.
    """
    try:
        db.rollback()
        stmt_job = (
            select(ProcessingJob)
            .where(ProcessingJob.artifact_id == artifact_id)
            .with_for_update()
        )
        job = db.execute(stmt_job).scalar_one_or_none()
        if job and job.status == ProcessingJobStatus.RUNNING:
            fail_job(job, error_code)
            db.commit()
    except Exception as e:
        logger.error(
            "Failed to record job failure transition",
            extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__},
        )
        db.rollback()


def orchestrate_resume_extraction(
    artifact_id: UUID,
    db: Session,
    settings: Settings,
) -> OrchestrationSuccess:
    """
    Synchronous processing orchestration:
    1. Transaction 1: Claim QUEUED (or retried FAILED) job -> RUNNING (committed).
       If already SUCCEEDED, returns existing result idempotently.
    2. Outside transaction: Run format-specific direct text extraction & normalization.
    3. Transaction 2: Persist extraction & blocks, transition RUNNING -> SUCCEEDED (committed).
    4. Failure: Map internal failure to SafeProcessingErrorCode, transition RUNNING -> FAILED (committed).
    """
    # 1. Claim job (Transaction 1)
    artifact, job = _claim_job_transaction(db, artifact_id)

    # Idempotent response for SUCCEEDED job
    if job.status == ProcessingJobStatus.SUCCEEDED:
        stmt_ext = (
            select(ResumeExtraction)
            .options(selectinload(ResumeExtraction.blocks))
            .where(ResumeExtraction.artifact_id == artifact_id)
        )
        extraction = db.execute(stmt_ext).scalar_one()
        return OrchestrationSuccess(
            artifact_id=artifact.artifact_id,
            job_id=job.job_id,
            extraction_id=extraction.extraction_id,
            processing_status=job.status,
            review_required=extraction.review_required,
            block_count=len(extraction.blocks),
        )

    # 2. Extract & Normalize (Outside DB transaction)
    try:
        if artifact.detected_format == ArtifactFormat.pdf:
            pdf_outcome = extract_pdf_direct_text(artifact, settings)
            if pdf_outcome.extraction_result is None:
                raise PDFDirectTextUnavailableError()
            raw_extraction = pdf_outcome.extraction_result
        elif artifact.detected_format == ArtifactFormat.docx:
            docx_outcome = extract_docx_text(artifact, settings)
            if docx_outcome.extraction_result is None:
                raise DOCXTextUnavailableError()
            raw_extraction = docx_outcome.extraction_result
        else:
            raise UnsupportedDocumentContentError("Unsupported artifact format for extraction.")

        # Normalize extraction result
        normalization_outcome = normalize_extraction_result(raw_extraction)
        normalized_extraction = normalization_outcome.extraction_result

    except (PDFDirectTextUnavailableError, DOCXTextUnavailableError, NormalizedTextUnavailableError) as e:
        logger.info("Direct text unavailable for artifact", extra={"artifact_id": str(artifact_id)})
        _fail_job_transaction(db, artifact_id, SafeProcessingErrorCode.DIRECT_TEXT_UNAVAILABLE)
        raise DirectTextUnavailableOrchestrationError() from e

    except (PDFIntegrityError, DOCXIntegrityError) as e:
        logger.warning("Artifact integrity verification failed", extra={"artifact_id": str(artifact_id)})
        _fail_job_transaction(db, artifact_id, SafeProcessingErrorCode.ARTIFACT_INTEGRITY_FAILED)
        raise OrchestrationError(
            code="ARTIFACT_INTEGRITY_FAILED",
            message="The stored document could not be verified.",
            status_code=422,
        ) from e

    except (PDFTextLimitExceededError, DOCXTextLimitExceededError) as e:
        logger.warning("Document extraction limit exceeded", extra={"artifact_id": str(artifact_id)})
        _fail_job_transaction(db, artifact_id, SafeProcessingErrorCode.RESOURCE_LIMIT_EXCEEDED)
        raise OrchestrationError(
            code="RESOURCE_LIMIT_EXCEEDED",
            message="The document exceeded the safe extraction limits.",
            status_code=422,
        ) from e

    except (PDFExtractionFailedError, DOCXExtractionFailedError) as e:
        logger.warning("Extraction failed for document", extra={"artifact_id": str(artifact_id)})
        _fail_job_transaction(db, artifact_id, SafeProcessingErrorCode.DOCUMENT_READ_FAILED)
        raise UnsupportedDocumentContentError("The document could not be read or parsed.") from e

    except Exception as e:
        logger.error(
            "Unexpected error during extraction / normalization",
            extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__},
        )
        _fail_job_transaction(db, artifact_id, SafeProcessingErrorCode.INTERNAL_PROCESSING_ERROR)
        raise ExtractionUnavailableError("The document could not be processed at this time.") from e

    # 3. Persist extraction and succeed job (Transaction 2)
    try:
        # Lock job and artifact for final commit
        stmt_art_2 = select(ResumeArtifact).where(ResumeArtifact.artifact_id == artifact_id).with_for_update()
        artifact_locked = db.execute(stmt_art_2).scalar_one()

        stmt_job_2 = select(ProcessingJob).where(ProcessingJob.artifact_id == artifact_id).with_for_update()
        job_locked = db.execute(stmt_job_2).scalar_one()

        if job_locked.status != ProcessingJobStatus.RUNNING:
            raise ExtractionStateConflictError("Job is no longer in RUNNING state.")

        # Persist extraction & blocks using Phase 1C.1 service
        persisted_extraction = persist_extraction(
            session=db,
            artifact=artifact_locked,
            result=normalized_extraction,
        )

        # Assemble and persist CandidateProfile (Phase 1D.5)
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
            for b in persisted_extraction.blocks
        ]

        from app.services.profile_assembly import (
            assemble_candidate_profile,
            persist_candidate_profile,
        )

        # Determine deterministic reference as-of date from persisted artifact created_at
        as_of_date_str = (
            artifact_locked.created_at.strftime("%Y-%m")
            if artifact_locked.created_at is not None
            else None
        )

        assembled_profile = assemble_candidate_profile(
            blocks=blocks_data,
            artifact_id=str(artifact_locked.artifact_id),
            as_of_date=as_of_date_str,
            extraction_review_required=persisted_extraction.review_required,
        )

        persist_candidate_profile(
            session=db,
            artifact_id=artifact_locked.artifact_id,
            extraction_id=persisted_extraction.extraction_id,
            assembled_profile=assembled_profile,
        )

        # Transition job RUNNING -> SUCCEEDED
        succeed_job(job_locked)

        db.commit()

        return OrchestrationSuccess(
            artifact_id=artifact_locked.artifact_id,
            job_id=job_locked.job_id,
            extraction_id=persisted_extraction.extraction_id,
            processing_status=job_locked.status,
            review_required=persisted_extraction.review_required or assembled_profile.review_required,
            block_count=len(persisted_extraction.blocks),
        )

    except (ExtractionAlreadyExists, ExtractionStateConflictError) as e:
        db.rollback()
        raise ExtractionStateConflictError(str(e)) from e
    except Exception as e:
        logger.error(
            "Database commit failed during extraction persistence",
            exc_info=True,
            extra={"artifact_id": str(artifact_id), "error_type": type(e).__name__},
        )
        _fail_job_transaction(db, artifact_id, SafeProcessingErrorCode.INTERNAL_PROCESSING_ERROR)
        raise ExtractionUnavailableError("Failed to persist extraction result.") from e

