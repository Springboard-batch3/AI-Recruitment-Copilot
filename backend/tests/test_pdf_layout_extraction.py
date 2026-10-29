import hashlib
import io
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from pypdf import PdfWriter

from app.config import Settings, get_settings
from app.models.extraction import BlockType, ExtractionMethod
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.services.pdf_text_extraction import (
    PDF_DIRECT_EXTRACTOR_VERSION,
    PageLayoutType,
    _WordItem,
    _detect_page_column_layout,
    _lines_to_extraction_blocks,
    _words_to_visual_lines,
    extract_pdf_direct_text,
)
from app.services.profile_assembly import assemble_candidate_profile

TEST_PDF_PATH = Path("/Users/herilvyas/Downloads/resume_test_B_two_column.pdf")


def _setup_test_artifact(tmp_pdf_path: Path, settings: Settings) -> ResumeArtifact:
    art_id = uuid.uuid4()
    q_dir = settings.get_quarantine_path() / "pdf"
    q_dir.mkdir(parents=True, exist_ok=True)
    q_path = q_dir / f"{art_id}.pdf"
    data = tmp_pdf_path.read_bytes()
    q_path.write_bytes(data)

    sha256 = hashlib.sha256(data).hexdigest().lower()
    return ResumeArtifact(
        artifact_id=art_id,
        original_filename=tmp_pdf_path.name,
        detected_format=ArtifactFormat.pdf,
        quarantine_key=f"pdf/{art_id}.pdf",
        size_bytes=len(data),
        sha256_hash=sha256,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )


# =========================================================================
# 1-13 Synthetic Unit Tests for Layout Extraction Principles
# =========================================================================


def test_ordinary_single_column_reading_order():
    """1. Ordinary single-column reading order: top-to-bottom line sequencing."""
    words = [
        _WordItem(text="Jane", x0=50.0, x1=80.0, top=50.0, bottom=62.0, fontname="Helvetica-Bold", size=14.0),
        _WordItem(text="Doe", x0=85.0, x1=110.0, top=50.0, bottom=62.0, fontname="Helvetica-Bold", size=14.0),
        _WordItem(text="Engineer", x0=50.0, x1=100.0, top=70.0, bottom=82.0, fontname="Helvetica", size=10.0),
        _WordItem(text="Summary", x0=50.0, x1=110.0, top=90.0, bottom=102.0, fontname="Helvetica", size=10.0),
        _WordItem(text="Paragraph", x0=115.0, x1=170.0, top=90.0, bottom=102.0, fontname="Helvetica", size=10.0),
    ]
    layout_type, col_count, split_x = _detect_page_column_layout(words, page_width=612.0)
    assert layout_type == PageLayoutType.SINGLE_COLUMN
    assert col_count == 1
    assert split_x is None

    lines = _words_to_visual_lines(words)
    assert len(lines) == 3
    assert lines[0].text == "Jane Doe"
    assert lines[1].text == "Engineer"
    assert lines[2].text == "Summary Paragraph"


def test_two_clearly_separated_columns_and_gutter():
    """2 & 3. Two clearly separated columns: gutter detected, no block crosses gutter."""
    left_words = [
        _WordItem(text="SKILLS", x0=40.0, x1=90.0, top=100.0, bottom=112.0, fontname="Helvetica-Bold", size=12.0),
        _WordItem(text="Python", x0=40.0, x1=80.0, top=120.0, bottom=132.0, fontname="Helvetica", size=10.0),
        _WordItem(text="Docker", x0=40.0, x1=80.0, top=135.0, bottom=147.0, fontname="Helvetica", size=10.0),
        _WordItem(text="Linux", x0=40.0, x1=75.0, top=150.0, bottom=162.0, fontname="Helvetica", size=10.0),
        _WordItem(text="AWS", x0=40.0, x1=65.0, top=165.0, bottom=177.0, fontname="Helvetica", size=10.0),
    ]
    right_words = [
        _WordItem(text="EXPERIENCE", x0=260.0, x1=340.0, top=100.0, bottom=112.0, fontname="Helvetica-Bold", size=12.0),
        _WordItem(text="Staff", x0=260.0, x1=290.0, top=120.0, bottom=132.0, fontname="Helvetica", size=10.0),
        _WordItem(text="Engineer", x0=295.0, x1=345.0, top=120.0, bottom=132.0, fontname="Helvetica", size=10.0),
        _WordItem(text="Built", x0=260.0, x1=290.0, top=135.0, bottom=147.0, fontname="Helvetica", size=10.0),
        _WordItem(text="systems", x0=295.0, x1=340.0, top=135.0, bottom=147.0, fontname="Helvetica", size=10.0),
    ]
    all_words = left_words + right_words
    layout_type, col_count, split_x = _detect_page_column_layout(all_words, page_width=612.0)

    assert layout_type == PageLayoutType.TWO_COLUMN
    assert col_count == 2
    assert split_x is not None
    assert 90.0 < split_x < 260.0


def test_same_height_left_right_lines_remain_separate():
    """4. Same-height left and right lines must remain in separate visual lines and blocks."""
    left_words = [
        _WordItem(text="LeftLine", x0=50.0, x1=120.0, top=200.0, bottom=212.0, fontname="Helvetica", size=10.0),
        _WordItem(text="ItemA", x0=50.0, x1=90.0, top=220.0, bottom=232.0, fontname="Helvetica", size=10.0),
        _WordItem(text="ItemB", x0=50.0, x1=90.0, top=235.0, bottom=247.0, fontname="Helvetica", size=10.0),
        _WordItem(text="ItemC", x0=50.0, x1=90.0, top=250.0, bottom=262.0, fontname="Helvetica", size=10.0),
        _WordItem(text="ItemD", x0=50.0, x1=90.0, top=265.0, bottom=277.0, fontname="Helvetica", size=10.0),
    ]
    right_words = [
        _WordItem(text="RightLine", x0=300.0, x1=370.0, top=200.0, bottom=212.0, fontname="Helvetica", size=10.0),
        _WordItem(text="Item1", x0=300.0, x1=340.0, top=220.0, bottom=232.0, fontname="Helvetica", size=10.0),
        _WordItem(text="Item2", x0=300.0, x1=340.0, top=235.0, bottom=247.0, fontname="Helvetica", size=10.0),
        _WordItem(text="Item3", x0=300.0, x1=340.0, top=250.0, bottom=262.0, fontname="Helvetica", size=10.0),
        _WordItem(text="Item4", x0=300.0, x1=340.0, top=265.0, bottom=277.0, fontname="Helvetica", size=10.0),
    ]
    all_words = left_words + right_words
    layout_type, _, split_x = _detect_page_column_layout(all_words, 612.0)
    assert layout_type == PageLayoutType.TWO_COLUMN
    assert split_x is not None

    words_l = [w for w in all_words if w.x1 <= split_x]
    words_r = [w for w in all_words if w.x0 >= split_x]

    lines_l = _words_to_visual_lines(words_l)
    lines_r = _words_to_visual_lines(words_r)

    blocks_l, _ = _lines_to_extraction_blocks(lines_l, page_number=1, start_seq=0, column_index=0)
    blocks_r, _ = _lines_to_extraction_blocks(lines_r, page_number=1, start_seq=len(blocks_l), column_index=1)

    for b in blocks_l:
        assert "RightLine" not in b.raw_text
        assert "Item1" not in b.raw_text

    for b in blocks_r:
        assert "LeftLine" not in b.raw_text
        assert "ItemA" not in b.raw_text


def test_sparse_single_column_page_not_classified_as_two_column():
    """7. Sparse one-column page must not have false two-column classification."""
    words = [
        _WordItem(text="Header", x0=50.0, x1=100.0, top=50.0, bottom=62.0, fontname="Helvetica", size=10.0),
        _WordItem(text="One", x0=50.0, x1=80.0, top=100.0, bottom=112.0, fontname="Helvetica", size=10.0),
        _WordItem(text="Two", x0=50.0, x1=80.0, top=150.0, bottom=162.0, fontname="Helvetica", size=10.0),
    ]
    layout_type, col_count, split_x = _detect_page_column_layout(words, page_width=612.0)
    assert layout_type == PageLayoutType.SINGLE_COLUMN
    assert col_count == 1
    assert split_x is None


def test_ambiguous_overlapping_layout_detected():
    """8. Irregular/overlapping layout triggers UNCERTAIN_LAYOUT classification."""
    # Words distributed across 4 distinct narrow gaps creating > 2 gutters
    words = []
    for col_idx, x_offset in enumerate([50.0, 180.0, 310.0, 440.0]):
        for row in range(5):
            words.append(
                _WordItem(
                    text=f"Col{col_idx}Row{row}",
                    x0=x_offset,
                    x1=x_offset + 40.0,
                    top=100.0 + row * 20.0,
                    bottom=112.0 + row * 20.0,
                    fontname="Helvetica",
                    size=10.0,
                )
            )
    layout_type, col_count, _ = _detect_page_column_layout(words, page_width=612.0)
    assert layout_type == PageLayoutType.UNCERTAIN_LAYOUT


def test_deterministic_output_across_repeated_runs(tmp_path):
    """9. Output is deterministic across repeated runs with identical sequence indices."""
    if not TEST_PDF_PATH.exists():
        pytest.skip(f"Test fixture not found at {TEST_PDF_PATH}")

    settings = get_settings()
    artifact = _setup_test_artifact(TEST_PDF_PATH, settings)

    res1 = extract_pdf_direct_text(artifact, settings)
    res2 = extract_pdf_direct_text(artifact, settings)

    assert len(res1.blocks) == len(res2.blocks)
    for b1, b2 in zip(res1.blocks, res2.blocks):
        assert b1.sequence_index == b2.sequence_index
        assert b1.raw_text == b2.raw_text
        assert b1.normalized_text == b2.normalized_text
        assert b1.structural_location == b2.structural_location
        assert b1.block_type == b2.block_type


# =========================================================================
# Regression Test Using Real Two-Column PDF
# =========================================================================


def test_two_column_resume_extraction_and_profile_assembly():
    """Validates the exact two-column ReportLab PDF resume end-to-end."""
    if not TEST_PDF_PATH.exists():
        pytest.skip(f"Test fixture not found at {TEST_PDF_PATH}")

    settings = get_settings()
    artifact = _setup_test_artifact(TEST_PDF_PATH, settings)

    res = extract_pdf_direct_text(artifact, settings)

    # 1. Version check
    assert res.extractor_version == "pdf-layout-text-v1.1"

    # 2. Layout assessment
    assert len(res.pages) == 1
    page0 = res.pages[0]
    assert page0.layout_type == "TWO_COLUMN"
    assert page0.column_count == 2
    assert not page0.review_required

    # 3. Column isolation & no header merging
    block_texts = [b.raw_text for b in res.blocks]
    assert any("Rohan Iyer" == b.raw_text.strip() for b in res.blocks)
    assert any("Cloud Platform Engineer" == b.raw_text.strip() for b in res.blocks)
    assert any("CONTACT" in b.raw_text for b in res.blocks)
    assert any("rohan.iyer@example.com" in b.raw_text for b in res.blocks)

    # Confirm CONTACT and Rohan Iyer are in separate blocks
    rohan_blocks = [b for b in res.blocks if "Rohan Iyer" in b.raw_text]
    contact_blocks = [b for b in res.blocks if "rohan.iyer@example.com" in b.raw_text]
    assert rohan_blocks and contact_blocks
    assert rohan_blocks[0].sequence_index != contact_blocks[0].sequence_index

    # 4. Profile Assembly verification
    p_res = assemble_candidate_profile(res.blocks, str(artifact.artifact_id))
    ident = p_res.identity
    prof = p_res.professional_profile

    # Identity
    assert ident.full_name.value == "Rohan Iyer"
    assert len(ident.emails) == 1
    assert ident.emails[0].value == "rohan.iyer@example.com"
    assert len(ident.phone_numbers) == 1
    assert ident.phone_numbers[0].value == "+1 617-555-0129"
    assert ident.github.value == "github.com/rohan-iyer-example"

    # Headline & Summary
    assert prof.headline.value == "Cloud Platform Engineer"
    assert prof.summary.value is not None
    assert "reliable container orchestration" in prof.summary.value

    # Skills: all 9 skills present
    extracted_skill_names = [s.value for s in prof.skills]
    expected_skills = ["Go", "Python", "Flask", "AWS", "Kubernetes", "Docker", "Redis", "MongoDB", "Linux"]
    for sk in expected_skills:
        assert sk in extracted_skill_names

    # Employment: 2 records
    assert len(prof.employment) == 2
    emp1 = prof.employment[0]
    assert emp1.job_title.value == "Cloud Platform Engineer"
    assert emp1.organization.value == "Blue Harbor Systems"
    assert emp1.start_date.value == "February 2023"
    assert emp1.end_date.value == "Present"
    assert len(emp1.descriptions) >= 1

    emp2 = prof.employment[1]
    assert emp2.job_title.value == "Backend Engineer"
    assert emp2.organization.value == "Cedar Labs"
    assert emp2.start_date.value == "August 2020"
    assert emp2.end_date.value == "January 2023"
    assert len(emp2.descriptions) >= 1

    # Projects: 2 records
    assert len(prof.projects) == 2
    proj_names = [p.name.value for p in prof.projects]
    assert "Cluster Health Console" in proj_names
    assert "API Resilience Lab" in proj_names

    # Education: 1 record
    assert len(prof.education) == 1
    edu = prof.education[0]
    assert edu.institution.value == "Eastwood Institute of Engineering"
    assert edu.degree.value == "Bachelor of Engineering"
    assert edu.field_of_study.value == "Information Technology"

    # Certification: 1 record
    assert len(prof.certifications) == 1
    cert = prof.certifications[0]
    assert cert.name.value == "AWS Certified Solutions Architect - Associate"
    assert cert.issuer.value == "Amazon Web Services"
    assert cert.credential_id.value == "SAA-EXAMPLE-25"

    # Languages: 2 records
    assert len(prof.languages) == 2
    lang_map = {l.value: l.normalized_value for l in prof.languages}
    assert lang_map.get("English") == "FLUENT"
    assert lang_map.get("Tamil") == "NATIVE"

    # Footer exclusion
    assert not any("Fictional resume" in b for b in block_texts)
    assert not any("Test PDF-B" in b for b in block_texts)


def test_single_column_pdf_extraction(tmp_path):
    """Validates that single-column direct-text PDFs continue to extract accurately."""
    from tests.test_pdf_text_extraction import _generate_pdf_bytes

    pdf_bytes = _generate_pdf_bytes([
        "Jane Doe\nSenior Backend Engineer\njane.doe@example.com | +1 555-0100\n\nEXPERIENCE\nStaff Engineer at Acme Corp 2021 - Present\nBuilt scalable microservices in Python and Go."
    ])

    pdf_file = tmp_path / "single_column.pdf"
    pdf_file.write_bytes(pdf_bytes)

    settings = get_settings()
    artifact = _setup_test_artifact(pdf_file, settings)

    res = extract_pdf_direct_text(artifact, settings)
    assert res.extractor_version == "pdf-layout-text-v1.1"
    assert res.pages[0].layout_type == "SINGLE_COLUMN"
    assert res.pages[0].column_count == 1
    assert len(res.blocks) >= 1

    p_res = assemble_candidate_profile(res.blocks, str(artifact.artifact_id))
    assert p_res.identity.full_name.value == "Jane Doe"
    assert p_res.professional_profile.headline.value == "Senior Backend Engineer"
    assert len(p_res.professional_profile.employment) == 1


