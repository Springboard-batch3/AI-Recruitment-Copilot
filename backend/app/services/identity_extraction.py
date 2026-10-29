import logging
import re
import urllib.parse
from typing import Dict, List, Optional, Set, Tuple
from uuid import UUID

from app.models.extraction import BlockType
from app.schemas.candidate import CandidateIdentity, EvidenceSpan, ExtractedField, ReviewStatus
from app.schemas.extraction import ExtractionBlockData
from app.schemas.section_and_identity import (
    DetectedSection,
    IdentityExtractionResult,
    IdentityExtractionWarning,
    IdentityExtractionWarningCode,
    SectionDetectionResult,
    SectionType,
)

from app.services.section_detection import match_heading_candidate

logger = logging.getLogger("ai_hiring.services.identity_extraction")

IDENTITY_PARSER_VERSION = "identity-extract-v1.2"

# Bounded email pattern
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9](?:[A-Za-z0-9._%+-]{0,62}[A-Za-z0-9])?@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,62}[A-Za-z0-9])?\.[A-Za-z]{2,10}\b"
)

# International and standard phone pattern:
# Captures numbers with optional +, parentheses, spaces, dots, hyphens
_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s.-]*)?(?:\(\d{1,4}\)[\s.-]*|\d{1,4}[\s.-]*)\d{2,4}[\s.-]+\d{3,9}\b"
)

# URL pattern: explicit http/https or www. or domain formats like linkedin.com/in/... or github.com/...
_URL_RE = re.compile(
    r"\b(?:https?://|www\.|linkedin\.com/in/|github\.com/)[^\s<>{}\[\]|\"^`\\]+",
    re.IGNORECASE,
)

# Location patterns in header/contact region
_LOCATION_LABEL_RE = re.compile(
    r"\b(?:Location|Address|City)\s*[:\-–—]\s*([A-Za-z\s.-]+,\s*[A-Z]{2}(?:\s+\d{5})?|[A-Za-z\s.-]+,\s*[A-Za-z\s.-]+)",
    re.IGNORECASE,
)
_CITY_STATE_RE = re.compile(
    r"\b([A-Z][a-zA-Z\s.-]+,\s*[A-Z]{2})\b"
)

# Trailing punctuation to strip from URLs
_URL_TRAILING_PUNCT_RE = re.compile(r"[.,;:!?)\]]+$")

# Technical and role keyword blacklist that should never be considered a person's name
_NAME_BLACKLIST = {
    "c++", "c#", ".net", "python", "java", "javascript", "typescript", "react", "react.js",
    "node.js", "scikit-learn", "ci/cd", "rest", "api", "sql", "postgresql", "docker", "aws",
    "resume", "curriculum vitae", "cv", "page", "confidential", "profile", "summary",
    "experience", "education", "skills", "projects", "contact", "email", "phone", "address",
    "github", "linkedin", "portfolio", "full-stack", "frontend", "backend", "software engineer",
    "developer", "senior developer", "lead developer", "architect", "data scientist",
    "senior software engineer", "lead software engineer", "principal engineer", "engineering",
    "manager", "director", "consultant", "analyst", "specialist", "technical toolkit", "toolkit",
}

# Substrings that disqualify a line from being a person's name
_NAME_DISQUALIFYING_SUBSTRINGS = [
    "engineer", "developer", "architect", "manager", "director", "consultant",
    "analyst", "specialist", "administrator", "lead", "officer", "executive", "toolkit",
]


def _clean_trailing_url_punct(raw_url: str) -> Tuple[str, int]:
    """Strips trailing punctuation from a URL match and returns (cleaned_url, chars_stripped)."""
    match = _URL_TRAILING_PUNCT_RE.search(raw_url)
    if match:
        stripped_count = len(match.group(0))
        return raw_url[:-stripped_count], stripped_count
    return raw_url, 0


def _normalize_phone(raw_phone: str) -> str:
    """Normalizes phone number to standard digit format with optional leading +."""
    has_plus = raw_phone.strip().startswith("+")
    digits_only = re.sub(r"\D", "", raw_phone)
    if has_plus:
        return f"+{digits_only}"
    return digits_only


def _is_valid_phone_candidate(raw_phone: str) -> bool:
    """Validates phone number plausibility (between 7 and 15 digits, not a date or isolated year)."""
    digits = re.sub(r"\D", "", raw_phone)
    if len(digits) < 7 or len(digits) > 15:
        return False
    # Reject 4-digit years or ranges like 2020-2024
    if len(digits) == 4 or len(digits) == 8 and ("-" in raw_phone or "/" in raw_phone) and digits.startswith("20"):
        return False
    return True


def _classify_url(url: str) -> str:
    """Classifies URL as linkedin, github, or portfolio."""
    lower = url.lower()
    if "linkedin.com" in lower:
        return "linkedin"
    if "github.com" in lower:
        return "github"
    return "portfolio"


def _extract_name_candidates_from_blocks(
    header_blocks: List[ExtractionBlockData],
) -> List[Tuple[str, int, int, ExtractionBlockData]]:
    """
    Extracts plausible candidate names from header blocks.
    Returns list of (name_text, start_offset, end_offset, block).
    """
    candidates = []

    for block in header_blocks:
        lines = block.normalized_text.split("\n")
        curr_offset = 0

        for line in lines:
            line_str = line.strip()
            line_len = len(line)

            # Find actual start index of line within block normalized_text
            line_start = block.normalized_text.find(line, curr_offset)
            if line_start == -1:
                line_start = curr_offset
            line_end = line_start + len(line_str)
            curr_offset = line_start + line_len + 1

            if not line_str:
                continue

            # Length bounds: 2 to 4 words, between 3 and 50 characters
            words = line_str.split()
            if len(words) < 2 or len(words) > 4 or len(line_str) > 50 or len(line_str) < 3:
                continue

            # Check if line contains email, phone digits, or url markers
            if "@" in line_str or "http" in line_str.lower() or "www." in line_str.lower() or ".com" in line_str.lower():
                continue

            digit_count = sum(1 for c in line_str if c.isdigit())
            if digit_count > 0:
                continue

            # Check for blacklisted technical or heading words
            lower_line = line_str.lower()
            if any(sub in lower_line for sub in _NAME_DISQUALIFYING_SUBSTRINGS):
                continue

            # Reject detected section headings
            if match_heading_candidate(line_str) is not None:
                continue

            lower_words = [w.lower().strip(".,:;") for w in words]
            if any(w in _NAME_BLACKLIST for w in lower_words):
                continue

            # Check capitalization pattern: words should typically start with uppercase letter
            title_case_words = sum(1 for w in words if w[0].isupper() or len(w) == 1)
            if title_case_words < len(words) - 1:
                continue

            # Exclude punctuation-heavy lines or lines with comma/location pattern
            if "," in line_str or _CITY_STATE_RE.search(line_str):
                continue

            punct_count = sum(1 for c in line_str if c in ";:/|\\<>{}[]()!@#$%^&*")
            if punct_count > 0:
                continue

            candidates.append((line_str, line_start, line_end, block))

    return candidates


def extract_identity_and_contacts(
    blocks: List[ExtractionBlockData],
    sections_result: SectionDetectionResult,
    artifact_id: str,
) -> IdentityExtractionResult:
    """
    Deterministically extracts candidate identity and contact information from Phase 1C blocks.

    - Name: extracted primarily from HEADER section evidence.
    - Emails, Phone numbers, URLs: extracted across blocks with exact character offsets.
    - Every accepted field is linked to an EvidenceSpan.
    - Ambiguous or missing fields emit controlled warning codes without guessing.
    """
    evidence_spans: List[EvidenceSpan] = []
    warnings: List[IdentityExtractionWarning] = []
    ev_counter = 0

    def make_evidence_span(
        block: ExtractionBlockData,
        char_start: int,
        char_end: int,
        matched_text: str,
    ) -> EvidenceSpan:
        nonlocal ev_counter
        ev_counter += 1
        ev_id = f"ev-{artifact_id[:8]}-{ev_counter:04d}"
        return EvidenceSpan(
            evidence_id=ev_id,
            resume_artifact_id=artifact_id,
            page_number=block.page_number,
            block_number=block.sequence_index,
            character_start=char_start,
            character_end=char_end,
            original_text=matched_text,
            extraction_method=block.extraction_method.value,
        )

    # 1. Identify HEADER blocks for name extraction
    header_block_seqs: Set[int] = set()
    for sec in sections_result.sections:
        if sec.section_type == SectionType.HEADER:
            header_block_seqs.update(range(sec.start_sequence_index, sec.end_sequence_index + 1))

    # If no explicit header section, consider top 3 blocks
    if not header_block_seqs and blocks:
        header_block_seqs = {b.sequence_index for b in blocks[: min(3, len(blocks))]}

    header_blocks = [b for b in blocks if b.sequence_index in header_block_seqs]

    # 2. Extract Name
    name_candidates = _extract_name_candidates_from_blocks(header_blocks)
    full_name_field = ExtractedField(field_name="full_name")

    if not name_candidates:
        warnings.append(
            IdentityExtractionWarning(
                code=IdentityExtractionWarningCode.NAME_NOT_FOUND,
                field_name="full_name",
                candidate_count=0,
            )
        )
    elif len(name_candidates) == 1:
        name_text, start_off, end_off, block = name_candidates[0]
        span = make_evidence_span(block, start_off, end_off, name_text)
        evidence_spans.append(span)
        full_name_field = ExtractedField(
            field_name="full_name",
            value=name_text,
            normalized_value=name_text,
            confidence=0.95,
            evidence_ids=[span.evidence_id],
            extraction_method=block.extraction_method.value,
            review_status=ReviewStatus.UNREVIEWED,
        )
    else:
        # Multiple candidate lines -> Pick top line with confidence 0.75 and flag MULTIPLE_NAME_CANDIDATES warning
        warnings.append(
            IdentityExtractionWarning(
                code=IdentityExtractionWarningCode.MULTIPLE_NAME_CANDIDATES,
                field_name="full_name",
                candidate_count=len(name_candidates),
            )
        )
        name_text, start_off, end_off, block = name_candidates[0]
        span = make_evidence_span(block, start_off, end_off, name_text)
        evidence_spans.append(span)
        full_name_field = ExtractedField(
            field_name="full_name",
            value=name_text,
            normalized_value=name_text,
            confidence=0.75,
            evidence_ids=[span.evidence_id],
            extraction_method=block.extraction_method.value,
            review_status=ReviewStatus.UNREVIEWED,
        )

    # 3. Extract Emails across all blocks (or header preferentially)
    email_fields: List[ExtractedField] = []
    seen_emails: Set[str] = set()

    for block in blocks:
        for match in _EMAIL_RE.finditer(block.normalized_text):
            raw_email = match.group(0)
            norm_email = raw_email.lower().strip()
            if norm_email in seen_emails:
                continue
            seen_emails.add(norm_email)

            span = make_evidence_span(block, match.start(), match.end(), raw_email)
            evidence_spans.append(span)

            email_field = ExtractedField(
                field_name="email",
                value=raw_email,
                normalized_value=norm_email,
                confidence=0.98,
                evidence_ids=[span.evidence_id],
                extraction_method=block.extraction_method.value,
                review_status=ReviewStatus.UNREVIEWED,
            )
            email_fields.append(email_field)

    if not email_fields:
        warnings.append(
            IdentityExtractionWarning(
                code=IdentityExtractionWarningCode.EMAIL_NOT_FOUND,
                field_name="emails",
                candidate_count=0,
            )
        )
    elif len(email_fields) > 1:
        warnings.append(
            IdentityExtractionWarning(
                code=IdentityExtractionWarningCode.MULTIPLE_EMAIL_CANDIDATES,
                field_name="emails",
                candidate_count=len(email_fields),
            )
        )

    # 4. Extract Phone Numbers across header/early blocks
    phone_fields: List[ExtractedField] = []
    seen_phones: Set[str] = set()
    ambiguous_or_invalid_phone_found = False

    # Search in header blocks first, then fall back to all blocks
    phone_search_blocks = header_blocks if header_blocks else blocks[:5]

    for block in phone_search_blocks:
        # Check if block mentions phone/mobile explicitly
        has_phone_label = bool(re.search(r"\b(?:phone|tel|mobile|cell)\b", block.normalized_text, re.IGNORECASE))
        for match in _PHONE_RE.finditer(block.normalized_text):
            raw_phone = match.group(0).strip()
            if not _is_valid_phone_candidate(raw_phone):
                # Found a phone-like candidate pattern that failed validity checks
                ambiguous_or_invalid_phone_found = True
                continue

            norm_phone = _normalize_phone(raw_phone)
            if norm_phone in seen_phones:
                continue
            seen_phones.add(norm_phone)

            span = make_evidence_span(block, match.start(), match.end(), raw_phone)
            evidence_spans.append(span)

            phone_field = ExtractedField(
                field_name="phone_number",
                value=raw_phone,
                normalized_value=norm_phone,
                confidence=0.92,
                evidence_ids=[span.evidence_id],
                extraction_method=block.extraction_method.value,
                review_status=ReviewStatus.UNREVIEWED,
            )
            phone_fields.append(phone_field)

        if not phone_fields and has_phone_label:
            ambiguous_or_invalid_phone_found = True

    if not phone_fields:
        warnings.append(
            IdentityExtractionWarning(
                code=IdentityExtractionWarningCode.PHONE_NOT_FOUND,
                field_name="phone_numbers",
                candidate_count=0,
            )
        )
    elif len(phone_fields) > 1:
        warnings.append(
            IdentityExtractionWarning(
                code=IdentityExtractionWarningCode.MULTIPLE_PHONE_CANDIDATES,
                field_name="phone_numbers",
                candidate_count=len(phone_fields),
            )
        )

    # 5. Extract URLs (LinkedIn, GitHub, Portfolio)
    linkedin_field = ExtractedField(field_name="linkedin")
    github_field = ExtractedField(field_name="github")
    portfolio_field = ExtractedField(field_name="portfolio")

    for block in blocks:
        for match in _URL_RE.finditer(block.normalized_text):
            raw_url_full = match.group(0)
            cleaned_url, stripped = _clean_trailing_url_punct(raw_url_full)
            start_pos = match.start()
            end_pos = match.end() - stripped

            url_type = _classify_url(cleaned_url)
            span = make_evidence_span(block, start_pos, end_pos, cleaned_url)
            evidence_spans.append(span)

            field = ExtractedField(
                field_name=url_type,
                value=cleaned_url,
                normalized_value=cleaned_url if cleaned_url.startswith("http") else f"https://{cleaned_url}",
                confidence=0.95,
                evidence_ids=[span.evidence_id],
                extraction_method=block.extraction_method.value,
                review_status=ReviewStatus.UNREVIEWED,
            )

            if url_type == "linkedin" and linkedin_field.value is None:
                linkedin_field = field
            elif url_type == "github" and github_field.value is None:
                github_field = field
            elif url_type == "portfolio" and portfolio_field.value is None:
                portfolio_field = field

    # 6. Extract Location from bounded header/contact blocks
    location_field = ExtractedField(field_name="location")
    location_search_blocks = header_blocks if header_blocks else blocks[:3]
    for block in location_search_blocks:
        # Check for explicit Location label
        m_lbl = _LOCATION_LABEL_RE.search(block.normalized_text)
        if m_lbl:
            loc_val = m_lbl.group(1).strip(" \t\r\n,;|")
            start_off = block.normalized_text.find(loc_val)
            if start_off != -1:
                span = make_evidence_span(block, start_off, start_off + len(loc_val), loc_val)
                evidence_spans.append(span)
                location_field = ExtractedField(
                    field_name="location",
                    value=loc_val,
                    normalized_value=loc_val.upper(),
                    confidence=0.95,
                    evidence_ids=[span.evidence_id],
                    extraction_method=block.extraction_method.value,
                    review_status=ReviewStatus.UNREVIEWED,
                )
                break

        # Check for City, State pattern (e.g. Los Angeles, CA or Seattle, WA)
        m_city = _CITY_STATE_RE.search(block.normalized_text)
        if m_city:
            loc_val = m_city.group(1).strip(" \t\r\n,;|")
            if (not full_name_field.value or loc_val.lower() not in full_name_field.value.lower()) and len(loc_val) < 40:
                start_off = block.normalized_text.find(loc_val)
                if start_off != -1:
                    span = make_evidence_span(block, start_off, start_off + len(loc_val), loc_val)
                    evidence_spans.append(span)
                    location_field = ExtractedField(
                        field_name="location",
                        value=loc_val,
                        normalized_value=loc_val.upper(),
                        confidence=0.90,
                        evidence_ids=[span.evidence_id],
                        extraction_method=block.extraction_method.value,
                        review_status=ReviewStatus.UNREVIEWED,
                    )
                    break

    identity = CandidateIdentity(
        resume_artifact_id=artifact_id,
        full_name=full_name_field,
        emails=email_fields,
        phone_numbers=phone_fields,
        linkedin=linkedin_field,
        github=github_field,
        portfolio=portfolio_field,
        location=location_field,
    )

    return IdentityExtractionResult(
        parser_version=IDENTITY_PARSER_VERSION,
        identity=identity,
        evidence_spans=evidence_spans,
        warnings=warnings,
    )
