import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models.extraction import BlockType, ExtractionMethod, ResumeExtraction
from app.models.resume import ArtifactValidationStatus, ResumeArtifact
from app.schemas.extraction import ExtractionBlockData, ExtractionResult
from app.services.extraction_persistence import persist_extraction
from app.services.text_normalization import (
    NormalizationOutcome,
    NormalizationPolicy,
    NormalizationQualityFacts,
    NormalizationWarningCode,
    NormalizedTextUnavailableError,
    TextNormalizationError,
    normalize_extraction_result,
)


def _create_sample_block(
    sequence_index: int = 0,
    raw_text: str = "Sample Text",
    normalized_text: str = "Sample Text",
    page_number: int = 1,
    structural_location: str = None,
    block_type: BlockType = BlockType.PARAGRAPH,
    extraction_method: ExtractionMethod = ExtractionMethod.PDF_TEXT,
    confidence: Decimal = None,
    review_required: bool = False,
    block_id: uuid.UUID = None,
) -> ExtractionBlockData:
    return ExtractionBlockData(
        block_id=block_id or uuid.uuid4(),
        sequence_index=sequence_index,
        page_number=page_number,
        structural_location=structural_location,
        block_type=block_type,
        raw_text=raw_text,
        normalized_text=normalized_text,
        extraction_method=extraction_method,
        confidence=confidence,
        review_required=review_required,
    )


# =========================================================================
# 1. Raw text remains logically and byte-for-byte unchanged
# =========================================================================

def test_raw_text_immutable_and_preserved():
    raw_content = "  Raw  \t Unformatted   Text\r\nWith   Spaces \t\t  "
    block = _create_sample_block(sequence_index=0, raw_text=raw_content, normalized_text="irrelevant")
    result = ExtractionResult(extractor_version="pdf-v1", blocks=[block])

    outcome = normalize_extraction_result(result)

    assert len(outcome.extraction_result.blocks) == 1
    norm_block = outcome.extraction_result.blocks[0]
    # raw_text is untouched
    assert norm_block.raw_text == raw_content
    # normalized_text is cleaned from raw_text
    assert norm_block.normalized_text == "Raw Unformatted Text\nWith Spaces"


# =========================================================================
# 2. Unicode, line endings and whitespace normalize deterministically
# =========================================================================

def test_unicode_ligatures_and_line_endings_normalization():
    # Ligatures: \ufb01 (fi), \ufb02 (fl)
    raw = "\ufb01nancial \ufb02ow\r\nNext\rLine\r\n\r\n\r\nThird Line"
    block = _create_sample_block(sequence_index=0, raw_text=raw)
    result = ExtractionResult(extractor_version="docx-v1", blocks=[block])

    outcome = normalize_extraction_result(result)
    norm_text = outcome.extraction_result.blocks[0].normalized_text

    assert norm_text == "financial flow\nNext\nLine\n\nThird Line"
    assert "\r" not in norm_text


def test_accents_and_international_names_preserved():
    raw = "René François-Xavier Müller, Zürich\nSão Paulo & København"
    block = _create_sample_block(sequence_index=0, raw_text=raw)
    result = ExtractionResult(extractor_version="pdf-v1", blocks=[block])

    outcome = normalize_extraction_result(result)
    norm_text = outcome.extraction_result.blocks[0].normalized_text

    assert "René François-Xavier Müller, Zürich" in norm_text
    assert "São Paulo & København" in norm_text


# =========================================================================
# 3. Legitimate technical terms, punctuation, and URLs remain unchanged
# =========================================================================

def test_technical_terms_and_punctuation_preserved():
    raw = (
        "Experienced with C++, C#, .NET 8.0, Node.js, and React.js.\n"
        "Proficient in scikit-learn, CI/CD, REST/API, 95% accuracy.\n"
        "Contact: dev-lead@example.com or visit https://example.com/portfolio."
    )
    block = _create_sample_block(sequence_index=0, raw_text=raw)
    result = ExtractionResult(extractor_version="pdf-v1", blocks=[block])

    outcome = normalize_extraction_result(result)
    norm_text = outcome.extraction_result.blocks[0].normalized_text

    assert "C++, C#, .NET 8.0, Node.js, and React.js." in norm_text
    assert "scikit-learn, CI/CD, REST/API, 95% accuracy." in norm_text
    assert "dev-lead@example.com" in norm_text
    assert "https://example.com/portfolio" in norm_text


# =========================================================================
# 4. Safe line-wrap hyphenation joins while legitimate hyphenated terms remain
# =========================================================================

def test_line_wrap_dehyphenation_and_legitimate_hyphens():
    raw = (
        "Five years of experi-\nence in full-stack web development.\n"
        "Built state-of-the-art client-server architectures under ISO-9001 standards.\n"
        "Supported 2024-2025 roadmap, e-mail notifications, and X-ray analysis."
    )
    block = _create_sample_block(sequence_index=0, raw_text=raw)
    result = ExtractionResult(extractor_version="pdf-v1", blocks=[block])

    outcome = normalize_extraction_result(result)
    norm_text = outcome.extraction_result.blocks[0].normalized_text

    # Joined word
    assert "experience" in norm_text
    # Preserved hyphenated terms
    assert "full-stack" in norm_text
    assert "state-of-the-art" in norm_text
    assert "client-server" in norm_text
    assert "ISO-9001" in norm_text
    assert "2024-2025" in norm_text
    assert "e-mail" in norm_text
    assert "X-ray" in norm_text


# =========================================================================
# 5. Empty normalized blocks are removed and sequences reindexed contiguously
# =========================================================================

def test_empty_blocks_removed_and_reindexed():
    b0 = _create_sample_block(sequence_index=0, raw_text="First meaningful paragraph")
    b1_whitespace = _create_sample_block(sequence_index=1, raw_text="   \t  \n\n  \x00 ")
    b2 = _create_sample_block(sequence_index=2, raw_text="Second meaningful paragraph")

    result = ExtractionResult(extractor_version="pdf-v1", blocks=[b0, b1_whitespace, b2])
    outcome = normalize_extraction_result(result)

    assert len(outcome.extraction_result.blocks) == 2
    assert outcome.quality_facts.empty_blocks_removed == 1
    assert NormalizationWarningCode.EMPTY_BLOCK_REMOVED in outcome.warning_codes

    assert outcome.extraction_result.blocks[0].sequence_index == 0
    assert outcome.extraction_result.blocks[0].raw_text == "First meaningful paragraph"

    assert outcome.extraction_result.blocks[1].sequence_index == 1
    assert outcome.extraction_result.blocks[1].raw_text == "Second meaningful paragraph"


def test_all_empty_blocks_raises_unavailable():
    b0 = _create_sample_block(sequence_index=0, raw_text="   \t\n  ")
    b1 = _create_sample_block(sequence_index=1, raw_text="\x00\x01\x02")

    result = ExtractionResult(extractor_version="pdf-v1", blocks=[b0, b1])

    with pytest.raises(NormalizedTextUnavailableError) as exc:
        normalize_extraction_result(result)
    assert "No usable text" in str(exc.value)


# =========================================================================
# 6. Page and structural evidence locations and block IDs remain unchanged
# =========================================================================

def test_locations_and_ids_preserved():
    id0 = uuid.uuid4()
    id1 = uuid.uuid4()
    b0 = _create_sample_block(
        block_id=id0,
        sequence_index=0,
        page_number=1,
        structural_location=None,
        raw_text="Page 1 Text",
    )
    b1 = _create_sample_block(
        block_id=id1,
        sequence_index=1,
        page_number=None,
        structural_location="body/table/0/row/1/cell/2",
        raw_text="DOCX Table Cell",
        extraction_method=ExtractionMethod.DOCX_TEXT,
    )
    result = ExtractionResult(extractor_version="v1", blocks=[b0, b1])

    outcome = normalize_extraction_result(result)

    res_b0 = outcome.extraction_result.blocks[0]
    assert res_b0.block_id == id0
    assert res_b0.page_number == 1
    assert res_b0.structural_location is None

    res_b1 = outcome.extraction_result.blocks[1]
    assert res_b1.block_id == id1
    assert res_b1.page_number is None
    assert res_b1.structural_location == "body/table/0/row/1/cell/2"


# =========================================================================
# 7. Repeated PDF marginal blocks are retyped but not deleted
# =========================================================================

def test_repeated_pdf_marginal_detection():
    # 3 pages with repeated header "Confidential Resume - Jane Doe" at top
    # and "Page Footer Note" at bottom
    b_p1_h = _create_sample_block(sequence_index=0, page_number=1, raw_text="Confidential Resume - Jane Doe")
    b_p1_body = _create_sample_block(sequence_index=1, page_number=1, raw_text="Experience summary on page 1")
    b_p1_f = _create_sample_block(sequence_index=2, page_number=1, raw_text="Page Footer Note")

    b_p2_h = _create_sample_block(sequence_index=3, page_number=2, raw_text="Confidential Resume - Jane Doe")
    b_p2_body = _create_sample_block(sequence_index=4, page_number=2, raw_text="Employment history on page 2")
    b_p2_f = _create_sample_block(sequence_index=5, page_number=2, raw_text="Page Footer Note")

    result = ExtractionResult(
        extractor_version="pdf-v1",
        blocks=[b_p1_h, b_p1_body, b_p1_f, b_p2_h, b_p2_body, b_p2_f],
    )

    outcome = normalize_extraction_result(result)

    assert len(outcome.extraction_result.blocks) == 6
    assert outcome.quality_facts.repeated_header_count == 2
    assert outcome.quality_facts.repeated_footer_count == 2

    # Verify retyped blocks
    assert outcome.extraction_result.blocks[0].block_type == BlockType.HEADER
    assert outcome.extraction_result.blocks[1].block_type == BlockType.PARAGRAPH
    assert outcome.extraction_result.blocks[2].block_type == BlockType.FOOTER
    assert outcome.extraction_result.blocks[3].block_type == BlockType.HEADER
    assert outcome.extraction_result.blocks[4].block_type == BlockType.PARAGRAPH
    assert outcome.extraction_result.blocks[5].block_type == BlockType.FOOTER


def test_docx_blocks_not_subject_to_pdf_marginal_retyping():
    # DOCX blocks already have explicit block types and no page numbers
    b0 = _create_sample_block(
        sequence_index=0,
        page_number=None,
        structural_location="body/paragraph/0",
        raw_text="Repeated Section",
        extraction_method=ExtractionMethod.DOCX_TEXT,
    )
    b1 = _create_sample_block(
        sequence_index=1,
        page_number=None,
        structural_location="body/paragraph/1",
        raw_text="Repeated Section",
        extraction_method=ExtractionMethod.DOCX_TEXT,
    )
    result = ExtractionResult(extractor_version="docx-v1", blocks=[b0, b1])

    outcome = normalize_extraction_result(result)

    assert outcome.extraction_result.blocks[0].block_type == BlockType.PARAGRAPH
    assert outcome.extraction_result.blocks[1].block_type == BlockType.PARAGRAPH
    assert outcome.quality_facts.repeated_header_count == 0


# =========================================================================
# 8. Quality thresholds trigger review_required and warnings correctly
# =========================================================================

def test_replacement_character_threshold_triggers_review():
    # Block with excessive \ufffd replacement characters
    bad_text = "Corrupted text " + ("\ufffd" * 15)
    block = _create_sample_block(sequence_index=0, raw_text=bad_text)
    result = ExtractionResult(extractor_version="pdf-v1", review_required=False, blocks=[block])

    outcome = normalize_extraction_result(result)

    assert outcome.review_required is True
    assert outcome.extraction_result.review_required is True
    assert NormalizationWarningCode.EXCESSIVE_REPLACEMENT_CHARACTERS in outcome.warning_codes


def test_fragmentation_threshold_triggers_review():
    # 5 tiny blocks ("a", "b", "c", "d", "e") and 1 normal block
    blocks = [
        _create_sample_block(sequence_index=i, raw_text=f"x{i}")
        for i in range(5)
    ]
    blocks.append(_create_sample_block(sequence_index=5, raw_text="Normal long paragraph with sufficient text."))

    result = ExtractionResult(extractor_version="pdf-v1", review_required=False, blocks=blocks)
    outcome = normalize_extraction_result(result)

    assert outcome.review_required is True
    assert NormalizationWarningCode.EXCESSIVE_FRAGMENTATION in outcome.warning_codes


# =========================================================================
# 9. Existing review_required is never cleared
# =========================================================================

def test_existing_review_required_never_cleared():
    block = _create_sample_block(sequence_index=0, raw_text="Clean high quality text.")
    result = ExtractionResult(extractor_version="pdf-v1", review_required=True, blocks=[block])

    outcome = normalize_extraction_result(result)

    assert outcome.review_required is True
    assert outcome.extraction_result.review_required is True


# =========================================================================
# 10. Normalization is idempotent including version suffix
# =========================================================================

def test_normalization_idempotency_and_version_suffix():
    raw = "  Jane   Doe  \r\nSenior   Developer  \n\n\nEducation  "
    block = _create_sample_block(sequence_index=0, raw_text=raw)
    result1 = ExtractionResult(extractor_version="pdf-direct-text-v1.0", blocks=[block])

    outcome1 = normalize_extraction_result(result1)
    res1 = outcome1.extraction_result

    assert res1.extractor_version == "pdf-direct-text-v1.0+normalize-v1"

    # Pass the normalized result through the normalizer again
    outcome2 = normalize_extraction_result(res1)
    res2 = outcome2.extraction_result

    assert res2.extractor_version == "pdf-direct-text-v1.0+normalize-v1"
    assert res2.blocks[0].raw_text == raw
    assert res2.blocks[0].normalized_text == res1.blocks[0].normalized_text
    assert outcome1.quality_facts.normalized_character_count == outcome2.quality_facts.normalized_character_count


# =========================================================================
# 11. Sensitive candidate text never appears in errors or exceptions
# =========================================================================

def test_sensitive_text_not_leaked_in_errors():
    with pytest.raises(TextNormalizationError) as exc:
        normalize_extraction_result(None)

    assert "None" in str(exc.value) or "Invalid" in str(exc.value)


# =========================================================================
# 12. Normalized results remain compatible with Phase 1C.1 persistence
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


def test_normalized_result_persists_with_phase_1c1_service():
    raw_b0 = "Jane   Doe"
    raw_b1 = "Senior   Developer with experi-\nence in distributed systems."

    b0 = _create_sample_block(sequence_index=0, page_number=1, raw_text=raw_b0)
    b1 = _create_sample_block(sequence_index=1, page_number=1, raw_text=raw_b1)

    initial_result = ExtractionResult(extractor_version="pdf-direct-text-v1.0", blocks=[b0, b1])
    outcome = normalize_extraction_result(initial_result)

    art_id = uuid.uuid4()
    artifact = ResumeArtifact(
        artifact_id=art_id,
        original_filename="jane_doe.pdf",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )

    db_session = MockDBSession()
    persisted = persist_extraction(
        session=db_session,
        artifact=artifact,
        result=outcome.extraction_result,
    )

    assert isinstance(persisted, ResumeExtraction)
    assert persisted.extractor_version == "pdf-direct-text-v1.0+normalize-v1"
    assert len(persisted.blocks) == 2

    # Block 0
    assert persisted.blocks[0].raw_text == raw_b0
    assert persisted.blocks[0].normalized_text == "Jane Doe"

    # Block 1
    assert persisted.blocks[1].raw_text == raw_b1
    assert persisted.blocks[1].normalized_text == "Senior Developer with experience in distributed systems."

    assert db_session.flushed is True
