import uuid
from decimal import Decimal
import pytest

from app.models.extraction import BlockType, ExtractionMethod
from app.schemas.extraction import ExtractionBlockData
from app.schemas.section_and_identity import (
    DetectedSection,
    SectionDetectionResult,
    SectionType,
)
from app.services.section_detection import (
    SECTION_PARSER_VERSION,
    detect_sections_from_blocks,
    match_heading_candidate,
)


def _create_block(
    seq: int,
    text: str,
    block_type: BlockType = BlockType.PARAGRAPH,
    page_num: int = 1,
) -> ExtractionBlockData:
    return ExtractionBlockData(
        block_id=uuid.uuid4(),
        sequence_index=seq,
        page_number=page_num,
        structural_location=None,
        block_type=block_type,
        raw_text=text,
        normalized_text=text,
        extraction_method=ExtractionMethod.PDF_TEXT,
        confidence=Decimal("1.0"),
        review_required=False,
    )


# =========================================================================
# 1. Standard Headings Matching
# =========================================================================

def test_standard_headings_detection():
    headings = [
        ("Summary", SectionType.SUMMARY),
        ("SKILLS", SectionType.SKILLS),
        ("Experience", SectionType.EXPERIENCE),
        ("EDUCATION", SectionType.EDUCATION),
        ("Projects", SectionType.PROJECTS),
        ("Certifications", SectionType.CERTIFICATIONS),
        ("Languages", SectionType.LANGUAGES),
    ]

    for raw, expected_type in headings:
        res = match_heading_candidate(raw)
        assert res is not None
        sec_type, norm, conf = res
        assert sec_type == expected_type
        assert conf == 1.0


# =========================================================================
# 2. Heading Aliases Matching
# =========================================================================

def test_heading_aliases_detection():
    aliases = [
        ("Professional Summary", SectionType.SUMMARY),
        ("Career Objective", SectionType.SUMMARY),
        ("About Me", SectionType.SUMMARY),
        ("Technical Skills", SectionType.SKILLS),
        ("Core Competencies", SectionType.SKILLS),
        ("Tools and Technologies", SectionType.SKILLS),
        ("Work Experience", SectionType.EXPERIENCE),
        ("Employment History", SectionType.EXPERIENCE),
        ("Professional Background", SectionType.EXPERIENCE),
        ("Academic Background", SectionType.EDUCATION),
        ("Academic Qualifications", SectionType.EDUCATION),
        ("Personal Projects", SectionType.PROJECTS),
        ("Licenses and Certifications", SectionType.CERTIFICATIONS),
        ("Language Proficiency", SectionType.LANGUAGES),
    ]

    for raw, expected_type in aliases:
        res = match_heading_candidate(raw)
        assert res is not None
        assert res[0] == expected_type


# =========================================================================
# 3. Case Differences, Whitespace & Trailing Colons
# =========================================================================

def test_case_and_trailing_colons():
    variations = [
        ("  wOrK eXpErIeNcE:  ", SectionType.EXPERIENCE),
        ("TECHNICAL SKILLS :", SectionType.SKILLS),
        ("education:", SectionType.EDUCATION),
        ("   Certifications ::: ", SectionType.CERTIFICATIONS),
    ]

    for raw, expected_type in variations:
        res = match_heading_candidate(raw)
        assert res is not None
        assert res[0] == expected_type


# =========================================================================
# 4. Content before first section becomes HEADER
# =========================================================================

def test_content_before_first_heading_becomes_header():
    b0 = _create_block(0, "Jane Doe\nSenior Python Engineer\njane@example.com")
    b1 = _create_block(1, "Experience", BlockType.HEADING)
    b2 = _create_block(2, "Worked at Acme Corp as Lead Architect.")

    res = detect_sections_from_blocks([b0, b1, b2])

    assert len(res.sections) == 2

    # Section 0: HEADER
    assert res.sections[0].section_type == SectionType.HEADER
    assert res.sections[0].start_sequence_index == 0
    assert res.sections[0].end_sequence_index == 0
    assert res.sections[0].heading is None

    # Section 1: EXPERIENCE
    assert res.sections[1].section_type == SectionType.EXPERIENCE
    assert res.sections[1].start_sequence_index == 1
    assert res.sections[1].end_sequence_index == 2
    assert res.sections[1].heading is not None
    assert res.sections[1].heading.section_type == SectionType.EXPERIENCE


# =========================================================================
# 5. Unknown/unclassified blocks are retained inside their section
# =========================================================================

def test_unknown_blocks_retained():
    b0 = _create_block(0, "Jane Doe")
    b1 = _create_block(1, "Skills", BlockType.HEADING)
    b2 = _create_block(2, "Python, FastAPI, Docker", BlockType.PARAGRAPH)
    b3 = _create_block(3, "Additional Notes / Unstructured Block", BlockType.UNKNOWN)

    res = detect_sections_from_blocks([b0, b1, b2, b3])

    assert len(res.sections) == 2
    assert res.sections[1].section_type == SectionType.SKILLS
    assert res.sections[1].start_sequence_index == 1
    assert res.sections[1].end_sequence_index == 3
    assert len(res.sections[1].block_ids) == 3


# =========================================================================
# 6. Ordinary sentences containing "skills" or "experience" are NOT headings
# =========================================================================

def test_ordinary_sentences_not_recognized_as_headings():
    sentences = [
        "Possess strong interpersonal and technical skills in cloud architecture.",
        "Over 10 years of professional experience managing cross-functional teams.",
        "Completed comprehensive education at University of Engineering.",
        "Led multiple high-impact projects delivering 95% test coverage.",
    ]

    for sentence in sentences:
        assert match_heading_candidate(sentence) is None


# =========================================================================
# 7. Consecutive sections produce correct non-overlapping contiguous ranges
# =========================================================================

def test_consecutive_sections_non_overlapping_ranges():
    blocks = [
        _create_block(0, "Jane Doe"),
        _create_block(1, "Summary", BlockType.HEADING),
        _create_block(2, "Experienced developer."),
        _create_block(3, "Skills", BlockType.HEADING),
        _create_block(4, "Python, SQL"),
        _create_block(5, "Experience", BlockType.HEADING),
        _create_block(6, "Acme Corp 2020-2024"),
        _create_block(7, "Education", BlockType.HEADING),
        _create_block(8, "B.S. Computer Science"),
    ]

    res = detect_sections_from_blocks(blocks)

    assert len(res.sections) == 5
    assert [s.section_type for s in res.sections] == [
        SectionType.HEADER,
        SectionType.SUMMARY,
        SectionType.SKILLS,
        SectionType.EXPERIENCE,
        SectionType.EDUCATION,
    ]

    # Verify contiguous ranges
    for i in range(len(res.sections) - 1):
        assert res.sections[i].end_sequence_index + 1 == res.sections[i + 1].start_sequence_index

    assert res.sections[0].start_sequence_index == 0
    assert res.sections[-1].end_sequence_index == 8


# =========================================================================
# 8. Empty Input
# =========================================================================

def test_empty_blocks_input():
    res = detect_sections_from_blocks([])
    assert res.parser_version == SECTION_PARSER_VERSION
    assert res.sections == []
    assert res.warnings == []


# =========================================================================
# 9. Heading at the beginning of a mixed-content block
# =========================================================================

def test_heading_at_beginning_of_multiline_block():
    b0 = _create_block(0, "Jane Doe")
    b1 = _create_block(1, "Technical Skills:\nPython, TypeScript, PostgreSQL, Docker")
    b2 = _create_block(2, "Experience:\nSoftware Engineer at Startup (2022 - Present)")

    res = detect_sections_from_blocks([b0, b1, b2])

    assert len(res.sections) == 3
    assert res.sections[0].section_type == SectionType.HEADER
    assert res.sections[1].section_type == SectionType.SKILLS
    assert res.sections[1].heading.matched_heading_text == "Technical Skills:"
    assert res.sections[2].section_type == SectionType.EXPERIENCE
    assert res.sections[2].heading.matched_heading_text == "Experience:"


# =========================================================================
# 10. Deterministic & Stable Output
# =========================================================================

def test_determinism_and_stability():
    blocks = [
        _create_block(0, "Jane Doe"),
        _create_block(1, "Work Experience", BlockType.HEADING),
        _create_block(2, "Software Engineer"),
    ]

    res1 = detect_sections_from_blocks(blocks)
    res2 = detect_sections_from_blocks(blocks)

    assert res1.model_dump() == res2.model_dump()
    assert res1.parser_version == SECTION_PARSER_VERSION
