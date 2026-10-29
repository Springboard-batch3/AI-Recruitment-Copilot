import uuid
from decimal import Decimal
import pytest

from app.models.extraction import BlockType, ExtractionMethod
from app.schemas.extraction import ExtractionBlockData
from app.schemas.section_and_identity import (
    IdentityExtractionWarningCode,
    SectionType,
)
from app.services.identity_extraction import (
    IDENTITY_PARSER_VERSION,
    extract_identity_and_contacts,
)
from app.services.section_detection import detect_sections_from_blocks


def _create_block(
    seq: int,
    text: str,
    block_type: BlockType = BlockType.PARAGRAPH,
    page_num: int = 1,
    method: ExtractionMethod = ExtractionMethod.PDF_TEXT,
) -> ExtractionBlockData:
    return ExtractionBlockData(
        block_id=uuid.uuid4(),
        sequence_index=seq,
        page_number=page_num,
        structural_location=None,
        block_type=block_type,
        raw_text=text,
        normalized_text=text,
        extraction_method=method,
        confidence=Decimal("1.0"),
        review_required=False,
    )


# =========================================================================
# 1. Clean Extraction: Name, Email, Phone, URLs from Header Evidence
# =========================================================================

def test_clean_identity_extraction():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Jane Doe\nSenior Software Engineer")
    b1 = _create_block(1, "Email: jane.doe@example.com | Phone: +1 (555) 019-2834")
    b2 = _create_block(2, "LinkedIn: https://linkedin.com/in/janedoe | GitHub: https://github.com/janedoe | Portfolio: https://janedoe.example.com")
    b3 = _create_block(3, "Experience", BlockType.HEADING)
    b4 = _create_block(4, "Software Engineer at TechCorp (2020-2024)")

    blocks = [b0, b1, b2, b3, b4]
    sections_res = detect_sections_from_blocks(blocks)
    res = extract_identity_and_contacts(blocks, sections_res, art_id)

    assert res.parser_version == IDENTITY_PARSER_VERSION
    identity = res.identity

    # Full Name
    assert identity.full_name.value == "Jane Doe"
    assert identity.full_name.confidence == 0.95
    assert len(identity.full_name.evidence_ids) == 1

    # Email
    assert len(identity.emails) == 1
    assert identity.emails[0].value == "jane.doe@example.com"
    assert identity.emails[0].normalized_value == "jane.doe@example.com"

    # Phone
    assert len(identity.phone_numbers) == 1
    assert identity.phone_numbers[0].value == "+1 (555) 019-2834"
    assert identity.phone_numbers[0].normalized_value == "+15550192834"

    # URLs
    assert identity.linkedin.value == "https://linkedin.com/in/janedoe"
    assert identity.github.value == "https://github.com/janedoe"
    assert identity.portfolio.value == "https://janedoe.example.com"

    # Verify Evidence Spans (1 name + 1 email + 1 phone + 3 URLs = 6 spans)
    assert len(res.evidence_spans) == 6
    for span in res.evidence_spans:
        assert span.resume_artifact_id == art_id
        assert span.character_end > span.character_start
        assert len(span.original_text) > 0


# =========================================================================
# 2. Missing Contact Fields -> Controlled Warnings
# =========================================================================

def test_missing_contact_fields_emits_warnings():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Skills", BlockType.HEADING)
    b1 = _create_block(1, "Python, SQL, Docker")

    blocks = [b0, b1]
    sections_res = detect_sections_from_blocks(blocks)
    res = extract_identity_and_contacts(blocks, sections_res, art_id)

    warning_codes = [w.code for w in res.warnings]
    assert IdentityExtractionWarningCode.NAME_NOT_FOUND in warning_codes
    assert IdentityExtractionWarningCode.EMAIL_NOT_FOUND in warning_codes
    assert IdentityExtractionWarningCode.PHONE_NOT_FOUND in warning_codes

    assert res.identity.full_name.value is None
    assert res.identity.emails == []
    assert res.identity.phone_numbers == []


# =========================================================================
# 3. Multiple Email Candidates -> Handled & Flagged
# =========================================================================

def test_multiple_emails_extracted_and_warned():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Alex Smith\nPrimary: alex@example.com\nSecondary: work.alex@example.org")

    blocks = [b0]
    sections_res = detect_sections_from_blocks(blocks)
    res = extract_identity_and_contacts(blocks, sections_res, art_id)

    assert len(res.identity.emails) == 2
    assert res.identity.emails[0].value == "alex@example.com"
    assert res.identity.emails[1].value == "work.alex@example.org"

    warning_codes = [w.code for w in res.warnings]
    assert IdentityExtractionWarningCode.MULTIPLE_EMAIL_CANDIDATES in warning_codes


# =========================================================================
# 4. Multiple Name Candidates -> Handled with Controlled Warning
# =========================================================================

def test_multiple_name_candidates_warned():
    art_id = str(uuid.uuid4())
    # Two plausible name lines in header
    b0 = _create_block(0, "Jane Doe\nJohn Smith\nSoftware Engineer")

    blocks = [b0]
    sections_res = detect_sections_from_blocks(blocks)
    res = extract_identity_and_contacts(blocks, sections_res, art_id)

    assert res.identity.full_name.value == "Jane Doe"
    assert res.identity.full_name.confidence == 0.75

    warning_codes = [w.code for w in res.warnings]
    assert IdentityExtractionWarningCode.MULTIPLE_NAME_CANDIDATES in warning_codes


# =========================================================================
# 5. Technical Terms & Keywords are NEVER extracted as Name
# =========================================================================

def test_technical_terms_never_become_name():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "C++, C#, .NET, Python\nReact.js, Node.js, scikit-learn\nCI/CD, Docker, AWS")

    blocks = [b0]
    sections_res = detect_sections_from_blocks(blocks)
    res = extract_identity_and_contacts(blocks, sections_res, art_id)

    assert res.identity.full_name.value is None
    warning_codes = [w.code for w in res.warnings]
    assert IdentityExtractionWarningCode.NAME_NOT_FOUND in warning_codes


# =========================================================================
# 6. Phone Punctuation & International Formats
# =========================================================================

def test_phone_formats_and_normalization():
    art_id = str(uuid.uuid4())
    phones = [
        ("+44 20 7946 0991", "+442079460991"),
        ("(415) 555-2671", "4155552671"),
        ("1-800-555-0199", "18005550199"),
        ("+91 98765 43210", "+919876543210"),
    ]

    for raw, expected_norm in phones:
        b0 = _create_block(0, f"Candidate Name\nTel: {raw}")
        sections_res = detect_sections_from_blocks([b0])
        res = extract_identity_and_contacts([b0], sections_res, art_id)

        assert len(res.identity.phone_numbers) == 1
        assert res.identity.phone_numbers[0].value == raw
        assert res.identity.phone_numbers[0].normalized_value == expected_norm


# =========================================================================
# 7. Invalid Short or Long Digit Sequences are Rejected
# =========================================================================

def test_invalid_phone_digits_rejected():
    art_id = str(uuid.uuid4())
    # 4 digits (year), isolated 12345 (zip code), or 25-digit sequence
    b0 = _create_block(0, "Candidate Name\nZip: 94105, Graduated: 2024, Random: 12345678901234567890")

    blocks = [b0]
    sections_res = detect_sections_from_blocks(blocks)
    res = extract_identity_and_contacts(blocks, sections_res, art_id)

    assert res.identity.phone_numbers == []


# =========================================================================
# 8. LinkedIn and GitHub Classification
# =========================================================================

def test_linkedin_and_github_classification():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Jane Doe\nlinkedin.com/in/janedoe-dev\ngithub.com/janedoe\nhttps://myblog.example.com")

    blocks = [b0]
    sections_res = detect_sections_from_blocks(blocks)
    res = extract_identity_and_contacts(blocks, sections_res, art_id)

    assert "janedoe-dev" in res.identity.linkedin.value
    assert "github.com/janedoe" in res.identity.github.value
    assert "myblog.example.com" in res.identity.portfolio.value


# =========================================================================
# 9. URL Trailing Punctuation Stripping
# =========================================================================

def test_url_trailing_punctuation_stripped():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Contact at https://github.com/janedoe, or check https://linkedin.com/in/janedoe.")

    blocks = [b0]
    sections_res = detect_sections_from_blocks(blocks)
    res = extract_identity_and_contacts(blocks, sections_res, art_id)

    assert res.identity.github.value == "https://github.com/janedoe"
    assert res.identity.linkedin.value == "https://linkedin.com/in/janedoe"


# =========================================================================
# 10. Correct Block IDs and Exact Evidence Offsets
# =========================================================================

def test_exact_evidence_offsets_and_block_binding():
    art_id = str(uuid.uuid4())
    text_content = "Name: Jane Doe\nEmail: jane@example.com"
    b0 = _create_block(0, text_content)

    blocks = [b0]
    sections_res = detect_sections_from_blocks(blocks)
    res = extract_identity_and_contacts(blocks, sections_res, art_id)

    # Email span validation
    email_span = next(s for s in res.evidence_spans if "jane@example.com" in s.original_text)
    assert email_span.block_number == 0
    assert text_content[email_span.character_start : email_span.character_end] == "jane@example.com"


# =========================================================================
# 11. Privacy: Warnings & Errors do NOT contain Sensitive Contact Values
# =========================================================================

def test_warnings_do_not_leak_contact_values():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "First Person\nSecond Person\nThird Person")

    blocks = [b0]
    sections_res = detect_sections_from_blocks(blocks)
    res = extract_identity_and_contacts(blocks, sections_res, art_id)

    for warning in res.warnings:
        warning_str = str(warning.model_dump())
        assert "First Person" not in warning_str
        assert "Second Person" not in warning_str
        assert "Third Person" not in warning_str
