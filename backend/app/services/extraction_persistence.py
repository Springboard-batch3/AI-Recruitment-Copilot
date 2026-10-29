import logging
import uuid
from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.extraction import ExtractionBlock, ResumeExtraction
from app.models.resume import ArtifactValidationStatus, ResumeArtifact
from app.schemas.extraction import ExtractionResult

logger = logging.getLogger("ai_hiring.services.extraction_persistence")


class ExtractionPersistenceError(Exception):
    """Base exception for extraction persistence failures."""
    pass


class ArtifactNotValidated(ExtractionPersistenceError):
    """Raised when attempting to persist extraction for an artifact not in VALIDATED state."""
    pass


class ExtractionAlreadyExists(ExtractionPersistenceError):
    """Raised when an extraction already exists for the given artifact."""
    pass


class InvalidExtractionResult(ExtractionPersistenceError):
    """Raised when an invalid extraction result is supplied."""
    pass


def persist_extraction(
    session: Session,
    artifact: ResumeArtifact,
    result: ExtractionResult,
) -> ResumeExtraction:
    """
    Persists a validated ExtractionResult for a validated ResumeArtifact into the database.

    Rules:
    1. Rejects artifacts that are not in VALIDATED status.
    2. Refuses to create a second extraction for the same artifact.
    3. Creates ResumeExtraction and its ordered ExtractionBlock records.
    4. Attaches blocks through relationships.
    5. Adds and flushes using the provided session (does NOT commit).
    6. Never logs extracted text or candidate information.
    7. Never includes extracted text or database exception internals in error messages.
    """
    if not isinstance(result, ExtractionResult):
        raise InvalidExtractionResult("Extraction result is invalid.")

    if not isinstance(artifact, ResumeArtifact):
        raise InvalidExtractionResult("Invalid resume artifact supplied.")

    if artifact.validation_status != ArtifactValidationStatus.VALIDATED:
        logger.warning(
            "Rejected extraction persistence for unvalidated artifact",
            extra={"artifact_id": str(artifact.artifact_id)},
        )
        raise ArtifactNotValidated("Resume artifact must be in VALIDATED status before extraction persistence.")

    # Check if extraction already exists for this artifact
    # Check in-memory relationship first if populated, then query session
    if artifact.extraction is not None:
        logger.warning(
            "Extraction already exists for artifact",
            extra={"artifact_id": str(artifact.artifact_id)},
        )
        raise ExtractionAlreadyExists("An extraction already exists for this resume artifact.")

    existing = session.execute(
        select(ResumeExtraction.extraction_id).where(ResumeExtraction.artifact_id == artifact.artifact_id)
    ).scalar_one_or_none()

    if existing is not None:
        logger.warning(
            "Extraction already exists for artifact in database",
            extra={"artifact_id": str(artifact.artifact_id)},
        )
        raise ExtractionAlreadyExists("An extraction already exists for this resume artifact.")

    extraction_id = uuid.uuid4()
    extraction = ResumeExtraction(
        extraction_id=extraction_id,
        artifact_id=artifact.artifact_id,
        extractor_version=result.extractor_version,
        review_required=result.review_required,
    )

    # Create ordered blocks
    for block_data in result.blocks:
        block = ExtractionBlock(
            block_id=block_data.block_id or uuid.uuid4(),
            extraction_id=extraction_id,
            sequence_index=block_data.sequence_index,
            page_number=block_data.page_number,
            structural_location=block_data.structural_location,
            block_type=block_data.block_type,
            raw_text=block_data.raw_text,
            normalized_text=block_data.normalized_text,
            extraction_method=block_data.extraction_method,
            confidence=block_data.confidence,
            review_required=block_data.review_required,
        )
        extraction.blocks.append(block)

    # Link extraction to artifact
    artifact.extraction = extraction

    session.add(extraction)
    session.flush()

    logger.info(
        "Successfully persisted extraction blocks for artifact",
        extra={
            "artifact_id": str(artifact.artifact_id),
            "extraction_id": str(extraction.extraction_id),
            "block_count": len(extraction.blocks),
        },
    )

    return extraction
