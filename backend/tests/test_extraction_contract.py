import decimal
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase

from app.models import (
    Base,
    BlockType,
    ExtractionBlock,
    ExtractionMethod,
    ResumeArtifact,
    ResumeExtraction,
    ArtifactValidationStatus,
)
from app.schemas.extraction import ExtractionBlockData, ExtractionResult
from app.services.extraction_persistence import (
    ArtifactNotValidated,
    ExtractionAlreadyExists,
    InvalidExtractionResult,
    persist_extraction,
)


# ==========================================
# Schema Tests
# ==========================================


def test_valid_extraction_result_schema():
    """Verify valid ExtractionResult and ExtractionBlockData serialization and camelCase alias."""
    block1 = ExtractionBlockData(
        sequence_index=0,
        page_number=1,
        structural_location=None,
        block_type=BlockType.HEADING,
        raw_text="Jane Doe",
        normalized_text="Jane Doe",
        extraction_method=ExtractionMethod.PDF_TEXT,
        confidence=None,
        review_required=False,
    )
    block2 = ExtractionBlockData(
        sequence_index=1,
        page_number=1,
        structural_location=None,
        block_type=BlockType.PARAGRAPH,
        raw_text="Software Engineer with 5 years experience.",
        normalized_text="Software Engineer with 5 years experience.",
        extraction_method=ExtractionMethod.PDF_TEXT,
        confidence=Decimal("0.9500"),
        review_required=False,
    )
    result = ExtractionResult(
        extractor_version="pdf-extractor-v1.0",
        review_required=False,
        blocks=[block1, block2],
    )

    assert result.extractor_version == "pdf-extractor-v1.0"
    assert result.review_required is False
    assert len(result.blocks) == 2
    assert result.blocks[0].sequence_index == 0
    assert result.blocks[1].sequence_index == 1

    # Verify camelCase serialization
    dumped = result.model_dump(by_alias=True)
    assert "extractorVersion" in dumped
    assert "reviewRequired" in dumped
    assert "blocks" in dumped
    assert len(dumped["blocks"]) == 2
    first_block = dumped["blocks"][0]
    assert "sequenceIndex" in first_block
    assert "pageNumber" in first_block
    assert "structuralLocation" in first_block
    assert "blockType" in first_block
    assert "rawText" in first_block
    assert "normalizedText" in first_block
    assert "extractionMethod" in first_block
    assert "confidence" in first_block
    assert "reviewRequired" in first_block


def test_docx_structural_location_schema():
    """Verify DOCX block with structural_location and no page_number."""
    block = ExtractionBlockData(
        sequence_index=0,
        page_number=None,
        structural_location="body/paragraph/12",
        block_type=BlockType.PARAGRAPH,
        raw_text="Education section",
        normalized_text="Education section",
        extraction_method=ExtractionMethod.DOCX_TEXT,
        confidence=None,
    )
    result = ExtractionResult(
        extractor_version="docx-extractor-v1.0",
        blocks=[block],
    )
    assert result.blocks[0].structural_location == "body/paragraph/12"
    assert result.blocks[0].page_number is None


def test_invalid_confidence_or_missing_locator_rejected():
    """Verify rejection when confidence is outside [0, 1] or both locators are missing."""
    # Confidence > 1.0
    with pytest.raises(ValidationError) as exc:
        ExtractionBlockData(
            sequence_index=0,
            page_number=1,
            block_type=BlockType.PARAGRAPH,
            raw_text="test",
            normalized_text="test",
            extraction_method=ExtractionMethod.PDF_TEXT,
            confidence=Decimal("1.05"),
        )
    assert "confidence" in str(exc.value)

    # Confidence < 0.0
    with pytest.raises(ValidationError) as exc:
        ExtractionBlockData(
            sequence_index=0,
            page_number=1,
            block_type=BlockType.PARAGRAPH,
            raw_text="test",
            normalized_text="test",
            extraction_method=ExtractionMethod.PDF_TEXT,
            confidence=Decimal("-0.1"),
        )
    assert "confidence" in str(exc.value)

    # Missing both page_number and structural_location
    with pytest.raises(ValidationError) as exc:
        ExtractionBlockData(
            sequence_index=0,
            page_number=None,
            structural_location=None,
            block_type=BlockType.PARAGRAPH,
            raw_text="test",
            normalized_text="test",
            extraction_method=ExtractionMethod.PDF_TEXT,
        )
    assert "source locator" in str(exc.value)

    # Blank structural_location when page_number is None
    with pytest.raises(ValidationError) as exc:
        ExtractionBlockData(
            sequence_index=0,
            page_number=None,
            structural_location="   ",
            block_type=BlockType.PARAGRAPH,
            raw_text="test",
            normalized_text="test",
            extraction_method=ExtractionMethod.PDF_TEXT,
        )
    assert "structural_location" in str(exc.value) or "source locator" in str(exc.value)


def test_both_raw_and_normalized_text_blank_rejected():
    """Verify rejection when both raw_text and normalized_text are empty/blank."""
    with pytest.raises(ValidationError) as exc:
        ExtractionBlockData(
            sequence_index=0,
            page_number=1,
            block_type=BlockType.PARAGRAPH,
            raw_text="",
            normalized_text="   ",
            extraction_method=ExtractionMethod.PDF_TEXT,
        )
    assert "raw_text and normalized_text cannot both be blank" in str(exc.value)


def test_empty_blocks_list_rejected():
    """Verify rejection when ExtractionResult contains no blocks."""
    with pytest.raises(ValidationError) as exc:
        ExtractionResult(
            extractor_version="v1.0",
            blocks=[],
        )
    assert "ExtractionResult blocks list cannot be empty" in str(exc.value)


def test_blank_extractor_version_rejected():
    """Verify rejection when extractor_version is blank."""
    block = ExtractionBlockData(
        sequence_index=0,
        page_number=1,
        block_type=BlockType.PARAGRAPH,
        raw_text="text",
        normalized_text="text",
        extraction_method=ExtractionMethod.PDF_TEXT,
    )
    with pytest.raises(ValidationError) as exc:
        ExtractionResult(
            extractor_version="   ",
            blocks=[block],
        )
    assert "extractor_version" in str(exc.value)


def test_duplicate_or_non_contiguous_sequence_values_rejected():
    """Verify rejection when sequence indexes are non-contiguous, duplicate, or not starting at 0."""
    b0 = ExtractionBlockData(
        sequence_index=0,
        page_number=1,
        block_type=BlockType.PARAGRAPH,
        raw_text="text 0",
        normalized_text="text 0",
        extraction_method=ExtractionMethod.PDF_TEXT,
    )
    b0_dup = ExtractionBlockData(
        sequence_index=0,
        page_number=1,
        block_type=BlockType.PARAGRAPH,
        raw_text="text 0 dup",
        normalized_text="text 0 dup",
        extraction_method=ExtractionMethod.PDF_TEXT,
    )
    b2_gap = ExtractionBlockData(
        sequence_index=2,
        page_number=1,
        block_type=BlockType.PARAGRAPH,
        raw_text="text 2",
        normalized_text="text 2",
        extraction_method=ExtractionMethod.PDF_TEXT,
    )
    b1 = ExtractionBlockData(
        sequence_index=1,
        page_number=1,
        block_type=BlockType.PARAGRAPH,
        raw_text="text 1",
        normalized_text="text 1",
        extraction_method=ExtractionMethod.PDF_TEXT,
    )

    # Duplicate sequence index [0, 0]
    with pytest.raises(ValidationError) as exc:
        ExtractionResult(
            extractor_version="v1.0",
            blocks=[b0, b0_dup],
        )
    assert "Sequence indexes must be unique, ordered, and contiguous" in str(exc.value)

    # Gap in sequence index [0, 2]
    with pytest.raises(ValidationError) as exc:
        ExtractionResult(
            extractor_version="v1.0",
            blocks=[b0, b2_gap],
        )
    assert "Sequence indexes must be unique, ordered, and contiguous" in str(exc.value)

    # Non-zero start [1, 2]
    with pytest.raises(ValidationError) as exc:
        ExtractionResult(
            extractor_version="v1.0",
            blocks=[b1, b2_gap],
        )
    assert "Sequence indexes must be unique, ordered, and contiguous" in str(exc.value)


def test_extra_fields_forbidden_in_schemas():
    """Verify extra/unknown fields are rejected."""
    with pytest.raises(ValidationError) as exc:
        ExtractionBlockData(
            sequence_index=0,
            page_number=1,
            block_type=BlockType.PARAGRAPH,
            raw_text="text",
            normalized_text="text",
            extraction_method=ExtractionMethod.PDF_TEXT,
            extra_field="not_allowed",
        )
    assert "Extra inputs are not permitted" in str(exc.value)

    with pytest.raises(ValidationError) as exc:
        ExtractionResult(
            extractor_version="v1.0",
            blocks=[
                ExtractionBlockData(
                    sequence_index=0,
                    page_number=1,
                    block_type=BlockType.PARAGRAPH,
                    raw_text="text",
                    normalized_text="text",
                    extraction_method=ExtractionMethod.PDF_TEXT,
                )
            ],
            unexpected="fail",
        )
    assert "Extra inputs are not permitted" in str(exc.value)


# ==========================================
# Model Structure & Constraints Tests
# ==========================================


def test_extraction_models_table_structure_and_constraints():
    """Verify table names, columns, PKs, FKs, constraints, and indexes."""
    # resume_extractions table
    ext_table = Base.metadata.tables["resume_extractions"]
    assert ext_table.c.extraction_id.primary_key is True
    assert isinstance(ext_table.c.extraction_id.type, UUID)
    assert ext_table.c.artifact_id.nullable is False
    assert ext_table.c.extractor_version.nullable is False
    assert ext_table.c.review_required.nullable is False
    assert ext_table.c.created_at.nullable is False
    assert ext_table.c.created_at.type.timezone is True

    # Artifact FK on resume_extractions
    fk_list = list(ext_table.c.artifact_id.foreign_keys)
    assert len(fk_list) == 1
    assert fk_list[0].target_fullname == "resume_artifacts.artifact_id"
    assert fk_list[0].ondelete == "CASCADE"

    # Unique constraint on artifact_id
    uq_names = {c.name for c in ext_table.constraints if hasattr(c, "columns")}
    assert "uq_resume_extractions_artifact_id" in uq_names

    # extraction_blocks table
    block_table = Base.metadata.tables["extraction_blocks"]
    assert block_table.c.block_id.primary_key is True
    assert isinstance(block_table.c.block_id.type, UUID)
    assert block_table.c.extraction_id.nullable is False
    assert block_table.c.sequence_index.nullable is False
    assert block_table.c.page_number.nullable is True
    assert block_table.c.structural_location.nullable is True
    assert block_table.c.block_type.nullable is False
    assert block_table.c.raw_text.nullable is False
    assert block_table.c.normalized_text.nullable is False
    assert block_table.c.extraction_method.nullable is False
    assert block_table.c.confidence.nullable is True
    assert isinstance(block_table.c.confidence.type, Numeric)
    assert block_table.c.review_required.nullable is False
    assert block_table.c.created_at.nullable is False
    assert block_table.c.created_at.type.timezone is True

    # Extraction FK on extraction_blocks
    block_fks = list(block_table.c.extraction_id.foreign_keys)
    assert len(block_fks) == 1
    assert block_fks[0].target_fullname == "resume_extractions.extraction_id"
    assert block_fks[0].ondelete == "CASCADE"

    # Check constraints and indexes on extraction_blocks
    block_constraints = {c.name for c in block_table.constraints if c.name is not None}
    assert "uq_extraction_blocks_extraction_id_sequence_index" in block_constraints
    assert "ck_extraction_blocks_sequence_index_non_negative" in block_constraints
    assert "ck_extraction_blocks_page_number_positive" in block_constraints
    assert "ck_extraction_blocks_confidence_range" in block_constraints

    block_indexes = {idx.name for idx in block_table.indexes}
    assert "ix_extraction_blocks_extraction_id" in block_indexes


def test_no_forbidden_fields_in_extraction_model():
    """Verify resume extraction model does not duplicate artifact fields."""
    ext_cols = set(Base.metadata.tables["resume_extractions"].columns.keys())
    forbidden = {
        "file_format",
        "original_filename",
        "mime_type",
        "declared_mime_type",
        "sha256",
        "sha256_hash",
        "quarantine_key",
        "processing_status",
    }
    assert forbidden.isdisjoint(ext_cols)


# ==========================================
# Persistence Service Tests
# ==========================================


class MockExtractionSession:
    def __init__(self, existing_extraction_id=None):
        self.added = []
        self.flushed = False
        self.committed = False
        self.rolled_back = False
        self.existing_extraction_id = existing_extraction_id

    def add(self, instance):
        self.added.append(instance)

    def flush(self):
        self.flushed = True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def execute(self, statement):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = self.existing_extraction_id
        return mock_result


def test_extraction_and_ordered_blocks_persist_correctly():
    """Verify extraction and ordered blocks persist correctly through service."""
    art_id = uuid.uuid4()
    artifact = ResumeArtifact(
        artifact_id=art_id,
        original_filename="resume.pdf",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    result = ExtractionResult(
        extractor_version="pdf-v1",
        review_required=False,
        blocks=[
            ExtractionBlockData(
                sequence_index=0,
                page_number=1,
                block_type=BlockType.HEADING,
                raw_text="Alice Smith",
                normalized_text="Alice Smith",
                extraction_method=ExtractionMethod.PDF_TEXT,
            ),
            ExtractionBlockData(
                sequence_index=1,
                page_number=1,
                block_type=BlockType.PARAGRAPH,
                raw_text="Senior Developer",
                normalized_text="Senior Developer",
                extraction_method=ExtractionMethod.PDF_TEXT,
                confidence=Decimal("0.9000"),
            ),
        ],
    )

    session = MockExtractionSession()
    extraction = persist_extraction(session, artifact, result)

    assert extraction is not None
    assert extraction.artifact_id == art_id
    assert extraction.extractor_version == "pdf-v1"
    assert extraction.review_required is False
    assert len(extraction.blocks) == 2

    # Check block ordering and properties
    assert extraction.blocks[0].sequence_index == 0
    assert extraction.blocks[0].raw_text == "Alice Smith"
    assert extraction.blocks[0].extraction_id == extraction.extraction_id
    assert extraction.blocks[1].sequence_index == 1
    assert extraction.blocks[1].raw_text == "Senior Developer"
    assert extraction.blocks[1].confidence == Decimal("0.9000")

    # Relationship populated
    assert artifact.extraction == extraction
    assert extraction.artifact == artifact

    # Session actions
    assert session.flushed is True
    assert session.committed is False  # Service must NOT commit
    assert extraction in session.added


def test_non_validated_artifact_is_rejected():
    """Verify persistence refuses artifacts not in VALIDATED state."""
    art_id = uuid.uuid4()
    artifact = ResumeArtifact(
        artifact_id=art_id,
        original_filename="resume.pdf",
        validation_status=ArtifactValidationStatus.PENDING,
    )
    result = ExtractionResult(
        extractor_version="pdf-v1",
        blocks=[
            ExtractionBlockData(
                sequence_index=0,
                page_number=1,
                block_type=BlockType.HEADING,
                raw_text="Alice",
                normalized_text="Alice",
                extraction_method=ExtractionMethod.PDF_TEXT,
            )
        ],
    )

    session = MockExtractionSession()
    with pytest.raises(ArtifactNotValidated) as exc:
        persist_extraction(session, artifact, result)

    assert "VALIDATED status" in str(exc.value)
    assert session.flushed is False


def test_second_extraction_for_one_artifact_is_rejected():
    """Verify persistence refuses creating a second extraction for the same artifact."""
    art_id = uuid.uuid4()
    artifact = ResumeArtifact(
        artifact_id=art_id,
        original_filename="resume.pdf",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    # Simulate already having extraction attached in memory
    artifact.extraction = ResumeExtraction(
        extraction_id=uuid.uuid4(),
        artifact_id=art_id,
        extractor_version="v1",
    )

    result = ExtractionResult(
        extractor_version="pdf-v1",
        blocks=[
            ExtractionBlockData(
                sequence_index=0,
                page_number=1,
                block_type=BlockType.HEADING,
                raw_text="Alice",
                normalized_text="Alice",
                extraction_method=ExtractionMethod.PDF_TEXT,
            )
        ],
    )

    session = MockExtractionSession()
    with pytest.raises(ExtractionAlreadyExists) as exc:
        persist_extraction(session, artifact, result)
    assert "already exists" in str(exc.value)

    # Also test DB query detection
    artifact_no_mem = ResumeArtifact(
        artifact_id=art_id,
        original_filename="resume.pdf",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    session_with_db_match = MockExtractionSession(existing_extraction_id=uuid.uuid4())
    with pytest.raises(ExtractionAlreadyExists) as exc2:
        persist_extraction(session_with_db_match, artifact_no_mem, result)
    assert "already exists" in str(exc2.value)


def test_persistence_service_does_not_commit():
    """Verify persistence service never calls commit on the session."""
    art_id = uuid.uuid4()
    artifact = ResumeArtifact(
        artifact_id=art_id,
        original_filename="resume.pdf",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    result = ExtractionResult(
        extractor_version="pdf-v1",
        blocks=[
            ExtractionBlockData(
                sequence_index=0,
                page_number=1,
                block_type=BlockType.HEADING,
                raw_text="Alice",
                normalized_text="Alice",
                extraction_method=ExtractionMethod.PDF_TEXT,
            )
        ],
    )
    session = MockExtractionSession()
    persist_extraction(session, artifact, result)

    assert session.flushed is True
    assert session.committed is False


def test_sensitive_text_does_not_appear_in_controlled_errors():
    """Verify exceptions never include extracted text or internal database errors."""
    sensitive_candidate_text = "SECRET_CANDIDATE_PERSONAL_INFO_98765"
    art_id = uuid.uuid4()
    artifact = ResumeArtifact(
        artifact_id=art_id,
        original_filename="resume.pdf",
        validation_status=ArtifactValidationStatus.PENDING,
    )
    result = ExtractionResult(
        extractor_version="pdf-v1",
        blocks=[
            ExtractionBlockData(
                sequence_index=0,
                page_number=1,
                block_type=BlockType.PARAGRAPH,
                raw_text=sensitive_candidate_text,
                normalized_text=sensitive_candidate_text,
                extraction_method=ExtractionMethod.PDF_TEXT,
            )
        ],
    )

    session = MockExtractionSession()
    with pytest.raises(ArtifactNotValidated) as exc:
        persist_extraction(session, artifact, result)

    err_str = str(exc.value)
    assert sensitive_candidate_text not in err_str
    assert "SECRET" not in err_str


def test_invalid_arguments_to_persist_extraction():
    """Verify invalid argument types raise InvalidExtractionResult."""
    session = MockExtractionSession()
    with pytest.raises(InvalidExtractionResult):
        persist_extraction(session, None, None)
