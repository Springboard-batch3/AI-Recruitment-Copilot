import uuid
from decimal import Decimal
import pytest

from app.models.extraction import BlockType, ExtractionMethod
from app.schemas.candidate import ConfidenceBand, ReviewStatus
from app.schemas.employment import (
    DatePrecision,
    EmploymentWarningCode,
)
from app.schemas.extraction import ExtractionBlockData
from app.schemas.section_and_identity import SectionType
from app.services.date_extraction import (
    DATE_RULES_VERSION,
    calculate_interval_duration_months,
    calculate_total_experience_months,
    parse_single_date,
)
from app.services.employment_extraction import (
    EMPLOYMENT_PARSER_VERSION,
    extract_employment_records,
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
# 1. Standard role, organization, date and description
# =========================================================================

def test_standard_employment_record():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, "Senior Software Engineer | Acme Corp\nJanuary 2022 - March 2024\n• Led distributed backend development in Python.\n• Optimized database query latency by 40%.")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_employment_records(blocks, sec_res, art_id)

    assert res.parser_version == EMPLOYMENT_PARSER_VERSION
    assert res.date_rules_version == DATE_RULES_VERSION
    assert len(res.records) == 1

    rec = res.records[0]
    assert rec.job_title.value == "Senior Software Engineer"
    assert rec.organization.value == "Acme Corp"
    assert rec.start_date.value == "January 2022"
    assert rec.start_date.normalized_value == "2022-01"
    assert rec.end_date.value == "March 2024"
    assert rec.end_date.normalized_value == "2024-03"
    assert rec.is_current is False
    assert rec.duration_months == 26  # (2024 - 2022) * 12 + (3 - 1) = 26
    assert len(rec.descriptions) == 2
    assert "Led distributed backend development in Python." in rec.descriptions[0].value
    assert len(rec.evidence_ids) >= 4


# =========================================================================
# 2. Multiple employment records
# =========================================================================

def test_multiple_employment_records():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Work Experience", BlockType.HEADING)
    b1 = _create_block(1, "Tech Lead | MegaCorp\nFeb 2022 - Present\n• Architecture and team leadership.")
    b2 = _create_block(2, "Software Engineer | StartupLabs\nJan 2020 - Jan 2022\n• Built backend microservices.")

    blocks = [b0, b1, b2]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_employment_records(blocks, sec_res, art_id, as_of_date="2024-02")

    assert len(res.records) == 2
    r0 = res.records[0]
    assert r0.job_title.value == "Tech Lead"
    assert r0.organization.value == "MegaCorp"
    assert r0.is_current is True
    assert r0.duration_months == 24  # Feb 2022 to Feb 2024 = 24 months

    r1 = res.records[1]
    assert r1.job_title.value == "Software Engineer"
    assert r1.organization.value == "StartupLabs"
    assert r1.duration_months == 24  # Jan 2020 to Jan 2022 = 24 months


# =========================================================================
# 3. Month-name and abbreviated-month ranges
# =========================================================================

def test_month_name_and_abbrev_ranges():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, "Data Scientist | AI Corp\nSept 2021 to Dec. 2023\n• Machine learning modeling.")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_employment_records(blocks, sec_res, art_id)

    assert len(res.records) == 1
    rec = res.records[0]
    assert rec.start_date.normalized_value == "2021-09"
    assert rec.end_date.normalized_value == "2023-12"
    assert rec.duration_months == 27  # (2023 - 2021) * 12 + (12 - 9) = 27


# =========================================================================
# 4. Year-only dates preserve YEAR precision
# =========================================================================

def test_year_only_dates_preserve_year_precision():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, "Software Consultant | Advisory LLC\n2020 - 2023\n• Cloud advisory.")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_employment_records(blocks, sec_res, art_id)

    rec = res.records[0]
    assert rec.start_date.normalized_value == "2020"
    assert rec.end_date.normalized_value == "2023"
    assert rec.date_interval.start_date.precision == DatePrecision.YEAR
    assert rec.date_interval.end_date.precision == DatePrecision.YEAR
    # Duration must be None (no invented months)
    assert rec.duration_months is None
    assert any(
        w.code == EmploymentWarningCode.EMPLOYMENT_DATE_PRECISION_INSUFFICIENT
        for w in rec.warnings
    )


# =========================================================================
# 5. Present/current role with explicit asOfDate
# =========================================================================

def test_current_role_with_explicit_as_of_date():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, "Lead Architect | Cloud Systems\nJune 2023 - Present\n• Cloud infrastructure.")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_employment_records(blocks, sec_res, art_id, as_of_date="December 2024")

    rec = res.records[0]
    assert rec.is_current is True
    assert rec.end_date.normalized_value == "Present"
    assert rec.duration_months == 18  # June 2023 to Dec 2024 = 18 months


# =========================================================================
# 6. Present role without asOfDate
# =========================================================================

def test_current_role_without_as_of_date():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, "Lead Architect | Cloud Systems\nJune 2023 - Present\n• Cloud infrastructure.")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_records = extract_employment_records(blocks, sec_res, art_id, as_of_date=None)

    rec = res.records[0]
    assert rec.is_current is True
    assert rec.duration_months is None
    assert any(
        w.code == EmploymentWarningCode.CURRENT_ROLE_REQUIRES_AS_OF_DATE
        for w in rec.warnings
    )


# =========================================================================
# 7. Reversed date range
# =========================================================================

def test_reversed_date_range():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, "Developer | Acme Inc\n2024 - 2020\n• Work description.")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_employment_records(blocks, sec_res, art_id)

    assert len(res.records) == 1
    rec = res.records[0]
    assert any(
        w.code == EmploymentWarningCode.EMPLOYMENT_DATE_REVERSED
        for w in rec.warnings
    )


# =========================================================================
# 8. Missing start or end date
# =========================================================================

def test_missing_date_range_emits_warning():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, "Software Engineer | Ghost Corp\n• Undated project work.")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_employment_records(blocks, sec_res, art_id)

    assert len(res.records) == 1
    rec = res.records[0]
    assert rec.job_title.value == "Software Engineer"
    assert rec.start_date.value is None
    assert rec.date_interval is None
    assert any(
        w.code == EmploymentWarningCode.EMPLOYMENT_DATE_MISSING
        for w in rec.warnings
    )


# =========================================================================
# 9. Ambiguous numeric date
# =========================================================================

def test_ambiguous_numeric_date_not_guessed():
    d = parse_single_date("01/02/2024")
    # Ambiguous 3-part day/month format is not parsed as standard month/year
    assert d is None


# =========================================================================
# 10. Multiple roles at one organization
# =========================================================================

def test_multiple_roles_at_one_organization():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, "Senior Engineer | TechCorp\nJan 2022 - Dec 2023\n• Led core team.")
    b2 = _create_block(2, "Junior Engineer | TechCorp\nJan 2020 - Dec 2021\n• Maintained microservices.")

    blocks = [b0, b1, b2]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_employment_records(blocks, sec_res, art_id)

    assert len(res.records) == 2
    assert res.records[0].job_title.value == "Senior Engineer"
    assert res.records[0].organization.value == "TechCorp"
    assert res.records[1].job_title.value == "Junior Engineer"
    assert res.records[1].organization.value == "TechCorp"


# =========================================================================
# 11. Role/organization ambiguity produces review warning
# =========================================================================

def test_role_organization_ambiguity_produces_warning():
    art_id = str(uuid.uuid4())
    # Both sides are ambiguous words without clear role or org signals
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, "AlphaBeta | GammaDelta\nJan 2021 - Jan 2023\n• Projects.")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_employment_records(blocks, sec_res, art_id)

    rec = res.records[0]
    assert rec.confidence == 0.75
    assert any(
        w.code == EmploymentWarningCode.EMPLOYMENT_HEADER_AMBIGUOUS
        for w in rec.warnings
    )


# =========================================================================
# 12. Description stops before the next record
# =========================================================================

def test_description_stops_before_next_record():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, "Role A | Org A\n2021 - 2022\n• Description A1\n• Description A2")
    b2 = _create_block(2, "Role B | Org B\n2022 - 2023\n• Description B1")

    blocks = [b0, b1, b2]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_employment_records(blocks, sec_res, art_id)

    assert len(res.records) == 2
    rec_a = res.records[0]
    rec_b = res.records[1]

    assert len(rec_a.descriptions) == 2
    assert all("Description A" in d.value for d in rec_a.descriptions)

    assert len(rec_b.descriptions) == 1
    assert "Description B1" in rec_b.descriptions[0].value


# =========================================================================
# 13. Education dates are not extracted as employment
# =========================================================================

def test_education_dates_are_not_extracted():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Education", BlockType.HEADING)
    b1 = _create_block(1, "B.S. Computer Science | University of Technology\n2018 - 2022\n• GPA 3.9")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_employment_records(blocks, sec_res, art_id)

    # Must produce zero employment records since section is EDUCATION
    assert len(res.records) == 0


# =========================================================================
# 14. Overlapping intervals are not double-counted
# =========================================================================

def test_overlapping_intervals_not_double_counted():
    # Job 1: Jan 2022 to Dec 2022 (12 months: 2022-01 to 2022-12)
    # Job 2: Jun 2022 to Jun 2023 (12 months: 2022-06 to 2023-06)
    # Combined span: Jan 2022 to Jun 2023 = 18 months (not 24 months)
    intervals = [
        (2022, 1, 2022, 12),
        (2022, 6, 2023, 6),
    ]
    total = calculate_total_experience_months(intervals)
    assert total == 17  # (2023 - 2022)*12 + (6 - 1) = 17 months duration from 2022-01 to 2023-06


# =========================================================================
# 15. Adjacent complete intervals merge consistently
# =========================================================================

def test_adjacent_complete_intervals_merge():
    # Job 1: Jan 2020 to Dec 2020 (11 months: 2020-01 to 2020-12)
    # Job 2: Dec 2020 to Dec 2021 (12 months: 2020-12 to 2021-12)
    # Total continuous interval: Jan 2020 to Dec 2021 = 23 months (or 24 depending on endpoints)
    intervals = [
        (2020, 1, 2020, 12),
        (2020, 12, 2021, 12),
    ]
    total = calculate_total_experience_months(intervals)
    assert total == 23  # (2021 - 2020)*12 + (12 - 1) = 23


# =========================================================================
# 16. Invalid or incomplete intervals are excluded from exact totals
# =========================================================================

def test_incomplete_intervals_excluded_from_totals():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, "Engineer | Company A\nJan 2022 - Dec 2022\n• Description")
    b2 = _create_block(2, "Consultant | Company B\n2020 - 2021\n• Year-only dates")

    blocks = [b0, b1, b2]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_employment_records(blocks, sec_res, art_id)

    # Only Company A has month-level precision (11 months)
    assert res.total_experience_months == 11


# =========================================================================
# 17. Exact evidence block IDs and offsets
# =========================================================================

def test_exact_evidence_block_ids_and_offsets():
    art_id = str(uuid.uuid4())
    raw_text = "Software Engineer | Acme Corp\nJanuary 2022 - March 2024\n• High scale backend engineering."
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, raw_text)

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_employment_records(blocks, sec_res, art_id)

    assert len(res.evidence_spans) >= 4
    for span in res.evidence_spans:
        assert span.resume_artifact_id == art_id
        assert span.block_number == 1
        assert span.character_start >= 0
        assert span.character_end <= len(raw_text)
        assert span.character_end > span.character_start
        assert raw_text[span.character_start : span.character_end] == span.original_text


# =========================================================================
# 18. Technical punctuation in descriptions remains unchanged
# =========================================================================

def test_technical_punctuation_in_descriptions_preserved():
    art_id = str(uuid.uuid4())
    desc = "Built services with C++, C#, .NET Core, Node.js and scikit-learn (v1.2)."
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, f"Senior Developer | TechCorp\nJan 2021 - Jan 2023\n• {desc}")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_employment_records(blocks, sec_res, art_id)

    rec = res.records[0]
    assert rec.descriptions[0].value == desc
    assert rec.descriptions[0].normalized_value == desc


# =========================================================================
# 19. Stable repeated output
# =========================================================================

def test_stable_repeated_output():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, "Lead Engineer | MetaCorp\nJan 2021 - Present\n• Systems architecture.")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)

    res1 = extract_employment_records(blocks, sec_res, art_id, as_of_date="2024-01")
    res2 = extract_employment_records(blocks, sec_res, art_id, as_of_date="2024-01")

    assert res1.model_dump() == res2.model_dump()


# =========================================================================
# 20. Logs/warnings do not expose fixture roles or organizations
# =========================================================================

def test_warnings_do_not_leak_roles_or_organizations():
    art_id = str(uuid.uuid4())
    secret_role = "SecretTopSecretOfficerXYZ"
    secret_org = "SecretClassifiedOrganizationABC"
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, f"{secret_role} | {secret_org}\n2024 - 2020\n• Work.")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_employment_records(blocks, sec_res, art_id)

    for warning in res.warnings:
        warn_dump = str(warning.model_dump())
        assert secret_role not in warn_dump
        assert secret_org not in warn_dump
