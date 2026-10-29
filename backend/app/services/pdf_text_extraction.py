import dataclasses
import enum
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import pdfplumber
import pypdf
from pypdf.errors import EmptyFileError, PdfReadError, PyPdfError

from app.config import Settings
from app.models.extraction import BlockType, ExtractionMethod
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.extraction import ExtractionBlockData, ExtractionResult

logger = logging.getLogger("ai_hiring.services.pdf_text_extraction")

PDF_DIRECT_EXTRACTOR_VERSION = "pdf-layout-text-v1.1"

KNOWN_SECTION_HEADINGS = {
    "CONTACT",
    "CONTACT INFORMATION",
    "CONTACT DETAILS",
    "CONTACTS",
    "TECHNICAL TOOLKIT",
    "TOOLKIT",
    "SKILLS",
    "SKILL",
    "TECHNICAL SKILLS",
    "TECHNICAL SKILL",
    "CORE SKILLS",
    "KEY SKILLS",
    "CERTIFICATES",
    "CERTIFICATE",
    "CERTIFICATIONS",
    "CERTIFICATION",
    "PROFESSIONAL CERTIFICATIONS",
    "PROFESSIONAL CERTIFICATION",
    "LANGUAGE PROFICIENCY",
    "LANGUAGES",
    "LANGUAGE",
    "LANGUAGES KNOWN",
    "PROFESSIONAL PROFILE",
    "PROFILE",
    "SUMMARY",
    "PROFESSIONAL SUMMARY",
    "CAREER SUMMARY",
    "EXECUTIVE SUMMARY",
    "PROFESSIONAL EXPERIENCE",
    "EXPERIENCE",
    "WORK EXPERIENCE",
    "EMPLOYMENT HISTORY",
    "WORK HISTORY",
    "ENGINEERING PROJECTS",
    "ENGINEERING PROJECT",
    "PROJECTS",
    "PROJECT",
    "SELECTED PROJECTS",
    "SELECTED PROJECT",
    "KEY PROJECTS",
    "KEY PROJECT",
    "TECHNICAL PROJECTS",
    "TECHNICAL PROJECT",
    "EDUCATION",
    "ACADEMIC BACKGROUND",
    "ACADEMIC QUALIFICATIONS",
    "QUALIFICATIONS",
}


class PageClassification(str, enum.Enum):
    TEXT = "TEXT"
    MIXED = "MIXED"
    IMAGE_ONLY = "IMAGE_ONLY"
    EMPTY = "EMPTY"
    UNREADABLE = "UNREADABLE"


class PageLayoutType(str, enum.Enum):
    SINGLE_COLUMN = "SINGLE_COLUMN"
    TWO_COLUMN = "TWO_COLUMN"
    UNCERTAIN_LAYOUT = "UNCERTAIN_LAYOUT"


class PDFExtractionError(Exception):
    """Base exception for controlled PDF text extraction failures."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ArtifactIntegrityError(PDFExtractionError):
    def __init__(self, message: str = "Artifact integrity verification failed."):
        super().__init__(code="ARTIFACT_INTEGRITY_FAILED", message=message)


class PDFTextLimitExceededError(PDFExtractionError):
    def __init__(self, message: str = "Extracted PDF character limit exceeded."):
        super().__init__(code="PDF_TEXT_LIMIT_EXCEEDED", message=message)


class PDFExtractionFailedError(PDFExtractionError):
    def __init__(self, message: str = "Failed to extract text from PDF document."):
        super().__init__(code="PDF_EXTRACTION_FAILED", message=message)


class PDFDirectTextUnavailableError(PDFExtractionError):
    def __init__(
        self,
        message: str = "No readable digital text was found. Please upload a text-based PDF or DOCX document.",
    ):
        super().__init__(code="PDF_DIRECT_TEXT_UNAVAILABLE", message=message)


@dataclasses.dataclass(frozen=True)
class PageAssessment:
    page_number: int
    classification: PageClassification
    extracted_character_count: int
    printable_character_ratio: float
    replacement_character_count: int
    has_visual_content: bool
    review_required: bool
    layout_type: Optional[str] = None
    column_count: int = 1


@dataclasses.dataclass(frozen=True)
class PDFDirectExtractionResult:
    extractor_version: str
    blocks: List[ExtractionBlockData]
    pages: List[PageAssessment]
    review_required: bool
    total_character_count: int
    extraction_result: Optional[ExtractionResult]


def _verify_and_resolve_quarantined_pdf(
    artifact: ResumeArtifact,
    settings: Settings,
) -> Path:
    """
    Verifies artifact preconditions and returns the resolved quarantined file path.

    Preconditions:
    1. ResumeArtifact is in VALIDATED state.
    2. detected_format is PDF.
    3. quarantine_key uses expected opaque PDF form ("pdf/{artifact_id}.pdf").
    4. resolved file remains inside the trusted PDF quarantine directory.
    5. file exists and is a regular file (not a symlink or directory).
    6. actual file size matches the stored artifact size_bytes.
    7. recalculated SHA-256 matches stored artifact sha256_hash.
    """
    if not isinstance(artifact, ResumeArtifact):
        raise ArtifactIntegrityError("Invalid resume artifact supplied.")

    if artifact.validation_status != ArtifactValidationStatus.VALIDATED:
        raise ArtifactIntegrityError("Resume artifact must be in VALIDATED state.")

    if artifact.detected_format != ArtifactFormat.pdf:
        raise ArtifactIntegrityError("Resume artifact format must be PDF.")

    if not artifact.quarantine_key or not isinstance(artifact.quarantine_key, str):
        raise ArtifactIntegrityError("Quarantine key is missing.")

    expected_key_suffix = f"{artifact.artifact_id}.pdf"
    if artifact.quarantine_key != f"pdf/{expected_key_suffix}":
        raise ArtifactIntegrityError("Quarantine key format mismatch.")

    quarantine_base = settings.get_quarantine_path()
    quarantine_pdf_dir = (quarantine_base / "pdf").resolve()

    raw_target_path = quarantine_base / artifact.quarantine_key
    if raw_target_path.is_symlink():
        raise ArtifactIntegrityError("Quarantined file cannot be a symlink.")

    target_path = raw_target_path.resolve()

    try:
        target_path.relative_to(quarantine_pdf_dir)
    except ValueError:
        raise ArtifactIntegrityError("Quarantine file path is outside trusted directory.")

    if target_path.is_symlink() or not target_path.is_file():
        raise ArtifactIntegrityError("Quarantined file is missing, invalid, or a symlink.")

    try:
        actual_size = os.path.getsize(target_path)
    except OSError:
        raise ArtifactIntegrityError("Unable to inspect quarantined file size.")

    if artifact.size_bytes is not None and actual_size != artifact.size_bytes:
        raise ArtifactIntegrityError("Quarantined file size mismatch.")

    sha256 = hashlib.sha256()
    try:
        with open(target_path, "rb") as f:
            while chunk := f.read(65536):
                sha256.update(chunk)
    except OSError:
        raise ArtifactIntegrityError("Unable to read quarantined file for hash verification.")

    actual_hash = sha256.hexdigest().lower()
    if artifact.sha256_hash is not None and actual_hash != artifact.sha256_hash.lower():
        raise ArtifactIntegrityError("Quarantined file SHA-256 hash mismatch.")

    return target_path


def _resolve_obj(obj: Any) -> Any:
    """Safely dereference indirect PDF objects."""
    if obj is None:
        return None
    if hasattr(obj, "get_object"):
        try:
            return obj.get_object()
        except Exception:
            return obj
    return obj


def _detect_visual_content(page: pypdf.PageObject) -> bool:
    """
    Safely inspects whether a PDF page contains meaningful visual/image content.
    Checks /Resources -> /XObject dictionaries for /Subtype == /Image, or
    page.images property if accessible.
    """
    try:
        if hasattr(page, "images") and len(page.images) > 0:
            return True
    except Exception:
        pass

    try:
        page_dict = _resolve_obj(page)
        if not isinstance(page_dict, dict):
            return False

        resources = _resolve_obj(page_dict.get("/Resources"))
        if not isinstance(resources, dict):
            return False

        xobjects = _resolve_obj(resources.get("/XObject"))
        if not isinstance(xobjects, dict):
            return False

        for _, xobj_ref in xobjects.items():
            xobj = _resolve_obj(xobj_ref)
            if isinstance(xobj, dict):
                subtype = _resolve_obj(xobj.get("/Subtype"))
                if str(subtype) == "/Image":
                    return True
    except Exception:
        pass

    return False


@dataclasses.dataclass
class _WordItem:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    fontname: str
    size: float


class _VisualLine:
    def __init__(self, words: List[_WordItem]):
        self.words = sorted(words, key=lambda x: x.x0)
        self.text = " ".join(w.text for w in self.words)
        self.x0 = min(w.x0 for w in self.words)
        self.x1 = max(w.x1 for w in self.words)
        self.top = min(w.top for w in self.words)
        self.bottom = max(w.bottom for w in self.words)
        self.height = self.bottom - self.top
        self.fontname = self.words[0].fontname if self.words else ""
        self.size = max((w.size for w in self.words), default=10.0)


def _detect_page_column_layout(
    words: List[_WordItem],
    page_width: float,
) -> Tuple[PageLayoutType, int, Optional[float]]:
    """
    Detects whether words on a page form a single-column, two-column, or uncertain layout.
    Uses horizontal word-interval histogram and identifies vertical whitespace gutters.
    """
    if len(words) < 5:
        return PageLayoutType.SINGLE_COLUMN, 1, None

    width_int = int(page_width)
    x_coverage = [0] * max(width_int, 1)

    min_word_x = min(w.x0 for w in words)
    max_word_x = max(w.x1 for w in words)

    for w in words:
        s = int(max(0, min(width_int - 1, w.x0)))
        e = int(max(0, min(width_int - 1, w.x1)))
        for x in range(s, e + 1):
            x_coverage[x] += 1

    # Scan for vertical whitespace gutter strictly within the text bounds (min_word_x to max_word_x)
    scan_min = int(max(0.10 * width_int, min_word_x + 10))
    scan_max = int(min(0.90 * width_int, max_word_x - 10))

    if scan_max <= scan_min:
        return PageLayoutType.SINGLE_COLUMN, 1, None

    gutters: List[Tuple[int, int, int]] = []
    cur_start = None

    for x in range(scan_min, scan_max):
        if x_coverage[x] == 0:
            if cur_start is None:
                cur_start = x
        else:
            if cur_start is not None:
                g_len = x - cur_start
                if g_len >= 12:  # at least 12pt gutter
                    gutters.append((cur_start, x, g_len))
                cur_start = None

    if cur_start is not None:
        g_len = scan_max - cur_start
        if g_len >= 12:
            gutters.append((cur_start, scan_max, g_len))

    if not gutters:
        return PageLayoutType.SINGLE_COLUMN, 1, None

    # If multiple distinct internal gutters exist across the page (> 1 separated column gutters),
    # this indicates a 3+ column layout or complex multi-pane layout -> UNCERTAIN_LAYOUT
    if len(gutters) >= 2:
        return PageLayoutType.UNCERTAIN_LAYOUT, len(gutters) + 1, None

    # Single internal gutter detected -> examine two-column separation
    g_start, g_end, g_width = gutters[0]
    split_x = (g_start + g_end) / 2.0

    left_words = [w for w in words if w.x1 <= split_x]
    right_words = [w for w in words if w.x0 >= split_x]
    spanning_words = [w for w in words if w.x0 < split_x and w.x1 > split_x]

    # If substantial words exist on both sides and few words span across the gutter
    if len(left_words) >= 4 and len(right_words) >= 4 and len(spanning_words) <= 2:
        return PageLayoutType.TWO_COLUMN, 2, split_x

    return PageLayoutType.SINGLE_COLUMN, 1, None



def _words_to_visual_lines(words: List[_WordItem]) -> List[_VisualLine]:
    """Reconstructs visual lines from words using vertical coordinate proximity."""
    if not words:
        return []

    lines_raw: List[List[_WordItem]] = []
    for w in sorted(words, key=lambda x: (x.top, x.x0)):
        if not lines_raw or abs(lines_raw[-1][0].top - w.top) > 3.0:
            lines_raw.append([w])
        else:
            lines_raw[-1].append(w)

    return [_VisualLine(l) for l in lines_raw]


def _lines_to_extraction_blocks(
    lines: List[_VisualLine],
    page_number: int,
    start_seq: int,
    column_index: int = 0,
) -> Tuple[List[ExtractionBlockData], int]:
    """
    Groups visual lines within a column into coherent ExtractionBlockData records.
    Keeps section headings isolated as HEADING blocks.
    Groups wrapped body lines into PARAGRAPH blocks.
    """
    blocks: List[ExtractionBlockData] = []
    cur_lines: List[_VisualLine] = []
    seq = start_seq

    def flush():
        nonlocal cur_lines, seq
        if not cur_lines:
            return
        
        # Check if line continues across hyphens (e.g. "AWS Certified Solutions Architect -\nAssociate" -> single text)
        joined_parts: List[str] = []
        for l in cur_lines:
            txt = l.text.strip()
            if joined_parts and joined_parts[-1].endswith("-"):
                # Join hyphenated continuation: "Architect -" + "Associate" -> "Architect - Associate"
                joined_parts[-1] = f"{joined_parts[-1]} {txt}"
            else:
                joined_parts.append(txt)

        t = "\n".join(joined_parts).strip()
        if not t:
            cur_lines = []
            return

        is_head = len(cur_lines) == 1 and cur_lines[0].text.strip().upper() in KNOWN_SECTION_HEADINGS
        b_type = BlockType.HEADING if is_head else BlockType.PARAGRAPH

        blocks.append(
            ExtractionBlockData(
                sequence_index=seq,
                page_number=page_number,
                structural_location=f"col_{column_index}",
                block_type=b_type,
                raw_text=t,
                normalized_text=t,
                extraction_method=ExtractionMethod.PDF_TEXT,
                confidence=0.95,
                review_required=False,
            )
        )
        seq += 1
        cur_lines = []

    for line in lines:
        line_str = line.text.strip()
        if not line_str:
            continue

        if line_str.upper() in KNOWN_SECTION_HEADINGS:
            flush()
            cur_lines.append(line)
            flush()
            continue

        if not cur_lines:
            cur_lines.append(line)
            continue

        prev = cur_lines[-1]
        gap = line.top - prev.bottom
        line_h = max(prev.height, line.height, 8.0)

        # Start a new block when:
        # 1. Previous line was a section heading
        if prev.text.strip().upper() in KNOWN_SECTION_HEADINGS:
            flush()
            cur_lines.append(line)
        # 2. Large font difference (e.g. Candidate Name size 22 vs Headline size 11.5)
        elif prev.size >= 16.0 and line.size <= 14.0:
            flush()
            cur_lines.append(line)
        # 3. Significant vertical gap between lines (> 1.4 * line_height)
        elif gap > 1.4 * line_h:
            flush()
            cur_lines.append(line)
        # 4. Bold line preceded by a non-bold line
        elif "Bold" in line.fontname and "Bold" not in prev.fontname:
            flush()
            cur_lines.append(line)
        # 5. Bullet point prefix
        elif line_str.startswith(("•", "-", "*")):
            flush()
            cur_lines.append(line)
        # 6. List items in sidebar or contact items
        elif (
            prev.text.strip().startswith(("•", "-", "*"))
            or "@" in line_str
            or line_str.startswith("+")
            or "github.com" in line_str
            or "linkedin.com" in line_str
            or ":" in line_str
            and line_str.split(":")[0].strip().upper() in ("ISSUER", "ISSUED", "EXPIRES", "CREDENTIAL ID", "ENGLISH", "TAMIL", "HINDI")
        ):
            flush()
            cur_lines.append(line)
        else:
            cur_lines.append(line)

    flush()
    return blocks, seq


def _extract_page_with_pdfplumber(
    page: pdfplumber.page.Page,
    page_number: int,
    start_seq: int,
) -> Tuple[List[ExtractionBlockData], int, PageLayoutType, int, int, float, int]:
    """
    Extracts layout-aware blocks from a single PDF page using pdfplumber word coordinates.
    """
    raw_words = page.extract_words(use_text_flow=False, extra_attrs=["fontname", "size"]) or []

    # 1. Filter out footer disclaimer text located at the bottom margin (top > page.height - 40)
    body_words: List[_WordItem] = []
    for w in raw_words:
        top = float(w["top"])
        text = str(w["text"])
        # Completely ignore words at the bottom 40pt margin
        if top > page.height - 40:
            continue
        body_words.append(
            _WordItem(
                text=text,
                x0=float(w["x0"]),
                x1=float(w["x1"]),
                top=top,
                bottom=float(w["bottom"]),
                fontname=str(w.get("fontname", "")),
                size=float(w.get("size", 10.0)),
            )
        )

    if not body_words:
        return [], start_seq, PageLayoutType.SINGLE_COLUMN, 1, 0, 1.0, 0

    # Character count and quality metrics
    all_text = " ".join(w.text for w in body_words)
    char_count = len(all_text)
    rep_count = all_text.count("\ufffd")
    printable_count = sum(1 for c in all_text if c.isprintable() or c in "\n\r\t")
    printable_ratio = round(printable_count / max(char_count, 1), 4)

    # 2. Detect column layout
    layout_type, col_count, split_x = _detect_page_column_layout(body_words, page.width)

    page_blocks: List[ExtractionBlockData] = []
    cur_seq = start_seq

    if layout_type == PageLayoutType.TWO_COLUMN and split_x is not None:
        # Separate spanning headers, left column words, right column words
        spanning_words = [w for w in body_words if w.top < 150 and w.x0 < split_x and w.x1 > split_x]
        left_words = [w for w in body_words if w.x1 <= split_x]
        right_words = [w for w in body_words if w.x0 >= split_x]

        # Top spanning header blocks (if any)
        if spanning_words:
            span_lines = _words_to_visual_lines(spanning_words)
            span_blocks, cur_seq = _lines_to_extraction_blocks(span_lines, page_number, cur_seq, column_index=-1)
            page_blocks.extend(span_blocks)

        lines_left = _words_to_visual_lines(left_words)
        lines_right = _words_to_visual_lines(right_words)

        # Check if right column has primary headline / name at top
        right_has_top_title = lines_right and lines_right[0].top <= (lines_left[0].top if lines_left else 999.0)

        if right_has_top_title:
            blocks_main, cur_seq = _lines_to_extraction_blocks(lines_right, page_number, cur_seq, column_index=1)
            blocks_side, cur_seq = _lines_to_extraction_blocks(lines_left, page_number, cur_seq, column_index=0)
            page_blocks.extend(blocks_main)
            page_blocks.extend(blocks_side)
        else:
            blocks_side, cur_seq = _lines_to_extraction_blocks(lines_left, page_number, cur_seq, column_index=0)
            blocks_main, cur_seq = _lines_to_extraction_blocks(lines_right, page_number, cur_seq, column_index=1)
            page_blocks.extend(blocks_side)
            page_blocks.extend(blocks_main)

    else:
        # Single column or uncertain layout: group lines top-to-bottom
        lines = _words_to_visual_lines(body_words)
        col_blocks, cur_seq = _lines_to_extraction_blocks(lines, page_number, cur_seq, column_index=0)
        page_blocks.extend(col_blocks)

    return page_blocks, cur_seq, layout_type, col_count, char_count, printable_ratio, rep_count


def _split_text_into_blocks_fallback(
    text: str,
    page_number: int,
    start_sequence_index: int,
) -> List[ExtractionBlockData]:
    """Fallback block splitter for plain text streams."""
    normalized_newlines = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_chunks = normalized_newlines.split("\n\n")
    blocks: List[ExtractionBlockData] = []
    current_index = start_sequence_index

    for chunk in raw_chunks:
        cleaned = chunk.strip()
        if not cleaned:
            continue

        is_head = cleaned.upper() in KNOWN_SECTION_HEADINGS
        b_type = BlockType.HEADING if is_head else BlockType.PARAGRAPH

        block = ExtractionBlockData(
            sequence_index=current_index,
            page_number=page_number,
            structural_location=None,
            block_type=b_type,
            raw_text=cleaned,
            normalized_text=cleaned,
            extraction_method=ExtractionMethod.PDF_TEXT,
            confidence=0.85,
            review_required=False,
        )
        blocks.append(block)
        current_index += 1

    return blocks


def extract_pdf_direct_text(
    artifact: ResumeArtifact,
    settings: Optional[Settings] = None,
) -> PDFDirectExtractionResult:
    """
    Layout-aware direct-text PDF extractor for validated and quarantined PDF artifacts.

    Uses pdfplumber for positional word extraction, gutter detection, and multi-column isolation.
    Preserves bounded, deterministic, OCR-free processing and column boundary isolation.
    """
    if settings is None:
        settings = Settings()

    # 1. Verify artifact preconditions & get trusted path
    quarantined_path = _verify_and_resolve_quarantined_pdf(artifact, settings)

    min_text_chars = settings.min_pdf_direct_text_chars
    max_chars = settings.max_pdf_extracted_chars

    all_blocks: List[ExtractionBlockData] = []
    page_assessments: List[PageAssessment] = []
    total_extracted_chars = 0
    any_page_review_required = False

    # 2. Defensive PDF processing using pdfplumber with pypdf validation support
    try:
        pypdf_reader = pypdf.PdfReader(str(quarantined_path), strict=False)
        if pypdf_reader.is_encrypted:
            raise PDFExtractionFailedError("Quarantined PDF is unexpectedly encrypted.")
        num_pages = len(pypdf_reader.pages)
        if num_pages == 0:
            raise PDFExtractionFailedError("Quarantined PDF contains no pages.")
    except PDFExtractionError:
        raise
    except (EmptyFileError, PdfReadError, PyPdfError, ValueError, KeyError, EOFError, IndexError) as e:
        logger.warning(
            "Failed to open quarantined PDF with pypdf",
            extra={"artifact_id": str(artifact.artifact_id), "error_type": type(e).__name__},
        )
        raise PDFExtractionFailedError("Failed to open PDF document.") from e
    except Exception as e:
        raise PDFExtractionFailedError("Unexpected failure opening PDF document.") from e

    try:
        with pdfplumber.open(quarantined_path) as pdf:
            for page_idx in range(num_pages):
                page_number = page_idx + 1
                pypdf_page = pypdf_reader.pages[page_idx] if page_idx < len(pypdf_reader.pages) else None
                has_visual = _detect_visual_content(pypdf_page) if pypdf_page else False

                try:
                    if page_idx < len(pdf.pages):
                        plum_page = pdf.pages[page_idx]
                        (
                            page_blocks,
                            next_seq,
                            layout_type,
                            col_count,
                            extracted_len,
                            printable_ratio,
                            replacement_count,
                        ) = _extract_page_with_pdfplumber(
                            page=plum_page,
                            page_number=page_number,
                            start_seq=len(all_blocks),
                        )
                    else:
                        raise ValueError("Page missing in pdfplumber handle.")
                except Exception as e:
                    logger.warning(
                        "pdfplumber extraction failed on page, falling back to pypdf",
                        extra={"artifact_id": str(artifact.artifact_id), "page_number": page_number, "error_type": type(e).__name__},
                    )
                    try:
                        raw_t = pypdf_page.extract_text() or "" if pypdf_page else ""
                    except Exception:
                        raw_t = ""
                    extracted_len = len(raw_t.strip())
                    replacement_count = raw_t.count("\ufffd")
                    printable_ratio = 1.0
                    layout_type = PageLayoutType.SINGLE_COLUMN
                    col_count = 1
                    page_blocks = _split_text_into_blocks_fallback(raw_t, page_number, len(all_blocks))

                # Check character limit boundedly
                if total_extracted_chars + extracted_len > max_chars:
                    raise PDFTextLimitExceededError(f"PDF character limit of {max_chars} exceeded.")

                # Classify page
                page_review = False
                if layout_type == PageLayoutType.UNCERTAIN_LAYOUT:
                    page_review = True

                if extracted_len >= min_text_chars:
                    if replacement_count > 10 or printable_ratio < 0.85:
                        classification = PageClassification.UNREADABLE
                        page_review = True
                    elif has_visual:
                        classification = PageClassification.MIXED
                    else:
                        classification = PageClassification.TEXT
                else:
                    if has_visual:
                        classification = PageClassification.IMAGE_ONLY
                        page_review = True
                    elif extracted_len > 0:
                        classification = PageClassification.TEXT
                        page_review = True
                    else:
                        classification = PageClassification.EMPTY

                if page_review:
                    any_page_review_required = True

                assessment = PageAssessment(
                    page_number=page_number,
                    classification=classification,
                    extracted_character_count=extracted_len,
                    printable_character_ratio=printable_ratio,
                    replacement_character_count=replacement_count,
                    has_visual_content=has_visual,
                    review_required=page_review,
                    layout_type=layout_type.value,
                    column_count=col_count,
                )
                page_assessments.append(assessment)

                if extracted_len > 0 and classification in (PageClassification.TEXT, PageClassification.MIXED):
                    all_blocks.extend(page_blocks)
                    total_extracted_chars += extracted_len

    except PDFExtractionError:
        raise
    except Exception as e:
        logger.warning(
            "Unexpected error extracting text from PDF",
            extra={"artifact_id": str(artifact.artifact_id), "error_type": type(e).__name__},
        )
        raise PDFExtractionFailedError(f"Failed to extract text from PDF: {e}") from e

    if not all_blocks:
        logger.info(
            "PDF document contains no usable text",
            extra={"artifact_id": str(artifact.artifact_id)},
        )
        raise PDFDirectTextUnavailableError()

    shared_extraction_result = ExtractionResult(
        extractor_version=PDF_DIRECT_EXTRACTOR_VERSION,
        review_required=any_page_review_required,
        blocks=all_blocks,
    )

    return PDFDirectExtractionResult(
        extractor_version=PDF_DIRECT_EXTRACTOR_VERSION,
        blocks=all_blocks,
        pages=page_assessments,
        review_required=any_page_review_required,
        total_character_count=total_extracted_chars,
        extraction_result=shared_extraction_result,
    )
