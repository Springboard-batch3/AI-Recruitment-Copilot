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
from app.schemas.extraction import ExtractionBlockData
from app.schemas.section_and_identity import (
    DetectedSection,
    SectionDetectionResult,
    SectionType,
)
from app.schemas.skill_and_summary import (
    ExtractedSkillItem,
    ProfessionalHeadlineResult,
    ProfessionalSummaryResult,
    SkillAndSummaryExtractionResult,
    SkillResolutionStatus,
    SkillSummaryWarning,
    SkillSummaryWarningCode,
)
from app.services.section_detection import match_heading_candidate
from app.services.skill_catalog import (
    DEFAULT_SKILL_CATALOG,
    SKILL_CATALOG_VERSION,
    SkillCatalog,
    SkillDefinition,
    load_active_onet_catalog,
)

logger = logging.getLogger("ai_hiring.services.skill_summary_extraction")

SKILL_SUMMARY_PARSER_VERSION = "skill-summary-extract-v1.4"

# Scopes for negation and limited evidence handling
_NEGATION_LIMITATION_RE = re.compile(
    r"\b(?:limited\s+evidence|limited|did\s+not|not|no|without|lack\s+of|lacks)\b",
    re.IGNORECASE,
)

_PROFICIENCY_QUALIFIER_RE = re.compile(
    r"^(?:basic|fundamental|foundational|introductory|elementary|working\s+knowledge\s+of|familiar\s+with)\s+",
    re.IGNORECASE,
)


def _is_negated_or_limited(text: str, start_pos: int) -> bool:
    """
    Checks if a match at start_pos is preceded in the same clause/sentence by a negation
    or limitation marker (no, not, did not, without, limited, limited evidence, lack of, lacks).
    """
    sub = text[:start_pos]
    clause_delims = [r"\.", r";", r":", r"\n\s*\n", r"\bbut\b", r"\bhowever\b", r"\balthough\b"]
    last_delim_pos = 0
    for d in clause_delims:
        for m in re.finditer(d, sub, re.IGNORECASE):
            if m.end() > last_delim_pos:
                last_delim_pos = m.end()
    clause_prefix = text[last_delim_pos:start_pos]
    return bool(_NEGATION_LIMITATION_RE.search(clause_prefix))

# Contact markers to avoid treating emails, phones, URLs as skills
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9](?:[A-Za-z0-9._%+-]{0,62}[A-Za-z0-9])?@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,62}[A-Za-z0-9])?\.[A-Za-z]{2,10}\b"
)
_URL_RE = re.compile(
    r"\b(?:https?://|www\.|linkedin\.com|github\.com)[^\s<>{}\[\]|\"^`\\]+",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s.-]*)?(?:\(\d{1,4}\)[\s.-]*|\d{1,4}[\s.-]*)\d{2,4}[\s.-]+\d{3,9}\b"
)

# Common section headers / metadata terms to ignore in skills list
_SECTION_OR_META_TERMS = {
    "skills", "skill", "technical skills", "technical skill", "core skills", "key skills",
    "technologies", "tools", "technical toolkit", "toolkit",
    "languages", "language", "frameworks", "databases", "platforms", "libraries",
    "tools & technologies", "tools and technologies",
    "experience", "work experience", "education", "projects", "project",
    "selected projects", "selected project", "certifications", "certification",
    "certificates", "certificate", "summary", "profile", "contact", "email",
    "phone", "address", "portfolio", "github", "linkedin", "curriculum vitae", "resume", "page",
}


# Role / Headline disqualifiers and indicators
_HEADLINE_DISQUALIFIERS = [
    "@", "http", "www.", ".com", "phone", "email", "tel", "github.com", "linkedin.com",
]

_ROLE_INDICATORS = [
    "developer", "engineer", "architect", "lead", "specialist", "scientist",
    "consultant", "analyst", "administrator", "manager", "designer", "programmer",
]


def _compute_confidence_band(confidence: float) -> ConfidenceBand:
    if confidence >= 0.90:
        return ConfidenceBand.HIGH
    elif confidence >= 0.70:
        return ConfidenceBand.MEDIUM
    elif confidence > 0.0:
        return ConfidenceBand.LOW
    return ConfidenceBand.UNKNOWN


def _is_punctuation_char(c: str) -> bool:
    """Checks if character is a delimiter/boundary punctuation (space, comma, parens, brackets, etc.)"""
    return c in " \t\n\r,;:|/\\()[]{}<>!*•·-~\"'“”‘’"


def _is_valid_token_boundary(text: str, start: int, end: int, pattern: str) -> bool:
    """
    Ensures match is bounded correctly on both sides, being careful with technical terms
    like C++, C#, .NET, Node.js, etc.
    """
    # Left boundary check
    if start > 0:
        prev_char = text[start - 1]
        # If pattern starts with a dot (like .NET), previous char should not be alphanumeric
        if pattern.startswith("."):
            if prev_char.isalnum():
                return False
        else:
            if prev_char.isalnum():
                return False

    # Right boundary check
    if end < len(text):
        next_char = text[end]
        # If pattern ends with + (like C++) or # (like C#), allow boundary
        if pattern.endswith("+") or pattern.endswith("#"):
            if next_char.isalnum():
                return False
        else:
            # If pattern ends with alnum or dot, next char should not be alnum (or + / # to avoid Java matching inside Java++ or similar)
            if next_char.isalnum() or next_char in "+#":
                return False

    return True


def _extract_skills_from_text_prose(
    block: ExtractionBlockData,
    catalog: SkillCatalog,
    section_name: str,
    allow_ambiguous_short: bool = False,
) -> List[Tuple[SkillDefinition, bool, str, str, int, int, float]]:
    """
    Scans descriptive prose within a block for catalog skills.
    Returns: List of (SkillDefinition, is_alias, matched_canonical, raw_matched_str, start_off, end_off, confidence)
    """
    results: List[Tuple[SkillDefinition, bool, str, str, int, int, float]] = []
    text = block.normalized_text
    lower_text = text.lower()

    # Track matched character spans to avoid overlapping sub-matches (e.g. Java inside JavaScript)
    matched_spans: List[Tuple[int, int]] = []

    # Iterate through patterns ordered by descending length
    for pattern, defn, is_alias in catalog.get_all_canonical_patterns():
        if defn.is_ambiguous_short and not allow_ambiguous_short:
            # Skip short ambiguous terms (Go, R, C) in prose
            continue

        start_idx = 0
        while True:
            pos = lower_text.find(pattern, start_idx)
            if pos == -1:
                break

            end_pos = pos + len(pattern)
            start_idx = end_pos

            # Verify boundary
            if not _is_valid_token_boundary(text, pos, end_pos, pattern):
                continue

            # Check if this span overlaps with an already matched longer span
            overlaps = any(
                max(pos, m_start) < min(end_pos, m_end)
                for m_start, m_end in matched_spans
            )
            if overlaps:
                continue

            raw_matched = text[pos:end_pos]
            matched_spans.append((pos, end_pos))

            # Negation or limitation check: disqualify if governed by negation/limitation marker
            if _is_negated_or_limited(text, pos):
                continue

            # Determine confidence based on section and alias
            if section_name == SectionType.SKILLS.value:
                conf = 0.98 if not is_alias else 0.95
            elif section_name in (SectionType.EXPERIENCE.value, SectionType.PROJECTS.value):
                conf = 0.90 if not is_alias else 0.85
            elif section_name == SectionType.SUMMARY.value:
                conf = 0.85 if not is_alias else 0.80
            else:
                conf = 0.80 if not is_alias else 0.75

            results.append((defn, is_alias, defn.canonical_name, raw_matched, pos, end_pos, conf))

    # Sort matches by start position
    results.sort(key=lambda x: x[4])
    return results


def _split_skills_section_items(
    block: ExtractionBlockData,
) -> List[Tuple[str, int, int, bool]]:
    """
    Parses an explicit SKILLS block into delimited candidate items:
    splits on newlines, bullets, commas, semicolons, pipes.
    Returns: List of (item_text, start_offset, end_offset, is_negated: bool)
    """
    text = block.normalized_text
    candidates: List[Tuple[str, int, int, bool]] = []

    # First split into lines to respect line-level structure
    lines = text.split("\n")
    curr_line_start = 0

    for line in lines:
        line_raw_start = text.find(line, curr_line_start)
        if line_raw_start == -1:
            line_raw_start = curr_line_start
        curr_line_start = line_raw_start + len(line) + 1

        stripped_line = line.strip()
        if not stripped_line:
            continue

        # If line starts with a category label like "Languages: Python, Java", handle prefix
        colon_idx = line.find(":")
        working_line = line
        line_offset_adj = 0
        is_line_limited = False
        if colon_idx != -1 and colon_idx < 30:
            prefix = line[:colon_idx].strip().lower()
            if any(lim in prefix for lim in ["limited evidence", "limited", "lack", "no evidence", "not evidenced", "lacks", "without"]):
                is_line_limited = True
            working_line = line[colon_idx + 1:]
            line_offset_adj = colon_idx + 1

        # Tokenize line by delimiters: comma, semicolon, pipe, bullets (•, -, *, ·)
        item_regex = re.compile(r"[,;|•·*]|\s-\s|^\s*-\s*")
        parts: List[Tuple[int, int]] = []
        last_pos = 0

        for match in item_regex.finditer(working_line):
            start_delim, end_delim = match.span()
            if start_delim > last_pos:
                parts.append((last_pos, start_delim))
            last_pos = end_delim

        if last_pos < len(working_line):
            parts.append((last_pos, len(working_line)))

        for p_start, p_end in parts:
            item_raw = working_line[p_start:p_end]
            item_trimmed = item_raw.strip(" \t\r\n•·*-")
            if not item_trimmed:
                continue

            # Calculate offsets relative to whole block text
            item_in_line_start = working_line.find(item_trimmed, p_start)
            abs_start = line_raw_start + line_offset_adj + item_in_line_start
            abs_end = abs_start + len(item_trimmed)

            is_item_negated = (
                is_line_limited
                or _is_negated_or_limited(working_line, p_start)
                or bool(_NEGATION_LIMITATION_RE.search(item_trimmed))
            )

            candidates.append((item_trimmed, abs_start, abs_end, is_item_negated))

    return candidates


def _is_unresolved_candidate_defensible(item_text: str) -> bool:
    """
    Validates whether an unknown item from a SKILLS section is a defensible candidate.
    - Not empty, between 2 and 40 characters
    - Not a sentence (<= 4 words, no terminal period with multiple words)
    - Not contact info (email, url, phone)
    - Not a section heading or meta term
    - Not purely numeric
    """
    cleaned = item_text.strip()
    if len(cleaned) < 2 or len(cleaned) > 40:
        return False

    words = cleaned.split()
    if len(words) > 4:
        return False

    # Check for contact info
    if _EMAIL_RE.search(cleaned) or _URL_RE.search(cleaned) or _PHONE_RE.search(cleaned):
        return False

    # Check for section or meta terms
    if cleaned.lower() in _SECTION_OR_META_TERMS:
        return False

    # Check for recognized section headings
    if match_heading_candidate(cleaned) is not None:
        return False

    # Pure numbers or years
    if re.fullmatch(r"\d+", cleaned):
        return False

    # Looks like a sentence (contains sentence punctuation with multiple words)
    if any(p in cleaned for p in ["?", "!"]) or (cleaned.endswith(".") and len(words) > 1 and not cleaned.endswith(".js")):
        return False

    return True


def _extract_headline(
    header_blocks: List[ExtractionBlockData],
    candidate_name: Optional[str] = None,
    artifact_id: str = "",
) -> Tuple[Optional[ProfessionalHeadlineResult], Optional[EvidenceSpan], List[SkillSummaryWarning]]:
    """
    Extracts a professional headline from HEADER blocks if a short, defensible title line exists.
    """
    warnings: List[SkillSummaryWarning] = []
    headline_candidates: List[Tuple[str, int, int, ExtractionBlockData]] = []

    for block in header_blocks:
        lines = block.normalized_text.split("\n")
        curr_offset = 0

        for line in lines:
            line_str = line.strip()
            line_len = len(line)
            line_start = block.normalized_text.find(line, curr_offset)
            if line_start == -1:
                line_start = curr_offset
            line_end = line_start + len(line_str)
            curr_offset = line_start + line_len + 1

            if not line_str:
                continue

            # Length guards: 2 to 6 words, 5 to 60 characters
            words = line_str.split()
            if len(words) < 2 or len(words) > 6 or len(line_str) < 5 or len(line_str) > 60:
                continue

            # Reject candidate name
            if candidate_name and line_str.lower() == candidate_name.lower():
                continue

            # Reject contacts / URLs / section headings
            if any(dis in line_str.lower() for dis in _HEADLINE_DISQUALIFIERS):
                continue
            if line_str.lower() in _SECTION_OR_META_TERMS:
                continue

            # Must contain a recognized role indicator word
            lower_words = [w.lower().strip(".,:;/-") for w in words]
            if any(ind in lower_words for ind in _ROLE_INDICATORS):
                headline_candidates.append((line_str, line_start, line_end, block))

    if not headline_candidates:
        warnings.append(
            SkillSummaryWarning(
                code=SkillSummaryWarningCode.HEADLINE_NOT_FOUND,
                field_name="headline",
                candidate_count=0,
            )
        )
        empty_field = ExtractedField(field_name="headline")
        return (
            ProfessionalHeadlineResult(
                headline_field=empty_field,
                raw_text=None,
                normalized_text=None,
                evidence_ids=[],
                confidence=None,
            ),
            None,
            warnings,
        )

    if len(headline_candidates) == 1:
        text, start_off, end_off, block = headline_candidates[0]
        ev_id = f"ev-hd-{artifact_id[:8]}-0001"
        span = EvidenceSpan(
            evidence_id=ev_id,
            resume_artifact_id=artifact_id,
            page_number=block.page_number,
            block_number=block.sequence_index,
            character_start=start_off,
            character_end=end_off,
            original_text=text,
            extraction_method=block.extraction_method.value,
        )
        field = ExtractedField(
            field_name="headline",
            value=text,
            normalized_value=text,
            confidence=0.90,
            evidence_ids=[ev_id],
            extraction_method=block.extraction_method.value,
            review_status=ReviewStatus.UNREVIEWED,
        )
        return (
            ProfessionalHeadlineResult(
                headline_field=field,
                raw_text=text,
                normalized_text=text,
                evidence_ids=[ev_id],
                confidence=0.90,
            ),
            span,
            warnings,
        )

    # Multiple distinct headline candidates -> Ambiguous
    warnings.append(
        SkillSummaryWarning(
            code=SkillSummaryWarningCode.HEADLINE_AMBIGUOUS,
            field_name="headline",
            candidate_count=len(headline_candidates),
        )
    )
    empty_field = ExtractedField(field_name="headline")
    return (
        ProfessionalHeadlineResult(
            headline_field=empty_field,
            raw_text=None,
            normalized_text=None,
            evidence_ids=[],
            confidence=None,
        ),
        None,
        warnings,
    )


def extract_skills_and_summary(
    blocks: List[ExtractionBlockData],
    sections_result: SectionDetectionResult,
    artifact_id: str,
    candidate_name: Optional[str] = None,
    catalog: Optional[SkillCatalog] = None,
) -> SkillAndSummaryExtractionResult:
    """
    Deterministically extracts skills, professional summary, and headline from Phase 1C blocks
    and Phase 1D.1 section detection result.
    """
    if catalog is None:
        catalog = load_active_onet_catalog()

    evidence_spans: List[EvidenceSpan] = []
    warnings: List[SkillSummaryWarning] = []
    ev_counter = 0

    def create_evidence_span(
        block: ExtractionBlockData,
        char_start: int,
        char_end: int,
        matched_text: str,
    ) -> EvidenceSpan:
        nonlocal ev_counter
        ev_counter += 1
        ev_id = f"ev-sk-{artifact_id[:8]}-{ev_counter:04d}"
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

    # Group blocks by detected section
    blocks_by_seq = {b.sequence_index: b for b in blocks}
    sections_by_type: Dict[SectionType, List[ExtractionBlockData]] = {}

    for sec in sections_result.sections:
        sec_blocks = [
            blocks_by_seq[seq]
            for seq in range(sec.start_sequence_index, sec.end_sequence_index + 1)
            if seq in blocks_by_seq
        ]
        sections_by_type.setdefault(sec.section_type, []).extend(sec_blocks)

    # -------------------------------------------------------------------------
    # 1. Extract Professional Summary
    # -------------------------------------------------------------------------
    summary_blocks = sections_by_type.get(SectionType.SUMMARY, [])
    summary_result: Optional[ProfessionalSummaryResult] = None

    if not summary_blocks:
        warnings.append(
            SkillSummaryWarning(
                code=SkillSummaryWarningCode.SUMMARY_NOT_FOUND,
                field_name="summary",
                candidate_count=0,
            )
        )
        empty_summary_field = ExtractedField(field_name="summary")
        summary_result = ProfessionalSummaryResult(
            summary_field=empty_summary_field,
            raw_text=None,
            normalized_text=None,
            evidence_ids=[],
            confidence=None,
        )
    else:
        # Check if summary is structurally ambiguous or unusually long (> 1500 chars)
        summary_parts = []
        raw_parts = []
        sum_evidence_ids = []

        for b in summary_blocks:
            norm_content = b.normalized_text.strip()
            # If the block starts with the section heading itself, strip only the heading line

            is_heading_block = b.block_type == BlockType.HEADING or (
                sections_result.sections
                and any(
                    s.heading and (
                        (s.heading.heading_block_id is not None and s.heading.heading_block_id == b.block_id)
                        or s.heading.heading_sequence_index == b.sequence_index
                    )
                    for s in sections_result.sections
                )
            )
            if is_heading_block:
                lines = norm_content.split("\n")
                if len(lines) > 1:
                    norm_content = "\n".join(lines[1:]).strip()
                else:
                    # Heading only block, skip content
                    continue


            if not norm_content:
                continue

            span = create_evidence_span(b, 0, len(b.normalized_text), b.normalized_text)
            evidence_spans.append(span)
            sum_evidence_ids.append(span.evidence_id)
            summary_parts.append(norm_content)
            raw_parts.append(b.raw_text.strip())

        combined_summary = "\n\n".join(summary_parts) if summary_parts else None
        combined_raw = "\n\n".join(raw_parts) if raw_parts else None

        if combined_summary:
            if len(combined_summary) > 1500:
                warnings.append(
                    SkillSummaryWarning(
                        code=SkillSummaryWarningCode.SUMMARY_STRUCTURALLY_AMBIGUOUS,
                        field_name="summary",
                        candidate_count=len(summary_blocks),
                    )
                )

            summary_field = ExtractedField(
                field_name="summary",
                value=combined_summary,
                normalized_value=combined_summary,
                confidence=0.95,
                evidence_ids=sum_evidence_ids,
                extraction_method=summary_blocks[0].extraction_method.value,
                review_status=ReviewStatus.UNREVIEWED,
            )
            summary_result = ProfessionalSummaryResult(
                summary_field=summary_field,
                raw_text=combined_raw,
                normalized_text=combined_summary,
                evidence_ids=sum_evidence_ids,
                confidence=0.95,
            )
        else:
            warnings.append(
                SkillSummaryWarning(
                    code=SkillSummaryWarningCode.SUMMARY_NOT_FOUND,
                    field_name="summary",
                    candidate_count=0,
                )
            )
            empty_summary_field = ExtractedField(field_name="summary")
            summary_result = ProfessionalSummaryResult(
                summary_field=empty_summary_field,
                raw_text=None,
                normalized_text=None,
                evidence_ids=[],
                confidence=None,
            )

    # -------------------------------------------------------------------------
    # 2. Extract Professional Headline
    # -------------------------------------------------------------------------
    header_blocks = sections_by_type.get(SectionType.HEADER, [])
    if not header_blocks and blocks:
        header_blocks = blocks[: min(2, len(blocks))]

    headline_result, hd_span, hd_warnings = _extract_headline(
        header_blocks,
        candidate_name=candidate_name,
        artifact_id=artifact_id,
    )
    if hd_span:
        evidence_spans.append(hd_span)
    warnings.extend(hd_warnings)

    # -------------------------------------------------------------------------
    # 3. Extract Skills Section-by-Section
    # -------------------------------------------------------------------------
    # Internal list of raw skill occurrences:
    # (canonical_name, raw_value, resolution_status, category, confidence, evidence_span, extraction_method, section_name)
    skill_occurrences: List[
        Tuple[str, str, SkillResolutionStatus, Optional[str], float, EvidenceSpan, str, str]
    ] = []

    # 3A. Explicit SKILLS Section
    skills_blocks = sections_by_type.get(SectionType.SKILLS, [])
    for block in skills_blocks:
        # Check if block has heading on first line
        items = _split_skills_section_items(block)
        for item_text, char_start, char_end, is_negated in items:
            if is_negated:
                continue

            # Skip if this is a section heading itself (e.g. "Technical Skills:")
            if item_text.lower().strip(":") in _SECTION_OR_META_TERMS:
                continue

            # Check for proficiency qualifier (e.g. "basic SQL")
            qualifier_m = _PROFICIENCY_QUALIFIER_RE.match(item_text)
            lookup_text = _PROFICIENCY_QUALIFIER_RE.sub("", item_text).strip() if qualifier_m else item_text

            # 1. Lookup in catalog
            lookup = catalog.lookup_exact_or_alias(lookup_text)
            if lookup:
                defn, is_alias, canon_name = lookup
                res_status = SkillResolutionStatus.RESOLVED_ALIAS if (is_alias or qualifier_m) else SkillResolutionStatus.RESOLVED_EXACT
                conf = 0.90 if qualifier_m else (0.98 if not is_alias else 0.95)
                span = create_evidence_span(block, char_start, char_end, item_text)
                evidence_spans.append(span)
                cat_val = defn.category.value if hasattr(defn.category, "value") else str(defn.category)
                skill_occurrences.append(
                    (
                        canon_name,
                        item_text,
                        res_status,
                        cat_val,
                        conf,
                        span,
                        block.extraction_method.value,
                        SectionType.SKILLS.value,
                    )
                )
            else:
                # 2. Check if item has multi-skill prose or sub-elements
                # Try prose extraction within the delimited item
                prose_matches = _extract_skills_from_text_prose(
                    ExtractionBlockData(
                        block_id=block.block_id,
                        sequence_index=block.sequence_index,
                        page_number=block.page_number,
                        structural_location=block.structural_location,
                        block_type=block.block_type,
                        raw_text=item_text,
                        normalized_text=item_text,
                        extraction_method=block.extraction_method,
                    ),
                    catalog,
                    SectionType.SKILLS.value,
                    allow_ambiguous_short=True,
                )
                if prose_matches:
                    for defn, is_alias, canon_name, raw_m, sub_start, sub_end, conf in prose_matches:
                        span = create_evidence_span(
                            block,
                            char_start + sub_start,
                            char_start + sub_end,
                            raw_m,
                        )
                        evidence_spans.append(span)
                        res_status = (
                            SkillResolutionStatus.RESOLVED_ALIAS
                            if is_alias
                            else SkillResolutionStatus.RESOLVED_EXACT
                        )
                        cat_val = defn.category.value if hasattr(defn.category, "value") else str(defn.category)
                        skill_occurrences.append(
                            (
                                canon_name,
                                raw_m,
                                res_status,
                                cat_val,
                                conf,
                                span,
                                block.extraction_method.value,
                                SectionType.SKILLS.value,
                            )
                        )
                else:
                    # 3. Retain defensible unknown items as UNRESOLVED
                    if _is_unresolved_candidate_defensible(item_text) and not _NEGATION_LIMITATION_RE.search(item_text):
                        span = create_evidence_span(block, char_start, char_end, item_text)
                        evidence_spans.append(span)
                        skill_occurrences.append(
                            (
                                item_text,  # Use raw string as canonical for unresolved
                                item_text,
                                SkillResolutionStatus.UNRESOLVED,
                                None,
                                0.95,
                                span,
                                block.extraction_method.value,
                                SectionType.SKILLS.value,
                            )
                        )
                        warnings.append(
                            SkillSummaryWarning(
                                code=SkillSummaryWarningCode.UNRESOLVED_SKILL_CANDIDATE,
                                field_name="skills",
                                candidate_count=1,
                                evidence_id=span.evidence_id,
                            )
                        )

    # 3B. Descriptive Sections (EXPERIENCE, PROJECTS, SUMMARY, EDUCATION)
    for sec_type in (SectionType.EXPERIENCE, SectionType.PROJECTS, SectionType.SUMMARY, SectionType.EDUCATION):
        sec_blocks = sections_by_type.get(sec_type, [])
        for block in sec_blocks:
            matches = _extract_skills_from_text_prose(
                block,
                catalog,
                sec_type.value,
                allow_ambiguous_short=False,  # Rejects Go, R, C in prose
            )
            for defn, is_alias, canon_name, raw_m, p_start, p_end, conf in matches:
                span = create_evidence_span(block, p_start, p_end, raw_m)
                evidence_spans.append(span)
                res_status = (
                    SkillResolutionStatus.RESOLVED_ALIAS
                    if is_alias
                    else SkillResolutionStatus.RESOLVED_EXACT
                )
                skill_occurrences.append(
                    (
                        canon_name,
                        raw_m,
                        res_status,
                        defn.category,
                        conf,
                        span,
                        block.extraction_method.value,
                        sec_type.value,
                    )
                )

    # -------------------------------------------------------------------------
    # 4. Deduplicate and Merge Skills
    # -------------------------------------------------------------------------
    # Key: canonical_name (case-insensitive for resolved, exact for unresolved)
    # Preservation of first appearance order
    merged_skills: List[ExtractedSkillItem] = []
    seen_skill_keys: Dict[str, ExtractedSkillItem] = {}

    for (
        canon_name,
        raw_val,
        res_status,
        category,
        conf,
        span,
        ext_method,
        sec_name,
    ) in skill_occurrences:
        dedup_key = (
            f"RESOLVED::{canon_name.lower()}"
            if res_status in (SkillResolutionStatus.RESOLVED_EXACT, SkillResolutionStatus.RESOLVED_ALIAS)
            else f"UNRESOLVED::{canon_name}"
        )

        if dedup_key in seen_skill_keys:
            existing = seen_skill_keys[dedup_key]
            # Merge raw values
            if raw_val not in existing.raw_values:
                existing.raw_values.append(raw_val)
            # Merge evidence IDs
            if span.evidence_id not in existing.evidence_ids:
                existing.evidence_ids.append(span.evidence_id)
            # Merge sections
            if sec_name not in existing.sections:
                existing.sections.append(sec_name)
            # Retain highest confidence
            if conf > existing.confidence:
                existing.confidence = conf
                existing.confidence_band = _compute_confidence_band(conf)
            # Resolution status upgrades from ALIAS to EXACT if exact appears
            if (
                existing.resolution_status == SkillResolutionStatus.RESOLVED_ALIAS
                and res_status == SkillResolutionStatus.RESOLVED_EXACT
            ):
                existing.resolution_status = SkillResolutionStatus.RESOLVED_EXACT
        else:
            new_item = ExtractedSkillItem(
                canonical_value=canon_name,
                raw_values=[raw_val],
                resolution_status=res_status,
                category=category,
                confidence=conf,
                confidence_band=_compute_confidence_band(conf),
                evidence_ids=[span.evidence_id],
                extraction_method=ext_method,
                review_required=(res_status == SkillResolutionStatus.UNRESOLVED),
                sections=[sec_name],
            )
            seen_skill_keys[dedup_key] = new_item
            merged_skills.append(new_item)

    return SkillAndSummaryExtractionResult(
        parser_version=SKILL_SUMMARY_PARSER_VERSION,
        catalog_version=catalog.version,
        skills=merged_skills,
        summary=summary_result,
        headline=headline_result,
        evidence_spans=evidence_spans,
        warnings=warnings,
    )
