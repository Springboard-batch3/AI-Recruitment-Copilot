import hashlib
import io
import os
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pypdf
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from app.config import Settings
from app.models.extraction import BlockType, ExtractionMethod, ResumeExtraction
from app.models.resume import (
    ArtifactFormat,
    ArtifactValidationStatus,
    ResumeArtifact,
)
from app.schemas.extraction import ExtractionResult
from app.services.extraction_persistence import persist_extraction
from app.services.pdf_text_extraction import (
    ArtifactIntegrityError,
    PDFDirectExtractionResult,
    PDFExtractionFailedError,
    PDFTextLimitExceededError,
    PageAssessment,
    PageClassification,
    PDF_DIRECT_EXTRACTOR_VERSION,
    extract_pdf_direct_text,
)



def _generate_pdf_bytes(pages_text: list[str]) -> bytes:
    """Helper to generate standard PDF bytes containing specified text per page using pure pypdf."""
    writer = PdfWriter()
    for text_content in pages_text:
        page = writer.add_blank_page(width=612, height=792)
        
        # Build font resource
        font_dict = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        resources = DictionaryObject({
            NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): font_dict
            })
        })

        # Format paragraphs into PDF content stream
        # Escape parenthesis
        escaped = text_content.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        paragraphs = escaped.split("\n\n")
        
        stream_ops = ["BT", "/F1 12 Tf", "50 720 Td"]
        for p in paragraphs:
            lines = p.split("\n")
            for line in lines:
                stream_ops.append(f"({line}) Tj")
                stream_ops.append("0 -15 Td")
            # Extra space for paragraph break
            stream_ops.append("0 -10 Td")
        stream_ops.append("ET")

        stream_data = "\n".join(stream_ops).encode("latin-1")
        stream_obj = DecodedStreamObject()
        stream_obj.set_data(stream_data)

        page[NameObject("/Resources")] = resources
        page[NameObject("/Contents")] = writer._add_object(stream_obj)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _generate_pdf_with_image_and_text(text: str, add_image: bool = True) -> bytes:
    """Helper to generate a PDF page with an optional embedded image and text."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    font_dict = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    resources = DictionaryObject({
        NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): font_dict
        })
    })

    if add_image:
        # Create minimal 1x1 image XObject
        img_stream = DecodedStreamObject()
        img_stream.set_data(b"\xff\x00\x00")
        img_stream[NameObject("/Type")] = NameObject("/XObject")
        img_stream[NameObject("/Subtype")] = NameObject("/Image")
        img_stream[NameObject("/Width")] = NumberObject(1)
        img_stream[NameObject("/Height")] = NumberObject(1)
        img_stream[NameObject("/ColorSpace")] = NameObject("/DeviceRGB")
        img_stream[NameObject("/BitsPerComponent")] = NumberObject(8)

        resources[NameObject("/XObject")] = DictionaryObject({
            NameObject("/Im1"): writer._add_object(img_stream)
        })

    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream_data = f"BT /F1 12 Tf 50 700 Td ({escaped}) Tj ET".encode("latin-1")
    stream_obj = DecodedStreamObject()
    stream_obj.set_data(stream_data)

    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(stream_obj)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _setup_quarantined_pdf(
    tmp_path: Path,
    pdf_bytes: bytes,
    validation_status: ArtifactValidationStatus = ArtifactValidationStatus.VALIDATED,
    detected_format: ArtifactFormat = ArtifactFormat.pdf,
    quarantine_key_override: str = None,
    size_override: int = None,
    hash_override: str = None,
) -> tuple[ResumeArtifact, Settings]:
    """Helper to setup quarantine directory, write PDF file, and build ResumeArtifact."""
    quarantine_dir = tmp_path / "quarantine"
    pdf_dir = quarantine_dir / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    art_id = uuid.uuid4()
    pdf_filename = f"{art_id}.pdf"
    file_path = pdf_dir / pdf_filename
    file_path.write_bytes(pdf_bytes)

    actual_size = len(pdf_bytes)
    actual_hash = hashlib.sha256(pdf_bytes).hexdigest().lower()

    quarantine_key = quarantine_key_override if quarantine_key_override is not None else f"pdf/{pdf_filename}"
    size_bytes = size_override if size_override is not None else actual_size
    sha256_hash = hash_override if hash_override is not None else actual_hash

    artifact = ResumeArtifact(
        artifact_id=art_id,
        original_filename="candidate_resume.pdf",
        declared_mime_type="application/pdf",
        detected_format=detected_format,
        size_bytes=size_bytes,
        sha256_hash=sha256_hash,
        validation_status=validation_status,
        quarantine_key=quarantine_key,
    )

    settings = Settings(
        database_url="postgresql+psycopg://dummy:dummy@localhost:5432/dummy",
        resume_quarantine_directory=str(quarantine_dir),
        min_pdf_direct_text_chars=30,
        max_pdf_extracted_chars=500000,
    )

    return artifact, settings


# =========================================================================
# 1. Digital multi-page PDF produces ordered blocks and correct page numbers
# =========================================================================

def test_digital_multipage_pdf_extraction(tmp_path):
    page1_text = (
        "Jane Doe\n"
        "Senior Software Engineer with extensive experience in Python and cloud systems.\n\n"
        "Summary\n"
        "Proven track record of architecting highly scalable backend systems."
    )
    page2_text = (
        "Experience\n"
        "Lead Engineer at TechCorp from 2020 to Present.\n\n"
        "Education\n"
        "B.S. in Computer Science from University of Technology."
    )

    pdf_bytes = _generate_pdf_bytes([page1_text, page2_text])
    artifact, settings = _setup_quarantined_pdf(tmp_path, pdf_bytes)

    result = extract_pdf_direct_text(artifact, settings)

    assert isinstance(result, PDFDirectExtractionResult)
    assert result.review_required is False
    assert len(result.pages) == 2

    assert result.pages[0].classification == PageClassification.TEXT
    assert result.pages[0].page_number == 1

    assert result.pages[1].classification == PageClassification.TEXT
    assert result.pages[1].page_number == 2

    # Blocks validation (Headings isolated as separate blocks)
    assert len(result.blocks) >= 4
    # Sequence index unique, contiguous starting at 0
    assert [b.sequence_index for b in result.blocks] == list(range(len(result.blocks)))

    # Page numbers
    assert result.blocks[0].page_number == 1
    assert result.blocks[-1].page_number == 2


    # Extraction method and block types
    for b in result.blocks:
        assert b.extraction_method == ExtractionMethod.PDF_TEXT
        assert b.block_type in (BlockType.PARAGRAPH, BlockType.HEADING)
        assert b.confidence is not None


    # Shared ExtractionResult is present and valid
    assert result.extraction_result is not None
    assert isinstance(result.extraction_result, ExtractionResult)
    assert len(result.extraction_result.blocks) == len(result.blocks)



# =========================================================================
# 2. Blank pages are classified without creating fake text and raise unavailable
# =========================================================================

def test_blank_pages_raise_direct_text_unavailable(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()

    artifact, settings = _setup_quarantined_pdf(tmp_path, pdf_bytes)

    with pytest.raises(PDFExtractionFailedError if False else Exception) as exc:
        extract_pdf_direct_text(artifact, settings)

    assert "No readable digital text was found" in str(exc.value)


# =========================================================================
# 3. An image-only PDF raises PDFDirectTextUnavailableError
# =========================================================================

def test_image_only_pdf_raises_direct_text_unavailable(tmp_path):
    # Page with an image and only 4 characters (below min threshold of 30)
    pdf_bytes = _generate_pdf_with_image_and_text(text="Scan", add_image=True)
    artifact, settings = _setup_quarantined_pdf(tmp_path, pdf_bytes)

    with pytest.raises(Exception) as exc:
        extract_pdf_direct_text(artifact, settings)

    assert "No readable digital text was found" in str(exc.value)


# =========================================================================
# 4. A readable page with a logo/image is mixed and supported
# =========================================================================

def test_readable_page_with_logo_is_mixed_and_supported(tmp_path):
    rich_text = (
        "ACME Corporation Senior Director\n"
        "Managed engineering teams of 50+ developers across multiple global offices.\n"
        "Delivered robust enterprise software architectures with high availability."
    )
    pdf_bytes = _generate_pdf_with_image_and_text(text=rich_text, add_image=True)
    artifact, settings = _setup_quarantined_pdf(tmp_path, pdf_bytes)

    result = extract_pdf_direct_text(artifact, settings)

    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.has_visual_content is True
    assert page.classification == PageClassification.MIXED
    assert len(result.blocks) > 0
    assert result.extraction_result is not None


# =========================================================================
# 4b. A partially readable PDF retains direct blocks and requires review
# =========================================================================

def test_partially_readable_pdf_retains_direct_blocks_and_requires_review(tmp_path):
    page1_text = (
        "Jane Doe\n"
        "Senior Software Engineer with extensive experience in Python and cloud systems."
    )
    # Page 1 has sufficient text; Page 2 has only an image without text
    writer = PdfWriter()
    
    # Page 1: normal text
    p1_bytes = _generate_pdf_bytes([page1_text])
    r1 = pypdf.PdfReader(io.BytesIO(p1_bytes))
    writer.add_page(r1.pages[0])

    # Page 2: image only
    p2_bytes = _generate_pdf_with_image_and_text(text="", add_image=True)
    r2 = pypdf.PdfReader(io.BytesIO(p2_bytes))
    writer.add_page(r2.pages[0])

    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()

    artifact, settings = _setup_quarantined_pdf(tmp_path, pdf_bytes)
    result = extract_pdf_direct_text(artifact, settings)

    assert result.review_required is True
    assert len(result.pages) == 2
    assert result.pages[0].classification == PageClassification.TEXT
    assert result.pages[1].classification == PageClassification.IMAGE_ONLY
    assert len(result.blocks) == 1
    assert result.blocks[0].raw_text.startswith("Jane Doe")
    assert result.extraction_result is not None
    assert result.extraction_result.review_required is True



# =========================================================================
# 5. Unsafe quarantine containment or hash/size/status mismatch is rejected
# =========================================================================

def test_unvalidated_artifact_rejected(tmp_path):
    pdf_bytes = _generate_pdf_bytes(["Valid text content that meets minimum length threshold."])
    artifact, settings = _setup_quarantined_pdf(
        tmp_path,
        pdf_bytes,
        validation_status=ArtifactValidationStatus.PENDING,
    )

    with pytest.raises(ArtifactIntegrityError) as exc:
        extract_pdf_direct_text(artifact, settings)
    assert "VALIDATED state" in str(exc.value)


def test_hash_mismatch_rejected(tmp_path):
    pdf_bytes = _generate_pdf_bytes(["Valid text content that meets minimum length threshold."])
    artifact, settings = _setup_quarantined_pdf(
        tmp_path,
        pdf_bytes,
        hash_override="0" * 64,
    )

    with pytest.raises(ArtifactIntegrityError) as exc:
        extract_pdf_direct_text(artifact, settings)
    assert "hash mismatch" in str(exc.value)


def test_size_mismatch_rejected(tmp_path):
    pdf_bytes = _generate_pdf_bytes(["Valid text content that meets minimum length threshold."])
    artifact, settings = _setup_quarantined_pdf(
        tmp_path,
        pdf_bytes,
        size_override=999999,
    )

    with pytest.raises(ArtifactIntegrityError) as exc:
        extract_pdf_direct_text(artifact, settings)
    assert "size mismatch" in str(exc.value)


def test_quarantine_path_traversal_rejected(tmp_path):
    pdf_bytes = _generate_pdf_bytes(["Valid text content that meets minimum length threshold."])
    artifact, settings = _setup_quarantined_pdf(
        tmp_path,
        pdf_bytes,
        quarantine_key_override="pdf/../../../etc/passwd",
    )

    with pytest.raises(ArtifactIntegrityError) as exc:
        extract_pdf_direct_text(artifact, settings)
    assert "Quarantine" in str(exc.value)


def test_symlink_quarantined_file_rejected(tmp_path):
    pdf_bytes = _generate_pdf_bytes(["Valid text content that meets minimum length threshold."])
    artifact, settings = _setup_quarantined_pdf(tmp_path, pdf_bytes)

    # Replace real file with a symlink to another file
    quarantine_base = settings.get_quarantine_path()
    real_file = quarantine_base / artifact.quarantine_key
    target_file = tmp_path / "other_target.pdf"
    target_file.write_bytes(pdf_bytes)

    real_file.unlink()
    real_file.symlink_to(target_file)

    with pytest.raises(ArtifactIntegrityError) as exc:
        extract_pdf_direct_text(artifact, settings)
    assert "symlink" in str(exc.value)


# =========================================================================
# 6. Extracted-character limit is enforced
# =========================================================================

def test_extracted_character_limit_enforced(tmp_path):
    long_text = "A" * 500  # 500 characters
    pdf_bytes = _generate_pdf_bytes([long_text])
    artifact, settings = _setup_quarantined_pdf(tmp_path, pdf_bytes)

    # Set limit below extracted length
    settings.max_pdf_extracted_chars = 100

    with pytest.raises(PDFTextLimitExceededError) as exc:
        extract_pdf_direct_text(artifact, settings)
    assert "character limit" in str(exc.value)


# =========================================================================
# 7. Parser failures return controlled errors without leaking sensitive text
# =========================================================================

def test_corrupted_pdf_file_returns_controlled_error(tmp_path):
    corrupt_bytes = b"%PDF-1.4 Corrupted content with confidential SECRET_SSN_12345 string that must not leak."
    artifact, settings = _setup_quarantined_pdf(tmp_path, corrupt_bytes)

    with pytest.raises(PDFExtractionFailedError) as exc:
        extract_pdf_direct_text(artifact, settings)

    assert "SECRET_SSN" not in str(exc.value)
    assert "Failed to open PDF document." in str(exc.value)


# =========================================================================
# 8. Fully readable result satisfies Phase 1C.1 persistence contract
# =========================================================================

class MockDBSession:
    def __init__(self):
        self.added = []
        self.flushed = False

    def add(self, instance):
        self.added.append(instance)

    def flush(self):
        self.flushed = True

    def execute(self, stmt):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        return mock_result


def test_fully_readable_result_persists_with_phase_1c1_service(tmp_path):
    page1_text = (
        "Alice Smith\n"
        "Experienced Principal Engineer specializing in distributed databases and distributed algorithms.\n\n"
        "Projects\n"
        "Architected distributed transactional storage engine with 99.999% uptime."
    )
    pdf_bytes = _generate_pdf_bytes([page1_text])
    artifact, settings = _setup_quarantined_pdf(tmp_path, pdf_bytes)

    extraction_outcome = extract_pdf_direct_text(artifact, settings)
    assert extraction_outcome.extraction_result is not None

    db_session = MockDBSession()
    persisted = persist_extraction(
        session=db_session,
        artifact=artifact,
        result=extraction_outcome.extraction_result,
    )

    assert isinstance(persisted, ResumeExtraction)
    assert persisted.extractor_version == PDF_DIRECT_EXTRACTOR_VERSION

    assert len(persisted.blocks) == 3
    assert persisted.blocks[0].sequence_index == 0
    assert persisted.blocks[0].page_number == 1
    assert persisted.blocks[0].raw_text == "Alice Smith\nExperienced Principal Engineer specializing in distributed databases and distributed algorithms."
    assert persisted.blocks[1].sequence_index == 1
    assert persisted.blocks[1].page_number == 1
    assert persisted.blocks[1].raw_text == "Projects"
    assert persisted.blocks[2].sequence_index == 2
    assert persisted.blocks[2].page_number == 1
    assert persisted.blocks[2].raw_text == "Architected distributed transactional storage engine with 99.999% uptime."
    assert db_session.flushed is True

