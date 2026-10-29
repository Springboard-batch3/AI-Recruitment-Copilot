import dataclasses
import enum
import logging
import re
import unicodedata
from typing import Dict, List, Optional, Set, Tuple

from app.models.extraction import BlockType, ExtractionMethod
from app.schemas.extraction import ExtractionBlockData, ExtractionResult

logger = logging.getLogger("ai_hiring.services.text_normalization")

NORMALIZER_VERSION = "normalize-v1"


class NormalizationWarningCode(str, enum.Enum):
    LOW_PRINTABLE_TEXT_RATIO = "LOW_PRINTABLE_TEXT_RATIO"
    EXCESSIVE_REPLACEMENT_CHARACTERS = "EXCESSIVE_REPLACEMENT_CHARACTERS"
    EXCESSIVE_FRAGMENTATION = "EXCESSIVE_FRAGMENTATION"
    EMPTY_BLOCK_REMOVED = "EMPTY_BLOCK_REMOVED"
    PARTIAL_DOCUMENT_TEXT = "PARTIAL_DOCUMENT_TEXT"
    UNSUPPORTED_VISUAL_CONTENT = "UNSUPPORTED_VISUAL_CONTENT"


class TextNormalizationError(Exception):
    """Base exception for controlled text normalization errors."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class NormalizedTextUnavailableError(TextNormalizationError):
    def __init__(self, message: str = "No usable text remained after normalization."):
        super().__init__(code="NORMALIZED_TEXT_UNAVAILABLE", message=message)


@dataclasses.dataclass(frozen=True)
class NormalizationPolicy:
    min_printable_ratio: float = 0.95
    max_replacement_ratio: float = 0.02
    max_tiny_block_ratio: float = 0.30
    tiny_block_char_threshold: int = 3
    repeated_marginal_max_len: int = 200
    repeated_marginal_min_pages: int = 2
    repeated_marginal_page_ratio: float = 0.50


@dataclasses.dataclass(frozen=True)
class NormalizationQualityFacts:
    block_count: int
    raw_character_count: int
    normalized_character_count: int
    empty_blocks_removed: int
    replacement_character_ratio: float
    printable_character_ratio: float
    tiny_block_ratio: float
    repeated_header_count: int
    repeated_footer_count: int
    review_required: bool
    warning_codes: List[NormalizationWarningCode]


@dataclasses.dataclass(frozen=True)
class NormalizationOutcome:
    extraction_result: ExtractionResult
    quality_facts: NormalizationQualityFacts
    warning_codes: List[NormalizationWarningCode]
    raw_character_count: int
    normalized_character_count: int
    review_required: bool


# C0 control characters regex (exclude \t, \n, \r)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Horizontal whitespace regex (excluding newlines)
_HORIZONTAL_WS_RE = re.compile(r"[^\S\n\r]+")

# Conservative line-wrap hyphenation regex
# Condition: word character(s) before hyphen -> hyphen -> newline -> lowercase word character(s)
# Left side must end with at least 2 letters, right side must start with lowercase letter and have >= 2 letters
_DEHYPHENATION_RE = re.compile(r"\b([a-zA-Z]{2,})-\n([a-z]{2,})\b")


def _normalize_text_content(raw_text: str) -> str:
    """
    Deterministically normalizes raw text:
    1. Unicode NFKC normalization.
    2. Convert CRLF and CR to LF.
    3. Remove unsafe C0 control characters (NUL, etc.), preserve LF and tab.
    4. Convert tabs into a single space.
    5. Convert Unicode horizontal space separators to regular spaces and collapse repeated spaces.
    6. Perform conservative line-wrap dehyphenation.
    7. Trim trailing spaces on each line and collapse excessive blank lines.
    8. Trim leading and trailing blank lines/whitespace.
    """
    if not raw_text:
        return ""

    # 1. Unicode NFKC
    text = unicodedata.normalize("NFKC", raw_text)

    # 2. Line endings to \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 3. Control characters
    text = _CONTROL_CHAR_RE.sub("", text)

    # 4. Tabs to space
    text = text.replace("\t", " ")

    # 5. Collapse horizontal whitespace per line
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        cleaned_line = _HORIZONTAL_WS_RE.sub(" ", line).strip()
        cleaned_lines.append(cleaned_line)
    text = "\n".join(cleaned_lines)

    # 6. Conservative dehyphenation
    text = _DEHYPHENATION_RE.sub(r"\1\2", text)

    # 7. Collapse excessive blank lines (more than 1 blank line -> 1 blank line)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 8. Trim overall whitespace
    return text.strip()


def _detect_repeated_pdf_marginals(
    blocks: List[ExtractionBlockData],
    policy: NormalizationPolicy,
) -> Tuple[Set[int], Set[int]]:
    """
    Detects repeated headers and footers for PDF_TEXT blocks only.
    Returns (set of header sequence indexes, set of footer sequence indexes).
    """
    # Group blocks by page_number
    pages_blocks: Dict[int, List[ExtractionBlockData]] = {}
    for block in blocks:
        if block.extraction_method == ExtractionMethod.PDF_TEXT and block.page_number is not None:
            pages_blocks.setdefault(block.page_number, []).append(block)

    total_pages = len(pages_blocks)
    if total_pages < policy.repeated_marginal_min_pages:
        return set(), set()

    first_blocks_by_norm: Dict[str, List[ExtractionBlockData]] = {}
    last_blocks_by_norm: Dict[str, List[ExtractionBlockData]] = {}

    for page_num, p_blocks in pages_blocks.items():
        if not p_blocks:
            continue

        first_b = p_blocks[0]
        # Ignore if already typed or too long
        if len(first_b.normalized_text) <= policy.repeated_marginal_max_len:
            first_blocks_by_norm.setdefault(first_b.normalized_text, []).append(first_b)

        if len(p_blocks) > 1:
            last_b = p_blocks[-1]
            if len(last_b.normalized_text) <= policy.repeated_marginal_max_len:
                last_blocks_by_norm.setdefault(last_b.normalized_text, []).append(last_b)

    header_indices: Set[int] = set()
    footer_indices: Set[int] = set()

    min_occurrences = max(policy.repeated_marginal_min_pages, int(total_pages * policy.repeated_marginal_page_ratio))

    for norm_text, matched_blocks in first_blocks_by_norm.items():
        # Check distinct pages
        distinct_pages = {b.page_number for b in matched_blocks}
        if len(distinct_pages) >= min_occurrences:
            for b in matched_blocks:
                header_indices.add(b.sequence_index)

    for norm_text, matched_blocks in last_blocks_by_norm.items():
        distinct_pages = {b.page_number for b in matched_blocks}
        if len(distinct_pages) >= min_occurrences:
            for b in matched_blocks:
                footer_indices.add(b.sequence_index)

    return header_indices, footer_indices


def _build_pipeline_version(base_version: str) -> str:
    """Appends normalization version cleanly and idempotently."""
    suffix = f"+{NORMALIZER_VERSION}"
    if base_version.endswith(suffix):
        return base_version
    # If base version already contains normalizer with another version tag, don't double append
    if f"+{NORMALIZER_VERSION}" in base_version:
        return base_version
    return f"{base_version}{suffix}"


def normalize_extraction_result(
    result: ExtractionResult,
    policy: Optional[NormalizationPolicy] = None,
) -> NormalizationOutcome:
    """
    Deterministic, format-independent normalization service.

    Transforms raw PDF/DOCX extraction blocks into consistent evidence-ready text
    while preserving immutable raw_text, block IDs, page numbers, structural locations,
    and sequence order.
    """
    if not isinstance(result, ExtractionResult):
        raise TextNormalizationError(
            code="INVALID_EXTRACTION_RESULT",
            message="Invalid extraction result provided.",
        )

    if policy is None:
        policy = NormalizationPolicy()

    # 1. Normalize each block's text from raw_text
    processed_blocks: List[ExtractionBlockData] = []
    empty_blocks_removed = 0
    total_raw_chars = 0
    total_norm_chars = 0
    total_replacement_chars = 0
    total_printable_chars = 0
    tiny_blocks_count = 0
    any_block_review_required = False

    for block in result.blocks:
        total_raw_chars += len(block.raw_text)

        norm_text = _normalize_text_content(block.raw_text)

        # Check if block became completely empty
        if not norm_text:
            empty_blocks_removed += 1
            continue

        total_norm_chars += len(norm_text)
        total_replacement_chars += norm_text.count("\ufffd")
        total_printable_chars += sum(1 for c in norm_text if c.isprintable() or c in "\n\r\t")

        # Tiny block check (non-whitespace chars < threshold)
        meaningful_chars = len(re.sub(r"\s+", "", norm_text))
        if meaningful_chars < policy.tiny_block_char_threshold:
            tiny_blocks_count += 1

        if block.review_required:
            any_block_review_required = True

        new_block = ExtractionBlockData(
            block_id=block.block_id,
            sequence_index=len(processed_blocks),  # temporary index, will finalize later
            page_number=block.page_number,
            structural_location=block.structural_location,
            block_type=block.block_type,
            raw_text=block.raw_text,  # raw_text is immutable
            normalized_text=norm_text,
            extraction_method=block.extraction_method,
            confidence=block.confidence,
            review_required=block.review_required,
        )
        processed_blocks.append(new_block)

    # If all blocks became empty
    if not processed_blocks:
        logger.info("Normalization produced zero usable text blocks.")
        raise NormalizedTextUnavailableError()

    # 2. Repeated PDF marginal detection (PDF_TEXT only)
    detected_headers, detected_footers = _detect_repeated_pdf_marginals(processed_blocks, policy)

    final_blocks: List[ExtractionBlockData] = []
    header_retype_count = 0
    footer_retype_count = 0

    for idx, block in enumerate(processed_blocks):
        b_type = block.block_type
        if idx in detected_headers and b_type not in (BlockType.HEADER, BlockType.FOOTER):
            b_type = BlockType.HEADER
            header_retype_count += 1
        elif idx in detected_footers and b_type not in (BlockType.HEADER, BlockType.FOOTER):
            b_type = BlockType.FOOTER
            footer_retype_count += 1

        final_block = ExtractionBlockData(
            block_id=block.block_id,
            sequence_index=idx,  # contiguous 0-indexed
            page_number=block.page_number,
            structural_location=block.structural_location,
            block_type=b_type,
            raw_text=block.raw_text,
            normalized_text=block.normalized_text,
            extraction_method=block.extraction_method,
            confidence=block.confidence,
            review_required=block.review_required,
        )
        final_blocks.append(final_block)

    # 3. Compute quality metrics and warnings
    block_count = len(final_blocks)
    printable_ratio = round(total_printable_chars / total_norm_chars, 4) if total_norm_chars > 0 else 1.0
    replacement_ratio = round(total_replacement_chars / total_norm_chars, 4) if total_norm_chars > 0 else 0.0
    tiny_block_ratio = round(tiny_blocks_count / block_count, 4) if block_count > 0 else 0.0

    warnings: List[NormalizationWarningCode] = []
    doc_review_required = result.review_required or any_block_review_required

    if printable_ratio < policy.min_printable_ratio:
        warnings.append(NormalizationWarningCode.LOW_PRINTABLE_TEXT_RATIO)
        doc_review_required = True

    if replacement_ratio > policy.max_replacement_ratio:
        warnings.append(NormalizationWarningCode.EXCESSIVE_REPLACEMENT_CHARACTERS)
        doc_review_required = True

    if tiny_block_ratio > policy.max_tiny_block_ratio:
        warnings.append(NormalizationWarningCode.EXCESSIVE_FRAGMENTATION)
        doc_review_required = True

    if empty_blocks_removed > 0:
        warnings.append(NormalizationWarningCode.EMPTY_BLOCK_REMOVED)

    quality_facts = NormalizationQualityFacts(
        block_count=block_count,
        raw_character_count=total_raw_chars,
        normalized_character_count=total_norm_chars,
        empty_blocks_removed=empty_blocks_removed,
        replacement_character_ratio=replacement_ratio,
        printable_character_ratio=printable_ratio,
        tiny_block_ratio=tiny_block_ratio,
        repeated_header_count=header_retype_count,
        repeated_footer_count=footer_retype_count,
        review_required=doc_review_required,
        warning_codes=warnings,
    )

    new_version = _build_pipeline_version(result.extractor_version)

    normalized_result = ExtractionResult(
        extractor_version=new_version,
        review_required=doc_review_required,
        blocks=final_blocks,
    )

    return NormalizationOutcome(
        extraction_result=normalized_result,
        quality_facts=quality_facts,
        warning_codes=warnings,
        raw_character_count=total_raw_chars,
        normalized_character_count=total_norm_chars,
        review_required=doc_review_required,
    )
