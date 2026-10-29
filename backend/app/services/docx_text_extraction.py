import dataclasses
import hashlib
import logging
import os
import zipfile
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple
import defusedxml.ElementTree as ET
import docx
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from app.config import Settings
from app.models.extraction import BlockType, ExtractionMethod
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.extraction import ExtractionBlockData, ExtractionResult

logger = logging.getLogger("ai_hiring.services.docx_text_extraction")

DOCX_EXTRACTOR_VERSION = "docx-structural-text-v1.0"

# OpenXML Namespaces
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
NS_V = "urn:schemas-microsoft-com:vml"
NS_W10 = "urn:schemas-microsoft-com:office:word"


class DOCXExtractionError(Exception):
    """Base exception for controlled DOCX text extraction failures."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ArtifactIntegrityError(DOCXExtractionError):
    def __init__(self, message: str = "Artifact integrity verification failed."):
        super().__init__(code="ARTIFACT_INTEGRITY_FAILED", message=message)


class DOCXTextLimitExceededError(DOCXExtractionError):
    def __init__(self, message: str = "Extracted DOCX character limit exceeded."):
        super().__init__(code="DOCX_TEXT_LIMIT_EXCEEDED", message=message)


class DOCXExtractionFailedError(DOCXExtractionError):
    def __init__(self, message: str = "Failed to extract text from DOCX document."):
        super().__init__(code="DOCX_EXTRACTION_FAILED", message=message)


class DOCXTextUnavailableError(DOCXExtractionError):
    def __init__(self, message: str = "No direct text could be extracted from the document."):
        super().__init__(code="DOCX_TEXT_UNAVAILABLE", message=message)


@dataclasses.dataclass(frozen=True)
class DOCXExtractionQualityFacts:
    paragraph_count: int
    table_cell_count: int
    header_block_count: int
    footer_block_count: int
    unsupported_drawing_or_textbox_count: int
    unsupported_nested_table_count: int
    unreliable_tracked_change_count: int
    has_visual_content: bool


@dataclasses.dataclass(frozen=True)
class DOCXExtractionOutcome:
    extractor_version: str
    blocks: List[ExtractionBlockData]
    quality_facts: DOCXExtractionQualityFacts
    review_required: bool
    total_character_count: int
    extraction_result: Optional[ExtractionResult]


def _verify_and_resolve_quarantined_docx(
    artifact: ResumeArtifact,
    settings: Settings,
) -> Path:
    """
    Verifies artifact preconditions and returns the resolved quarantined file path.

    Preconditions:
    1. ResumeArtifact is in VALIDATED state.
    2. detected_format is DOCX.
    3. quarantine_key uses expected opaque DOCX form ("docx/{artifact_id}.docx").
    4. resolved file remains inside the trusted DOCX quarantine directory.
    5. file exists and is a regular file (not a symlink or directory).
    6. actual file size matches the stored artifact size_bytes.
    7. recalculated SHA-256 matches stored artifact sha256_hash.
    """
    if not isinstance(artifact, ResumeArtifact):
        raise ArtifactIntegrityError("Invalid resume artifact supplied.")

    if artifact.validation_status != ArtifactValidationStatus.VALIDATED:
        raise ArtifactIntegrityError("Resume artifact must be in VALIDATED state.")

    if artifact.detected_format != ArtifactFormat.docx:
        raise ArtifactIntegrityError("Resume artifact format must be DOCX.")

    if not artifact.quarantine_key or not isinstance(artifact.quarantine_key, str):
        raise ArtifactIntegrityError("Quarantine key is missing.")

    expected_key_suffix = f"{artifact.artifact_id}.docx"
    if artifact.quarantine_key != f"docx/{expected_key_suffix}":
        raise ArtifactIntegrityError("Quarantine key format mismatch.")

    quarantine_base = settings.get_quarantine_path()
    quarantine_docx_dir = (quarantine_base / "docx").resolve()

    raw_target_path = quarantine_base / artifact.quarantine_key
    if raw_target_path.is_symlink():
        raise ArtifactIntegrityError("Quarantined file cannot be a symlink.")

    target_path = raw_target_path.resolve()

    # Verify containment within trusted DOCX quarantine directory
    try:
        target_path.relative_to(quarantine_docx_dir)
    except ValueError:
        raise ArtifactIntegrityError("Quarantine file path is outside trusted directory.")

    if target_path.is_symlink() or not target_path.is_file():
        raise ArtifactIntegrityError("Quarantined file is missing, invalid, or a symlink.")

    # Size check
    try:
        actual_size = os.path.getsize(target_path)
    except OSError:
        raise ArtifactIntegrityError("Unable to inspect quarantined file size.")

    if artifact.size_bytes is not None and actual_size != artifact.size_bytes:
        raise ArtifactIntegrityError("Quarantined file size mismatch.")

    # SHA-256 recalculation check
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


def _safe_xml_precheck(file_path: Path, settings: Settings) -> None:
    """
    Safely inspects text-bearing XML parts in the DOCX archive without extracting to disk.
    Enforces expanded-size limits and safe XML parsing with defusedxml to reject DTD/entity expansion.
    """
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            entries = zf.infolist()
            total_expanded = 0
            max_expanded = settings.max_docx_expanded_bytes

            # Look only for relevant text-bearing parts:
            # word/document.xml, word/header*.xml, word/footer*.xml
            for info in entries:
                norm_name = info.filename.replace("\\", "/").strip("/").lower()
                is_text_part = (
                    norm_name == "word/document.xml"
                    or (norm_name.startswith("word/header") and norm_name.endswith(".xml"))
                    or (norm_name.startswith("word/footer") and norm_name.endswith(".xml"))
                )
                if not is_text_part:
                    continue

                total_expanded += info.file_size
                if total_expanded > max_expanded:
                    raise DOCXExtractionFailedError("DOCX content exceeds safe expanded limits.")

                part_bytes = zf.read(info)
                try:
                    ET.fromstring(part_bytes)
                except Exception as e:
                    logger.warning(
                        "Safe XML precheck failed for document part",
                        extra={"error_type": type(e).__name__},
                    )
                    raise DOCXExtractionFailedError("Invalid or unsafe XML content detected.") from e
    except DOCXExtractionError:
        raise
    except Exception as e:
        logger.warning(
            "Unexpected failure during safe XML precheck",
            extra={"error_type": type(e).__name__},
        )
        raise DOCXExtractionFailedError("Failed to verify DOCX XML content.") from e


def _extract_paragraph_text_and_signals(p_elem: Any) -> Tuple[str, bool, bool, bool]:
    """
    Extracts visible text from a w:p element in document order, including:
    - w:r runs (w:t, w:tab, w:br, w:cr)
    - w:hyperlink (child w:r runs)
    - w:ins (inserted text from tracked changes)
    - excludes w:del (deleted text from tracked changes)
    - excludes w:fldSimple / w:instrText / w:fldChar instructions
    - excludes w:comment / comment references

    Returns:
        (text, has_drawing_or_textbox, has_image, has_unreliable_tracked_change)
    """
    text_chunks: List[str] = []
    has_drawing_or_textbox = False
    has_image = False
    has_unreliable_tracked_change = False

    # Walk children recursively or iteratively while preserving order
    def walk_elem(elem: Any, in_deleted: bool = False, in_field_instr: bool = False) -> None:
        nonlocal has_drawing_or_textbox, has_image, has_unreliable_tracked_change

        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        tag_lower = tag.lower()

        # Tracked change: deleted text
        if tag_lower in ("del", "deltext", "moveto", "movefrom"):
            if tag_lower in ("del", "movefrom"):
                # Mark anything inside as deleted
                for child in elem:
                    walk_elem(child, in_deleted=True, in_field_instr=in_field_instr)
                return

        # Field instructions: w:instrText
        if tag_lower in ("instrtext", "fldchar", "flddata"):
            # Exclude field codes
            return

        # Comment elements
        if tag_lower in ("commentreference", "commentrangestart", "commentrangeend", "annotation"):
            return

        # Text boxes and drawings
        if tag_lower in ("textbox", "txbxcontent", "wsp", "shape", "pict"):
            has_drawing_or_textbox = True
        if tag_lower in ("drawing", "graphic", "blip", "imagedata"):
            has_image = True
            # Check if there's drawing text inside like txBox
            for child in elem.iter():
                c_tag = child.tag.split("}")[-1].lower() if "}" in child.tag else child.tag.lower()
                if c_tag in ("txbxcontent", "textbox", "wsp"):
                    has_drawing_or_textbox = True

        # Handle text tokens if not inside deleted block or field instructions
        if not in_deleted and not in_field_instr:
            if tag_lower == "t":
                if elem.text:
                    text_chunks.append(elem.text)
            elif tag_lower == "tab":
                text_chunks.append("\t")
            elif tag_lower in ("br", "cr"):
                text_chunks.append("\n")

        # Recurse children
        for child in elem:
            walk_elem(child, in_deleted=in_deleted, in_field_instr=in_field_instr)

    walk_elem(p_elem)
    raw_extracted = "".join(text_chunks)
    return raw_extracted, has_drawing_or_textbox, has_image, has_unreliable_tracked_change


def _classify_paragraph(paragraph: Paragraph) -> BlockType:
    """
    Classifies a python-docx Paragraph conservatively:
    - HEADING: based on heading style name (e.g. 'Heading 1', 'Heading 2') or outline level in pPr
    - LIST_ITEM: based on list/bullet style name or w:numPr numbering properties in pPr
    - PARAGRAPH: normal body paragraphs
    - UNKNOWN: if degraded/unidentifiable
    """
    try:
        pPr = paragraph._element.find(qn("w:pPr"))
        if pPr is not None:
            # Check outline level (w:outlineLvl)
            outline_lvl = pPr.find(qn("w:outlineLvl"))
            if outline_lvl is not None:
                val = outline_lvl.get(qn("w:val"))
                if val is not None and val.isdigit():
                    return BlockType.HEADING

            # Check numbering properties (w:numPr)
            numPr = pPr.find(qn("w:numPr"))
            if numPr is not None:
                return BlockType.LIST_ITEM

        style_name = ""
        if paragraph.style and paragraph.style.name:
            style_name = paragraph.style.name.lower()

        if "heading" in style_name or "title" in style_name:
            return BlockType.HEADING

        if "list" in style_name or "bullet" in style_name:
            return BlockType.LIST_ITEM

        return BlockType.PARAGRAPH
    except Exception:
        return BlockType.UNKNOWN


def _extract_header_footer_text(
    hf_obj: Any,
    location_prefix: str,
    start_seq: int,
    block_type: BlockType,
    seen_part_ids: Set[int],
    max_chars: int,
    current_char_count: int,
) -> Tuple[List[ExtractionBlockData], int, int, bool, bool]:
    """
    Extracts text from a Header or Footer object.
    Deduplicates linked parts across sections.
    """
    blocks: List[ExtractionBlockData] = []
    curr_seq = start_seq
    char_count = current_char_count
    has_drawing_or_textbox = False
    has_image = False

    if hf_obj is None or hf_obj._element is None:
        return blocks, curr_seq, char_count, has_drawing_or_textbox, has_image

    # Check if header/footer is linked to previous section or shares underlying part
    part_elem = hf_obj._element
    if part_elem in seen_part_ids:
        return blocks, curr_seq, char_count, has_drawing_or_textbox, has_image
    seen_part_ids.add(part_elem)

    # Walk paragraphs inside the header/footer
    for p_idx, p in enumerate(hf_obj.paragraphs):
        p_text, p_draw, p_img, _ = _extract_paragraph_text_and_signals(p._element)
        if p_draw:
            has_drawing_or_textbox = True
        if p_img:
            has_image = True

        cleaned = p_text.strip()
        if not cleaned:
            continue

        if char_count + len(cleaned) > max_chars:
            raise DOCXTextLimitExceededError(f"DOCX character limit of {max_chars} exceeded.")

        # Note: Phase 1C.5 will perform normalization.
        block = ExtractionBlockData(
            sequence_index=curr_seq,
            page_number=None,
            structural_location=f"{location_prefix}/paragraph/{p_idx}",
            block_type=block_type,
            raw_text=cleaned,
            normalized_text=cleaned,
            extraction_method=ExtractionMethod.DOCX_TEXT,
            confidence=None,
            review_required=False,
        )
        blocks.append(block)
        curr_seq += 1
        char_count += len(cleaned)

    return blocks, curr_seq, char_count, has_drawing_or_textbox, has_image


def extract_docx_text(
    artifact: ResumeArtifact,
    settings: Optional[Settings] = None,
) -> DOCXExtractionOutcome:
    """
    Safe native-DOCX text extractor for already validated and quarantined DOCX artifacts.

    Preserves interleaved document order of paragraphs and tables, extracts headers and footers,
    returns ordered DOCX_TEXT blocks, computes quality facts, identifies unsupported content,
    and returns a structured DOCXExtractionOutcome.
    """
    if settings is None:
        settings = Settings()

    # 1. Preconditions verification
    quarantined_path = _verify_and_resolve_quarantined_docx(artifact, settings)

    # 2. Safe XML precheck with defusedxml
    _safe_xml_precheck(quarantined_path, settings)

    max_chars = settings.max_docx_extracted_chars

    # 3. Open with python-docx defensively
    try:
        doc = docx.Document(str(quarantined_path))
    except (DOCXExtractionError, zipfile.BadZipFile):
        raise
    except Exception as e:
        logger.warning(
            "Failed to open quarantined DOCX with python-docx",
            extra={"artifact_id": str(artifact.artifact_id), "error_type": type(e).__name__},
        )
        raise DOCXExtractionFailedError("Failed to open DOCX document.") from e

    all_blocks: List[ExtractionBlockData] = []
    current_seq = 0
    total_chars = 0

    paragraph_count = 0
    table_cell_count = 0
    header_block_count = 0
    footer_block_count = 0
    unsupported_drawing_or_textbox_count = 0
    unsupported_nested_table_count = 0
    unreliable_tracked_change_count = 0
    has_visual_content = False
    doc_review_required = False

    seen_header_part_ids: Set[int] = set()
    seen_footer_part_ids: Set[int] = set()

    # 4. Extract headers (deterministic order: headers before body)
    try:
        for s_idx, section in enumerate(doc.sections):
            # Default header
            if section.header and not section.header.is_linked_to_previous:
                h_blocks, current_seq, total_chars, h_draw, h_img = _extract_header_footer_text(
                    hf_obj=section.header,
                    location_prefix=f"section/{s_idx}/header/default",
                    start_seq=current_seq,
                    block_type=BlockType.HEADER,
                    seen_part_ids=seen_header_part_ids,
                    max_chars=max_chars,
                    current_char_count=total_chars,
                )
                all_blocks.extend(h_blocks)
                header_block_count += len(h_blocks)
                if h_draw:
                    unsupported_drawing_or_textbox_count += 1
                    doc_review_required = True
                if h_img:
                    has_visual_content = True

            # First page header
            if section.different_first_page_header_footer and hasattr(section, "first_page_header"):
                fph = section.first_page_header
                if fph and not fph.is_linked_to_previous:
                    h_blocks, current_seq, total_chars, h_draw, h_img = _extract_header_footer_text(
                        hf_obj=fph,
                        location_prefix=f"section/{s_idx}/header/first_page",
                        start_seq=current_seq,
                        block_type=BlockType.HEADER,
                        seen_part_ids=seen_header_part_ids,
                        max_chars=max_chars,
                        current_char_count=total_chars,
                    )
                    all_blocks.extend(h_blocks)
                    header_block_count += len(h_blocks)
                    if h_draw:
                        unsupported_drawing_or_textbox_count += 1
                        doc_review_required = True
                    if h_img:
                        has_visual_content = True
    except DOCXExtractionError:
        raise
    except Exception as e:
        logger.warning(
            "Error extracting headers from DOCX",
            extra={"artifact_id": str(artifact.artifact_id), "error_type": type(e).__name__},
        )
        raise DOCXExtractionFailedError("Failed to extract document header.") from e

    # 5. Extract body children in true document order
    try:
        body_elem = doc._element.body
        if body_elem is None:
            raise DOCXExtractionFailedError("Missing document body.")

        body_paragraph_idx = 0
        body_table_idx = 0

        for child in body_elem.iterchildren():
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

            if tag == "p":
                # Body paragraph
                p_obj = Paragraph(child, doc)
                p_text, p_draw, p_img, p_tc = _extract_paragraph_text_and_signals(child)
                if p_draw:
                    unsupported_drawing_or_textbox_count += 1
                    doc_review_required = True
                if p_img:
                    has_visual_content = True
                if p_tc:
                    unreliable_tracked_change_count += 1
                    doc_review_required = True

                cleaned = p_text.strip()
                if not cleaned:
                    # Empty paragraph with unsupported drawings should influence review signal
                    if p_draw:
                        doc_review_required = True
                    body_paragraph_idx += 1
                    continue

                if total_chars + len(cleaned) > max_chars:
                    raise DOCXTextLimitExceededError(f"DOCX character limit of {max_chars} exceeded.")

                p_type = _classify_paragraph(p_obj)
                block = ExtractionBlockData(
                    sequence_index=current_seq,
                    page_number=None,
                    structural_location=f"body/paragraph/{body_paragraph_idx}",
                    block_type=p_type,
                    raw_text=cleaned,
                    normalized_text=cleaned,
                    extraction_method=ExtractionMethod.DOCX_TEXT,
                    confidence=None,
                    review_required=False,
                )
                all_blocks.append(block)
                current_seq += 1
                total_chars += len(cleaned)
                paragraph_count += 1
                body_paragraph_idx += 1

            elif tag == "tbl":
                # Body table
                tbl_obj = Table(child, doc)
                seen_cell_elements: Set[Any] = set()

                for row_idx, row in enumerate(tbl_obj.rows):
                    for col_idx, cell in enumerate(row.cells):
                        if cell._element in seen_cell_elements:
                            # Avoid duplicate merged-cell extraction
                            continue
                        seen_cell_elements.add(cell._element)

                        # Check for nested tables in cell
                        nested_tbls = cell._element.findall(qn("w:tbl"))
                        if nested_tbls:
                            unsupported_nested_table_count += len(nested_tbls)
                            doc_review_required = True

                        # Extract cell text across all cell paragraphs preserving order
                        cell_para_chunks: List[str] = []
                        cell_has_draw = False
                        cell_has_img = False

                        for cp in cell.paragraphs:
                            c_text, c_draw, c_img, c_tc = _extract_paragraph_text_and_signals(cp._element)
                            if c_draw:
                                cell_has_draw = True
                            if c_img:
                                cell_has_img = True
                            if c_tc:
                                unreliable_tracked_change_count += 1
                                doc_review_required = True
                            if c_text.strip():
                                cell_para_chunks.append(c_text.strip())

                        if cell_has_draw:
                            unsupported_drawing_or_textbox_count += 1
                            doc_review_required = True
                        if cell_has_img:
                            has_visual_content = True

                        if not cell_para_chunks:
                            continue

                        combined_cell_text = "\n".join(cell_para_chunks).strip()
                        if not combined_cell_text:
                            continue

                        if total_chars + len(combined_cell_text) > max_chars:
                            raise DOCXTextLimitExceededError(f"DOCX character limit of {max_chars} exceeded.")

                        block = ExtractionBlockData(
                            sequence_index=current_seq,
                            page_number=None,
                            structural_location=f"body/table/{body_table_idx}/row/{row_idx}/cell/{col_idx}",
                            block_type=BlockType.TABLE_CELL,
                            raw_text=combined_cell_text,
                            normalized_text=combined_cell_text,
                            extraction_method=ExtractionMethod.DOCX_TEXT,
                            confidence=None,
                            review_required=False,
                        )
                        all_blocks.append(block)
                        current_seq += 1
                        total_chars += len(combined_cell_text)
                        table_cell_count += 1

                body_table_idx += 1

    except DOCXExtractionError:
        raise
    except Exception as e:
        logger.warning(
            "Error extracting body content from DOCX",
            extra={"artifact_id": str(artifact.artifact_id), "error_type": type(e).__name__},
        )
        raise DOCXExtractionFailedError("Failed to extract body content from DOCX.") from e

    # 6. Extract footers (deterministic order: footers after body)
    try:
        for s_idx, section in enumerate(doc.sections):
            # Default footer
            if section.footer and not section.footer.is_linked_to_previous:
                f_blocks, current_seq, total_chars, f_draw, f_img = _extract_header_footer_text(
                    hf_obj=section.footer,
                    location_prefix=f"section/{s_idx}/footer/default",
                    start_seq=current_seq,
                    block_type=BlockType.FOOTER,
                    seen_part_ids=seen_footer_part_ids,
                    max_chars=max_chars,
                    current_char_count=total_chars,
                )
                all_blocks.extend(f_blocks)
                footer_block_count += len(f_blocks)
                if f_draw:
                    unsupported_drawing_or_textbox_count += 1
                    doc_review_required = True
                if f_img:
                    has_visual_content = True

            # First page footer
            if section.different_first_page_header_footer and hasattr(section, "first_page_footer"):
                fpf = section.first_page_footer
                if fpf and not fpf.is_linked_to_previous:
                    f_blocks, current_seq, total_chars, f_draw, f_img = _extract_header_footer_text(
                        hf_obj=fpf,
                        location_prefix=f"section/{s_idx}/footer/first_page",
                        start_seq=current_seq,
                        block_type=BlockType.FOOTER,
                        seen_part_ids=seen_footer_part_ids,
                        max_chars=max_chars,
                        current_char_count=total_chars,
                    )
                    all_blocks.extend(f_blocks)
                    footer_block_count += len(f_blocks)
                    if f_draw:
                        unsupported_drawing_or_textbox_count += 1
                        doc_review_required = True
                    if f_img:
                        has_visual_content = True
    except DOCXExtractionError:
        raise
    except Exception as e:
        logger.warning(
            "Error extracting footers from DOCX",
            extra={"artifact_id": str(artifact.artifact_id), "error_type": type(e).__name__},
        )
        raise DOCXExtractionFailedError("Failed to extract document footer.") from e

    quality_facts = DOCXExtractionQualityFacts(
        paragraph_count=paragraph_count,
        table_cell_count=table_cell_count,
        header_block_count=header_block_count,
        footer_block_count=footer_block_count,
        unsupported_drawing_or_textbox_count=unsupported_drawing_or_textbox_count,
        unsupported_nested_table_count=unsupported_nested_table_count,
        unreliable_tracked_change_count=unreliable_tracked_change_count,
        has_visual_content=has_visual_content,
    )

    # Empty or image-only DOCX handling
    if not all_blocks:
        logger.info(
            "DOCX document contains no usable text",
            extra={"artifact_id": str(artifact.artifact_id)},
        )
        raise DOCXTextUnavailableError("No native text could be extracted from the DOCX document.")

    shared_extraction_result = ExtractionResult(
        extractor_version=DOCX_EXTRACTOR_VERSION,
        review_required=doc_review_required,
        blocks=all_blocks,
    )

    return DOCXExtractionOutcome(
        extractor_version=DOCX_EXTRACTOR_VERSION,
        blocks=all_blocks,
        quality_facts=quality_facts,
        review_required=doc_review_required,
        total_character_count=total_chars,
        extraction_result=shared_extraction_result,
    )
