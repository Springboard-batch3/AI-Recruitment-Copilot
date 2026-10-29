"""
Evaluation normalization helpers for formal parser accuracy measurement.
Follows deterministic, field-aware rules matching platform normalization policies.
"""
import re
from typing import Optional, Set


def normalize_scalar_text(val: Optional[str]) -> str:
    """Normalize whitespace and case for scalar text comparison."""
    if not val:
        return ""
    # Normalize unicode whitespace & strip
    cleaned = re.sub(r"\s+", " ", val).strip()
    return cleaned.lower()


def normalize_email(val: Optional[str]) -> str:
    """Case-insensitive email normalization."""
    if not val:
        return ""
    return val.strip().lower()


def normalize_phone(val: Optional[str]) -> str:
    """Extract digits and leading '+' to compare phone numbers deterministically."""
    if not val:
        return ""
    cleaned = re.sub(r"[^\d+]", "", val.strip())
    # Standardize leading international prefixes if any
    return cleaned


def normalize_url(val: Optional[str]) -> str:
    """Normalize URLs by stripping scheme, www, and trailing slashes."""
    if not val:
        return ""
    url = val.strip().lower()
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    url = url.rstrip("/")
    return url


def normalize_skill(val: Optional[str]) -> str:
    """Normalize skill name for canonical set comparison."""
    if not val:
        return ""
    # Normalize case and strip
    return re.sub(r"\s+", " ", val).strip().lower()


def normalize_date_string(val: Optional[str]) -> str:
    """
    Standardize date strings into comparable forms (e.g. 'YYYY-MM', 'YYYY', 'PRESENT').
    Matches date rules in app.services.date_extraction.
    """
    if not val:
        return ""
    norm = val.strip().upper()
    if norm in ("PRESENT", "CURRENT", "NOW", "TILL DATE"):
        return "PRESENT"

    # Match YYYY-MM
    m_ym = re.match(r"^(\d{4})-(\d{2})$", norm)
    if m_ym:
        return f"{m_ym.group(1)}-{m_ym.group(2)}"

    # Match YYYY
    m_y = re.match(r"^(\d{4})$", norm)
    if m_y:
        return m_y.group(1)

    # Textual month-year (e.g., 'June 2024' or '06/2024')
    # Use standard lookup
    month_names = {
        "january": "01", "jan": "01",
        "february": "02", "feb": "02",
        "march": "03", "mar": "03",
        "april": "04", "apr": "04",
        "may": "05",
        "june": "06", "jun": "06",
        "july": "07", "jul": "07",
        "august": "08", "aug": "08",
        "september": "09", "sep": "09", "sept": "09",
        "october": "10", "oct": "10",
        "november": "11", "nov": "11",
        "december": "12", "dec": "12",
    }
    parts = re.split(r"[\s,\-/]+", val.strip().lower())
    if len(parts) == 2:
        p1, p2 = parts
        if p1 in month_names and re.match(r"^\d{4}$", p2):
            return f"{p2}-{month_names[p1]}"
        if p2 in month_names and re.match(r"^\d{4}$", p1):
            return f"{p1}-{month_names[p2]}"
        if re.match(r"^\d{1,2}$", p1) and re.match(r"^\d{4}$", p2):
            return f"{p2}-{int(p1):02d}"

    return norm.lower()


def normalize_language_proficiency(val: Optional[str]) -> str:
    """Map language proficiency levels to standardized canonical levels."""
    if not val:
        return ""
    norm = val.strip().lower()
    if any(k in norm for k in ["native", "bilingual", "mother tongue"]):
        return "native"
    if any(k in norm for k in ["fluent", "full professional"]):
        return "fluent"
    if any(k in norm for k in ["professional", "working"]):
        return "professional"
    if any(k in norm for k in ["intermediate", "conversational", "limited"]):
        return "intermediate"
    if any(k in norm for k in ["elementary", "basic", "beginner"]):
        return "basic"
    return norm


def compare_scalar(expected: Optional[str], predicted: Optional[str]) -> bool:
    """Compare two scalar string fields with case & whitespace normalization."""
    if expected is None and predicted is None:
        return True
    if expected is None or predicted is None:
        return False
    return normalize_scalar_text(expected) == normalize_scalar_text(predicted)


def compare_dates(expected: Optional[str], predicted: Optional[str]) -> bool:
    """Compare expected and predicted dates with exact normalized representation."""
    if expected is None and predicted is None:
        return True
    if expected is None or predicted is None:
        return False
    return normalize_date_string(expected) == normalize_date_string(predicted)
