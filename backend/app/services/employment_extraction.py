import logging
import re
from typing import Dict, List, Optional, Set, Tuple

from app.models.extraction import BlockType
from app.schemas.candidate import (
    ConfidenceBand,
    EvidenceSpan,
    ExtractedField,
    ReviewStatus,
)
from app.schemas.employment import (
    DatePrecision,
    EmploymentExtractionResult,
    EmploymentWarning,
    EmploymentWarningCode,
    ExtractedDateInterval,
    ExtractedEmploymentRecord,
    NormalizedDate,
)
from app.schemas.extraction import ExtractionBlockData
from app.schemas.section_and_identity import (
    DetectedSection,
    SectionDetectionResult,
    SectionType,
)
from app.services.date_extraction import (
    DATE_RULES_VERSION,
    calculate_interval_duration_months,
    calculate_total_experience_months,
    extract_date_range_matches,
    parse_as_of_date,
    parse_single_date,
)

logger = logging.getLogger("ai_hiring.services.employment_extraction")

EMPLOYMENT_PARSER_VERSION = "employment-extract-v1.4"

_ROLE_INDICATORS = {
    "engineer", "developer", "architect", "lead", "specialist", "scientist",
    "consultant", "analyst", "administrator", "manager", "designer", "programmer",
    "intern", "director", "officer", "vp", "president", "associate", "head", "founder", "co-founder",
}

_ORG_INDICATORS = {
    "inc", "inc.", "corp", "corp.", "corporation", "llc", "ltd", "ltd.",
    "technologies", "labs", "systems", "solutions", "software", "company",
    "group", "bank", "university", "institute", "hospital", "services",
}

_ACTION_VERBS = {
    "developed", "built", "created", "managed", "designed", "led", "architected",
    "engineered", "integrated", "maintained", "implemented", "containerized",
    "wrote", "provided", "performed", "analyzed", "assisted", "collaborated",
    "delivered", "supported", "automated", "configured", "deployed", "spearheaded",
    "orchestrated", "optimized", "monitored", "refactored", "troubleshot",
}

_TABLE_COLUMN_HEADER_TERMS = {
    "role", "title", "job title", "position", "designation",
    "organization", "employer", "company", "firm", "workplace",
    "role and organization", "role & organization", "position and organization",
    "dates", "date", "period", "duration", "timeline", "years", "timeframe",
    "responsibilities", "outcomes", "description", "details", "duties",
    "responsibilities and outcomes", "responsibilities & outcomes",
    "key responsibilities", "achievements", "summary",
}

_HEADER_DELIMITERS = [" | ", " – ", " — ", " at ", " for ", " @ ", " - ", "|", "—", "–"]
_HEADER_SPLIT_RE = re.compile(r"\s*(?:[|•·—–-]|\bat\b|\bfor\b|\b@\b)\s*", re.IGNORECASE)


def _is_table_header_text(text: str) -> bool:
    """Checks if text is a structural table column header row."""
    cleaned = text.strip().lower().strip(".,:;/-")
    if not cleaned:
        return False
    if cleaned in _TABLE_COLUMN_HEADER_TERMS:
        return True
    sub_parts = [p.strip() for p in re.split(r"[,|/]|(?:\band\b)|(?:\b&\b)", cleaned) if p.strip()]
    if sub_parts and all(p in _TABLE_COLUMN_HEADER_TERMS for p in sub_parts):
        return True
    return False


def _is_date_only_line(line_str: str, date_matches: list) -> bool:
    """Returns True if the line contains a date range and no role/org content."""
    if not date_matches:
        return False
    d_start, d_end, d_full, _, _ = date_matches[0]
    rem = line_str[:d_start].strip(" \t|,·•—–-()") + line_str[d_end:].strip(" \t|,·•—–-()")
    return len(rem) == 0


def _is_employment_header_candidate(line_str: str, date_matches: list) -> bool:
    """Determines if a line is a genuine Role / Organization header line."""
    clean = line_str.strip()
    if not clean or clean.startswith(("•", "*", "·", "1.", "2.", "3.")):
        return False

    if _is_table_header_text(clean):
        return False

    # Check if first word is an action verb
    first_word = clean.split()[0].lower().strip(".,:;/-") if clean.split() else ""
    if first_word in _ACTION_VERBS:
        return False

    # Disqualify date-only lines
    if _is_date_only_line(clean, date_matches):
        return False

    # Separated header: e.g. "Role | Org", "Role at Org", "Role - Org"
    has_separator = any(sep in clean for sep in ["|", " at ", " for ", " @ "]) or (
        any(sep in clean for sep in ["—", "–", " - "]) and not date_matches
    )

    words = [w.lower().strip(".,:;/-()") for w in clean.split()]
    has_role_word = any(w in _ROLE_INDICATORS for w in words)
    has_org_word = any(w in _ORG_INDICATORS for w in words)

    if has_separator and (has_role_word or has_org_word or len(clean) < 80):
        return True

    if has_role_word and len(clean) < 60:
        if clean.endswith("."):
            return False
        if clean[0].islower():
            return False
        if any(w in words for w in ["the", "a", "an", "to", "their", "our", "this", "these", "those"]):
            return False
        if len(words) > 6:
            return False
        return True

    return False


def _has_role_indicators(text: str) -> bool:
    words = [w.lower().strip(".,:;/-()") for w in text.split()]
    return any(w in _ROLE_INDICATORS for w in words)


def _is_valid_org_segment(text: str) -> bool:
    clean = text.strip()
    if not clean or len(clean) > 80:
        return False
    if extract_date_range_matches(clean) or parse_single_date(clean):
        return False
    if clean.startswith(("•", "*", "·", "- ")) or (clean.endswith(".") and len(clean.split()) > 7):
        return False
    if _is_table_header_text(clean):
        return False
    return True


def _classify_role_and_org(
    part_a: str, part_b: str
) -> Tuple[Optional[str], Optional[str], bool]:
    """
    Given two header parts (e.g. "Software Engineer" and "Acme Corp"),
    determines which is the role and which is the organization.
    Returns (role_str, org_str, is_ambiguous: bool).
    """
    a_clean = part_a.strip()
    b_clean = part_b.strip()

    a_lower_words = [w.lower().strip(".,:;/-") for w in a_clean.split()]
    b_lower_words = [w.lower().strip(".,:;/-") for w in b_clean.split()]

    a_has_role = any(w in _ROLE_INDICATORS for w in a_lower_words)
    b_has_role = any(w in _ROLE_INDICATORS for w in b_lower_words)

    a_has_org = any(w in _ORG_INDICATORS for w in a_lower_words)
    b_has_org = any(w in _ORG_INDICATORS for w in b_lower_words)

    if a_has_role and not b_has_role:
        return a_clean, b_clean, False
    if b_has_role and not a_has_role:
        return b_clean, a_clean, False

    if a_has_org and not b_has_org:
        return b_clean, a_clean, False
    if b_has_org and not a_has_org:
        return a_clean, b_clean, False

    # Default convention: First part is Role, Second part is Organization (with ambiguity flag)
    return a_clean, b_clean, True


def _split_compound_role_org_line(
    line: str,
) -> Optional[Tuple[str, str, int, int, int, int, bool]]:
    """
    Evaluates compound lines with separators: |, -, –, —, at, for, @.
    Returns (role_val, org_val, role_start, role_end, org_start, org_end, is_ambiguous) or None.
    """
    clean_line = line.strip()
    for delim in _HEADER_DELIMITERS:
        if delim in clean_line:
            parts = clean_line.split(delim, 1)
            p0 = parts[0].strip()
            p1 = parts[1].strip()
            if not p0 or not p1:
                continue

            p0_has_role = _has_role_indicators(p0)
            p1_has_role = _has_role_indicators(p1)

            p0_valid_org = _is_valid_org_segment(p0)
            p1_valid_org = _is_valid_org_segment(p1)

            p0_start = line.find(p0)
            p0_end = p0_start + len(p0)
            p1_start = line.find(p1, p0_end)
            if p1_start == -1:
                p1_start = line.find(p1)
            p1_end = p1_start + len(p1)

            if p0_has_role and not p1_has_role and p1_valid_org:
                return p0, p1, p0_start, p0_end, p1_start, p1_end, False
            if p1_has_role and not p0_has_role and p0_valid_org:
                return p1, p0, p1_start, p1_end, p0_start, p0_end, False

            role_val, org_val, amb = _classify_role_and_org(p0, p1)
            if role_val == p0:
                return p0, p1, p0_start, p0_end, p1_start, p1_end, amb
            else:
                return p1, p0, p1_start, p1_end, p0_start, p0_end, amb

    return None


def extract_employment_records(
    blocks: List[ExtractionBlockData],
    sections_result: SectionDetectionResult,
    artifact_id: str,
    as_of_date: Optional[str] = None,
) -> EmploymentExtractionResult:
    """
    Deterministically extracts structured employment records from EXPERIENCE section blocks.
    - Preserves candidate wording without rewriting.
    - Accurately tracks evidence spans and character offsets.
    - Handles multiple records, overlapping jobs, current roles, and date intervals.
    """
    evidence_spans: List[EvidenceSpan] = []
    warnings: List[EmploymentWarning] = []
    ev_counter = 0

    parsed_as_of_date = parse_as_of_date(as_of_date) if as_of_date else None

    def make_span(
        block: ExtractionBlockData,
        start_off: int,
        end_off: int,
        matched_text: str,
    ) -> EvidenceSpan:
        nonlocal ev_counter
        ev_counter += 1
        ev_id = f"ev-emp-{artifact_id[:8]}-{ev_counter:04d}"
        return EvidenceSpan(
            evidence_id=ev_id,
            resume_artifact_id=artifact_id,
            page_number=block.page_number,
            block_number=block.sequence_index,
            character_start=start_off,
            character_end=end_off,
            original_text=matched_text,
            extraction_method=block.extraction_method.value,
        )

    # 1. Filter blocks belonging to EXPERIENCE section
    blocks_by_seq = {b.sequence_index: b for b in blocks}
    exp_blocks: List[ExtractionBlockData] = []

    for sec in sections_result.sections:
        if sec.section_type == SectionType.EXPERIENCE:
            for seq in range(sec.start_sequence_index, sec.end_sequence_index + 1):
                if seq in blocks_by_seq:
                    b = blocks_by_seq[seq]
                    if b.block_type == BlockType.FOOTER or (b.structural_location and "/footer/" in b.structural_location):
                        continue
                    # Skip section heading block if it contains only heading text
                    if sec.heading and sec.heading.heading_block_id == b.block_id and b.normalized_text.strip().lower() == sec.heading.matched_heading_text.strip().lower():
                        continue
                    exp_blocks.append(b)

    if not exp_blocks:
        return EmploymentExtractionResult(
            parser_version=EMPLOYMENT_PARSER_VERSION,
            date_rules_version=DATE_RULES_VERSION,
            as_of_date=as_of_date,
            records=[],
            total_experience_months=0,
            evidence_spans=[],
            warnings=[],
        )

    # 2. Identify candidate record anchors (date ranges inside blocks/lines)
    # Structure of line item: (block, line_text, line_start_off, line_end_off, date_range_matches)
    LineItem = Tuple[ExtractionBlockData, str, int, int, List[Tuple[int, int, str, str, str]]]
    all_lines: List[LineItem] = []

    for block in exp_blocks:
        text_to_split = block.normalized_text if block.normalized_text else block.raw_text
        lines = text_to_split.split("\n")
        curr_off = 0
        for line in lines:
            line_str = line.strip()
            line_len = len(line)
            line_start = text_to_split.find(line, curr_off)
            if line_start == -1:
                line_start = curr_off
            line_end = line_start + len(line_str)
            curr_off = line_start + line_len + 1

            if not line_str or _is_table_header_text(line_str):
                continue

            date_matches = extract_date_range_matches(line_str)
            all_lines.append((block, line_str, line_start, line_end, date_matches))

    # 3. Partition lines into employment record groups using State Machine
    record_groups: List[List[LineItem]] = []
    current_group: List[LineItem] = []

    for line_entry in all_lines:
        block, line_str, l_start, l_end, date_matches = line_entry

        is_header = _is_employment_header_candidate(line_str, date_matches)
        is_date_only = _is_date_only_line(line_str, date_matches)

        if is_header:
            # A new header always finalizes previous group and starts a new pending record
            if current_group:
                record_groups.append(current_group)
            current_group = [line_entry]
        elif date_matches and not is_date_only:
            # Line has both header content and dates (e.g. "Software Engineer | Google | 2020 - 2022")
            if current_group:
                record_groups.append(current_group)
            current_group = [line_entry]
        elif is_date_only:
            # Date-only line attaches to current pending record if it doesn't already have dates
            has_dates_already = any(len(item[4]) > 0 for item in current_group)
            if has_dates_already and current_group:
                record_groups.append(current_group)
                current_group = [line_entry]
            else:
                current_group.append(line_entry)
        else:
            # Description or bullet line attaches to pending record
            current_group.append(line_entry)

    if current_group:
        record_groups.append(current_group)

    # 4. Parse each record group into an ExtractedEmploymentRecord
    extracted_records: List[ExtractedEmploymentRecord] = []
    valid_month_intervals: List[Tuple[int, int, int, int]] = []

    for rec_idx, group in enumerate(record_groups):
        rec_warnings: List[EmploymentWarning] = []

        # Separate header lines (lines with role, org, dates) from description lines
        date_line_idx = -1
        for idx, item in enumerate(group):
            if item[4]:  # has date range match
                date_line_idx = idx
                break

        role_cand: Optional[str] = None
        org_cand: Optional[str] = None
        role_span_info: Optional[Tuple[ExtractionBlockData, int, int]] = None
        org_span_info: Optional[Tuple[ExtractionBlockData, int, int]] = None
        is_header_ambiguous = False

        desc_lines: List[LineItem] = []

        if date_line_idx != -1:
            date_block, date_line_str, d_l_start, d_l_end, date_matches = group[date_line_idx]
            d_start_char, d_end_char, d_full_str, raw_start_str, raw_end_str = date_matches[0]

            # Check if role / org are on the SAME line as date
            # e.g. "Software Engineer | Google | 2020 - 2022" or "Software Engineer at Google  Jan 2020 - Present"
            line_before_date = date_line_str[:d_start_char].strip(" \t|,·•—–")
            line_after_date = date_line_str[d_end_char:].strip(" \t|,·•—–")

            header_lines_before = group[:date_line_idx]
            desc_lines = group[date_line_idx + 1:]

            if line_before_date:
                split_res = _split_compound_role_org_line(line_before_date)
                if split_res:
                    r_val, o_val, r_st, r_en, o_st, o_en, amb = split_res
                    role_cand = r_val
                    org_cand = o_val
                    is_header_ambiguous = amb
                    role_span_info = (date_block, d_l_start + r_st, d_l_start + r_en)
                    org_span_info = (date_block, d_l_start + o_st, d_l_start + o_en)
                else:
                    if _has_role_indicators(line_before_date):
                        role_cand = line_before_date
                        r_off = date_block.normalized_text.find(line_before_date, d_l_start)
                        role_span_info = (date_block, r_off, r_off + len(line_before_date))
                    else:
                        org_cand = line_before_date
                        o_off = date_block.normalized_text.find(line_before_date, d_l_start)
                        org_span_info = (date_block, o_off, o_off + len(line_before_date))

            # If role or org still missing, look in lines preceding the date line
            if (not role_cand or not org_cand) and header_lines_before:
                if len(header_lines_before) == 1:
                    h_block, h_line, h_start, h_end, _ = header_lines_before[0]
                    split_res = _split_compound_role_org_line(h_line)
                    if split_res:
                        r_val, o_val, r_st, r_en, o_st, o_en, amb = split_res
                        role_cand = r_val
                        org_cand = o_val
                        is_header_ambiguous = amb
                        role_span_info = (h_block, h_start + r_st, h_start + r_en)
                        org_span_info = (h_block, h_start + o_st, h_start + o_en)
                    else:
                        if _has_role_indicators(h_line):
                            role_cand = h_line
                            role_span_info = (h_block, h_start, h_end)
                        else:
                            org_cand = h_line
                            org_span_info = (h_block, h_start, h_end)
                elif len(header_lines_before) >= 2:
                    h0_block, h0_line, h0_start, h0_end, _ = header_lines_before[0]
                    h1_block, h1_line, h1_start, h1_end, _ = header_lines_before[1]
                    role_cand, org_cand, is_header_ambiguous = _classify_role_and_org(h0_line, h1_line)
                    if role_cand == h0_line:
                        role_span_info = (h0_block, h0_start, h0_end)
                        org_span_info = (h1_block, h1_start, h1_end)
                    else:
                        role_span_info = (h1_block, h1_start, h1_end)
                        org_span_info = (h0_block, h0_start, h0_end)

            # If org or role still missing, check if the immediately following line in desc_lines is role/org
            if (not role_cand or not org_cand) and desc_lines:
                first_d_block, first_d_line, first_d_start, first_d_end, _ = desc_lines[0]
                d_str = first_d_line.strip()
                if not any(d_str.startswith(b) for b in ["•", "-", "*"]) and len(d_str.split()) <= 6 and not d_str.endswith("."):
                    if not org_cand and role_cand:
                        org_cand = d_str
                        org_span_info = (first_d_block, first_d_start, first_d_end)
                        desc_lines = desc_lines[1:]
                    elif not role_cand and org_cand:
                        role_cand = d_str
                        role_span_info = (first_d_block, first_d_start, first_d_end)
                        desc_lines = desc_lines[1:]

            # Date Range parsing & validation
            norm_start_date = parse_single_date(raw_start_str)
            norm_end_date = parse_single_date(raw_end_str)

            # Date Span
            abs_date_start = date_block.normalized_text.find(d_full_str, d_l_start)
            if abs_date_start == -1:
                abs_date_start = d_l_start + d_start_char
            abs_date_end = abs_date_start + len(d_full_str)

            date_span = make_span(date_block, abs_date_start, abs_date_end, d_full_str)
            evidence_spans.append(date_span)

            is_current_role = (norm_end_date is not None and norm_end_date.is_current)

            duration = None
            if norm_start_date and norm_end_date:
                # Check for reversed date
                if not is_current_role and norm_start_date.year and norm_end_date.year:
                    s_y = norm_start_date.year
                    e_y = norm_end_date.year
                    s_m = norm_start_date.month or 1
                    e_m = norm_end_date.month or 12
                    if (s_y > e_y) or (s_y == e_y and s_m > e_m):
                        rec_warnings.append(
                            EmploymentWarning(
                                code=EmploymentWarningCode.EMPLOYMENT_DATE_REVERSED,
                                record_index=rec_idx,
                                field_name="date_interval",
                                evidence_id=date_span.evidence_id,
                            )
                        )

                if is_current_role and parsed_as_of_date is None:
                    rec_warnings.append(
                        EmploymentWarning(
                            code=EmploymentWarningCode.CURRENT_ROLE_REQUIRES_AS_OF_DATE,
                            record_index=rec_idx,
                            field_name="duration_months",
                        )
                    )

                if (
                    norm_start_date.precision == DatePrecision.YEAR
                    or (not is_current_role and norm_end_date.precision == DatePrecision.YEAR)
                ):
                    rec_warnings.append(
                        EmploymentWarning(
                            code=EmploymentWarningCode.EMPLOYMENT_DATE_PRECISION_INSUFFICIENT,
                            record_index=rec_idx,
                            field_name="duration_months",
                        )
                    )

                duration = calculate_interval_duration_months(
                    norm_start_date, norm_end_date, parsed_as_of_date
                )

                if (
                    duration is not None
                    and norm_start_date.year is not None
                    and norm_start_date.month is not None
                ):
                    if is_current_role and parsed_as_of_date and parsed_as_of_date.year and parsed_as_of_date.month:
                        valid_month_intervals.append(
                            (norm_start_date.year, norm_start_date.month, parsed_as_of_date.year, parsed_as_of_date.month)
                        )
                    elif not is_current_role and norm_end_date.year and norm_end_date.month:
                        valid_month_intervals.append(
                            (norm_start_date.year, norm_start_date.month, norm_end_date.year, norm_end_date.month)
                        )

            interval_model = ExtractedDateInterval(
                raw_text=d_full_str,
                start_date=norm_start_date,
                end_date=norm_end_date,
                is_current=is_current_role,
                duration_months=duration,
                evidence_ids=[date_span.evidence_id],
                confidence=0.95,
            )

            # Start Date Field
            start_field = ExtractedField(
                field_name="start_date",
                value=raw_start_str,
                normalized_value=f"{norm_start_date.year:04d}-{norm_start_date.month:02d}" if (norm_start_date and norm_start_date.month and norm_start_date.year) else (str(norm_start_date.year) if (norm_start_date and norm_start_date.year) else raw_start_str),
                confidence=0.95,
                evidence_ids=[date_span.evidence_id],
                extraction_method=date_block.extraction_method.value,
                review_status=ReviewStatus.UNREVIEWED,
            )

            # End Date Field
            end_field = ExtractedField(
                field_name="end_date",
                value=raw_end_str,
                normalized_value="Present" if is_current_role else (f"{norm_end_date.year:04d}-{norm_end_date.month:02d}" if (norm_end_date and norm_end_date.month and norm_end_date.year) else (str(norm_end_date.year) if (norm_end_date and norm_end_date.year) else raw_end_str)),
                confidence=0.95,
                evidence_ids=[date_span.evidence_id],
                extraction_method=date_block.extraction_method.value,
                review_status=ReviewStatus.UNREVIEWED,
            )

        else:
            # Missing date range in this record group
            rec_warnings.append(
                EmploymentWarning(
                    code=EmploymentWarningCode.EMPLOYMENT_DATE_MISSING,
                    record_index=rec_idx,
                    field_name="date_interval",
                )
            )
            interval_model = None
            start_field = ExtractedField(field_name="start_date")
            end_field = ExtractedField(field_name="end_date")
            duration = None
            is_current_role = False

            # Try to extract role and org from the first line of the group
            if group:
                h0_block, h0_line, h0_start, h0_end, _ = group[0]
                split_res = _split_compound_role_org_line(h0_line)
                if split_res:
                    r_val, o_val, r_st, r_en, o_st, o_en, amb = split_res
                    role_cand = r_val
                    org_cand = o_val
                    is_header_ambiguous = amb
                    role_span_info = (h0_block, h0_start + r_st, h0_start + r_en)
                    org_span_info = (h0_block, h0_start + o_st, h0_start + o_en)
                    desc_lines = group[1:]
                else:
                    if _has_role_indicators(h0_line):
                        role_cand = h0_line
                        role_span_info = (h0_block, h0_start, h0_end)
                        desc_lines = group[1:]
                    else:
                        org_cand = h0_line
                        org_span_info = (h0_block, h0_start, h0_end)
                        desc_lines = group[1:]
            else:
                desc_lines = group

        # Build Role Field
        if role_cand and role_span_info:
            r_block, r_st, r_en = role_span_info
            r_span = make_span(r_block, r_st, r_en, role_cand)
            evidence_spans.append(r_span)
            role_field = ExtractedField(
                field_name="job_title",
                value=role_cand,
                normalized_value=role_cand,
                confidence=0.90 if not is_header_ambiguous else 0.75,
                evidence_ids=[r_span.evidence_id],
                extraction_method=r_block.extraction_method.value,
                review_status=ReviewStatus.UNREVIEWED,
            )
        else:
            rec_warnings.append(
                EmploymentWarning(
                    code=EmploymentWarningCode.EMPLOYMENT_ROLE_MISSING,
                    record_index=rec_idx,
                    field_name="job_title",
                )
            )
            role_field = ExtractedField(field_name="job_title")

        # Build Org Field
        if org_cand and org_span_info:
            o_block, o_st, o_en = org_span_info
            o_span = make_span(o_block, o_st, o_en, org_cand)
            evidence_spans.append(o_span)
            org_field = ExtractedField(
                field_name="organization",
                value=org_cand,
                normalized_value=org_cand,
                confidence=0.90 if not is_header_ambiguous else 0.75,
                evidence_ids=[o_span.evidence_id],
                extraction_method=o_block.extraction_method.value,
                review_status=ReviewStatus.UNREVIEWED,
            )
        else:
            rec_warnings.append(
                EmploymentWarning(
                    code=EmploymentWarningCode.EMPLOYMENT_ORGANIZATION_MISSING,
                    record_index=rec_idx,
                    field_name="organization",
                )
            )
            org_field = ExtractedField(field_name="organization")

        if is_header_ambiguous:
            rec_warnings.append(
                EmploymentWarning(
                    code=EmploymentWarningCode.EMPLOYMENT_HEADER_AMBIGUOUS,
                    record_index=rec_idx,
                    field_name="job_title",
                )
            )

        # Build Description Fields
        description_fields: List[ExtractedField] = []
        for d_block, d_line_str, d_st, d_en, _ in desc_lines:
            d_clean = d_line_str.strip(" \t•·*-")
            if not d_clean:
                continue
            item_st = d_block.normalized_text.find(d_clean, d_st)
            if item_st == -1:
                item_st = d_st
            item_en = item_st + len(d_clean)

            desc_span = make_span(d_block, item_st, item_en, d_clean)
            evidence_spans.append(desc_span)

            df = ExtractedField(
                field_name="description",
                value=d_clean,
                normalized_value=d_clean,
                confidence=0.92,
                evidence_ids=[desc_span.evidence_id],
                extraction_method=d_block.extraction_method.value,
                review_status=ReviewStatus.UNREVIEWED,
            )
            description_fields.append(df)

        # Collect evidence IDs for record
        rec_ev_ids = []
        if role_field.value:
            rec_ev_ids.extend(role_field.evidence_ids)
        if org_field.value:
            rec_ev_ids.extend(org_field.evidence_ids)
        if interval_model:
            rec_ev_ids.extend(interval_model.evidence_ids)
        for df in description_fields:
            rec_ev_ids.extend(df.evidence_ids)

        overall_conf = 0.90
        if is_header_ambiguous:
            overall_conf = 0.75
        elif not role_field.value or not org_field.value:
            overall_conf = 0.70

        rec = ExtractedEmploymentRecord(
            job_title=role_field,
            organization=org_field,
            start_date=start_field,
            end_date=end_field,
            descriptions=description_fields,
            date_interval=interval_model,
            duration_months=duration,
            is_current=is_current_role,
            confidence=overall_conf,
            evidence_ids=rec_ev_ids,
            warnings=rec_warnings,
        )
        extracted_records.append(rec)
        warnings.extend(rec_warnings)

    # 5. Calculate total experience months across non-overlapping valid intervals
    total_months = calculate_total_experience_months(valid_month_intervals) if valid_month_intervals else None

    return EmploymentExtractionResult(
        parser_version=EMPLOYMENT_PARSER_VERSION,
        date_rules_version=DATE_RULES_VERSION,
        as_of_date=as_of_date,
        records=extracted_records,
        total_experience_months=total_months,
        evidence_spans=evidence_spans,
        warnings=warnings,
    )
