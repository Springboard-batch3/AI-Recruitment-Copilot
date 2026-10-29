import re
from typing import List, Optional, Tuple

from app.schemas.employment import (
    DatePrecision,
    EmploymentWarning,
    EmploymentWarningCode,
    ExtractedDateInterval,
    NormalizedDate,
)

DATE_RULES_VERSION = "date-rules-v1.0"

_MONTH_NAMES = {
    "january": 1, "jan": 1, "jan.": 1,
    "february": 2, "feb": 2, "feb.": 2,
    "march": 3, "mar": 3, "mar.": 3,
    "april": 4, "apr": 4, "apr.": 4,
    "may": 5,
    "june": 6, "jun": 6, "jun.": 6,
    "july": 7, "jul": 7, "jul.": 7,
    "august": 8, "aug": 8, "aug.": 8,
    "september": 9, "sep": 9, "sep.": 9, "sept": 9, "sept.": 9,
    "october": 10, "oct": 10, "oct.": 10,
    "november": 11, "nov": 11, "nov.": 11,
    "december": 12, "dec": 12, "dec.": 12,
}

_CURRENT_KEYWORDS = {"present", "current", "now", "ongoing", "till date", "to date"}

# Single Date Regex Components
# 1. Month Name + Year: e.g. "January 2022", "Jan. 2022", "Sept 2020", "May 2019"
_MONTH_NAME_YEAR_RE = re.compile(
    r"\b(January|Jan\.?|February|Feb\.?|March|Mar\.?|April|Apr\.?|May|June|Jun\.?|July|Jul\.?|August|Aug\.?|September|Sept?\.?|October|Oct\.?|November|Nov\.?|December|Dec\.?)\s*,?\s*([12]\d{3})\b",
    re.IGNORECASE,
)

# 2. Numeric Month/Year: e.g. "01/2022", "1/2022", "2022-01", "2022/01"
_NUMERIC_MY_RE = re.compile(
    r"\b(?:(0?[1-9]|1[0-2])[\/\.-]([12]\d{3})|([12]\d{3})[\/\.-](0?[1-9]|1[0-2]))\b"
)

# 3. Year only: e.g. "2022"
_YEAR_ONLY_RE = re.compile(r"\b([12]\d{3})\b")

# 4. Present / Current:
_CURRENT_RE = re.compile(r"\b(Present|Current|Now|Ongoing|Till Date|To Date)\b", re.IGNORECASE)

# Range Separator: "-", "–", "—", "to", "until"
_RANGE_SEP_RE = re.compile(r"\s*(?:[-–—]|to|until)\s*", re.IGNORECASE)

# Ambiguous numeric full date: e.g. "01/02/2024" or "01-02-2024"
_AMBIGUOUS_NUMERIC_DATE_RE = re.compile(r"\b(0?[1-9]|[12]\d|3[01])[\/\.-](0?[1-9]|[12]\d|3[01])[\/\.-]([12]\d{3})\b")

# Master Date Range Regex
# Matches Date1 + Separator + Date2
_DATE_RANGE_RE = re.compile(
    r"\b((?:(?:Jan(?:uary|\.)?|Feb(?:ruary|\.)?|Mar(?:ch|\.)?|Apr(?:il|\.)?|May|Jun(?:e|\.)?|Jul(?:y|\.)?|Aug(?:ust|\.)?|Sep(?:t(?:ember|\.)?|\.)?|Oct(?:ober|\.)?|Nov(?:ember|\.)?|Dec(?:ember|\.)?)\s*,?\s*[12]\d{3}|(?:0?[1-9]|1[0-2])[\/\.-][12]\d{3}|[12]\d{3}[\/\.-](?:0?[1-9]|1[0-2])|[12]\d{3}))"
    r"\s*(?:[-–—]|to|until)\s*"
    r"((?:(?:Jan(?:uary|\.)?|Feb(?:ruary|\.)?|Mar(?:ch|\.)?|Apr(?:il|\.)?|May|Jun(?:e|\.)?|Jul(?:y|\.)?|Aug(?:ust|\.)?|Sep(?:t(?:ember|\.)?|\.)?|Oct(?:ober|\.)?|Nov(?:ember|\.)?|Dec(?:ember|\.)?)\s*,?\s*[12]\d{3}|(?:0?[1-9]|1[0-2])[\/\.-][12]\d{3}|[12]\d{3}[\/\.-](?:0?[1-9]|1[0-2])|[12]\d{3}|Present|Current|Now|Ongoing|Till Date|To Date))\b",
    re.IGNORECASE,
)


def parse_single_date(raw_date_str: str) -> Optional[NormalizedDate]:
    """
    Parses a single date string into a NormalizedDate.
    Preserves raw text and precision (MONTH or YEAR).
    Does NOT guess missing months for year-only dates.
    """
    cleaned = raw_date_str.strip()
    if not cleaned:
        return None

    if cleaned.lower() in _CURRENT_KEYWORDS:
        return NormalizedDate(
            raw_text=cleaned,
            year=None,
            month=None,
            precision=DatePrecision.UNKNOWN,
            is_current=True,
        )

    # Check for ambiguous 3-part numeric dates (e.g. 01/02/2024 or 01-02-2024) and reject
    if _AMBIGUOUS_NUMERIC_DATE_RE.search(cleaned):
        return None

    # 1. Month Name + Year
    m_name = _MONTH_NAME_YEAR_RE.search(cleaned)
    if m_name:
        month_name = m_name.group(1).lower().rstrip(".")
        month_num = _MONTH_NAMES.get(month_name) or _MONTH_NAMES.get(month_name + ".")
        if month_num:
            year_num = int(m_name.group(2))
            return NormalizedDate(
                raw_text=cleaned,
                year=year_num,
                month=month_num,
                precision=DatePrecision.MONTH,
                is_current=False,
            )

    # 2. Numeric Month / Year
    m_num = _NUMERIC_MY_RE.search(cleaned)
    if m_num:
        if m_num.group(1) and m_num.group(2):
            month_num = int(m_num.group(1))
            year_num = int(m_num.group(2))
        else:
            year_num = int(m_num.group(3))
            month_num = int(m_num.group(4))
        return NormalizedDate(
            raw_text=cleaned,
            year=year_num,
            month=month_num,
            precision=DatePrecision.MONTH,
            is_current=False,
        )

    # 3. Year only
    m_year = _YEAR_ONLY_RE.search(cleaned)
    if m_year:
        year_num = int(m_year.group(1))
        return NormalizedDate(
            raw_text=cleaned,
            year=year_num,
            month=None,
            precision=DatePrecision.YEAR,
            is_current=False,
        )

    return None


def calculate_interval_duration_months(
    start: NormalizedDate,
    end: NormalizedDate,
    as_of_date: Optional[NormalizedDate] = None,
) -> Optional[int]:
    """
    Calculates duration in months using standard formula:
    (endYear - startYear) * 12 + (endMonth - startMonth)

    Only calculated when:
    - start and end both have MONTH precision, OR
    - end is current/present AND an as_of_date with MONTH precision is provided.

    Returns None for year-only intervals or missing as_of_date.
    """
    if start.precision != DatePrecision.MONTH or start.year is None or start.month is None:
        return None

    if end.is_current:
        if as_of_date is None or as_of_date.precision != DatePrecision.MONTH or as_of_date.year is None or as_of_date.month is None:
            return None
        end_year = as_of_date.year
        end_month = as_of_date.month
    else:
        if end.precision != DatePrecision.MONTH or end.year is None or end.month is None:
            return None
        end_year = end.year
        end_month = end.month

    duration = (end_year - start.year) * 12 + (end_month - start.month)
    return duration if duration >= 0 else None


def extract_date_range_matches(text: str) -> List[Tuple[int, int, str, str, str]]:
    """
    Finds all candidate date ranges in a block of text.
    Returns: List of (char_start, char_end, full_match_str, raw_start, raw_end)
    """
    matches = []

    # Check for ambiguous numeric full dates first (e.g. 01/02/2024 - 03/04/2024)
    for m in _DATE_RANGE_RE.finditer(text):
        full_str = m.group(0)
        start_str = m.group(1).strip()
        end_str = m.group(2).strip()

        # Reject if either start or end contains an ambiguous 3-part numeric date (e.g. 01/02/2020)
        if _AMBIGUOUS_NUMERIC_DATE_RE.search(start_str) or _AMBIGUOUS_NUMERIC_DATE_RE.search(end_str):
            continue

        matches.append((m.start(), m.end(), full_str, start_str, end_str))

    return matches


def parse_as_of_date(as_of_date_str: Optional[str]) -> Optional[NormalizedDate]:
    """Parses an as_of_date string into a NormalizedDate."""
    if not as_of_date_str:
        return None
    return parse_single_date(as_of_date_str)


def calculate_total_experience_months(
    intervals: List[Tuple[int, int, int, int]]
) -> int:
    """
    Merges a list of month-level intervals [(start_year, start_month, end_year, end_month), ...]
    and returns total non-overlapping months.

    Interval format:
    start_val = start_year * 12 + (start_month - 1)
    end_val = end_year * 12 + (end_month - 1)
    Months in [start_val, end_val] are counted once.
    """
    if not intervals:
        return 0

    # Convert to continuous 1D month intervals [start_idx, end_idx]
    ranges: List[Tuple[int, int]] = []
    for s_yr, s_mo, e_yr, e_mo in intervals:
        s_idx = s_yr * 12 + (s_mo - 1)
        e_idx = e_yr * 12 + (e_mo - 1)
        if e_idx >= s_idx:
            ranges.append((s_idx, e_idx))

    if not ranges:
        return 0

    # Sort ranges by start_idx
    ranges.sort(key=lambda r: (r[0], r[1]))

    # Merge overlapping and adjacent intervals
    merged: List[Tuple[int, int]] = []
    curr_start, curr_end = ranges[0]

    for next_start, next_end in ranges[1:]:
        if next_start <= curr_end:  # Overlapping or contiguous
            curr_end = max(curr_end, next_end)
        else:
            merged.append((curr_start, curr_end))
            curr_start, curr_end = next_start, next_end
    merged.append((curr_start, curr_end))

    # Total duration is sum of (end - start) for each interval
    total_months = sum((end - start) for start, end in merged)
    return total_months
