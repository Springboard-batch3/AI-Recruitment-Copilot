import dataclasses
import logging
from pathlib import Path
from typing import Any, Optional, Set
import pypdf
from pypdf.errors import EmptyFileError, PdfReadError, PyPdfError

from app.models.resume import ArtifactFormat

logger = logging.getLogger("ai_hiring.services.pdf_validation")

DISALLOWED_ACTION_TYPES = {
    "/javascript",
    "/js",
    "/launch",
    "/submitform",
    "/importdata",
    "/gotor",
    "/gotoe",
    "/rendition",
    "/sound",
    "/movie",
    "/hide",
    "/resetform",
    "/setstate",
}

DISALLOWED_ANNOT_SUBTYPES = {
    "/richmedia",
    "/screen",
    "/sound",
    "/movie",
    "/fileattachment",
    "/3d",
}

DISALLOWED_NAMES_KEYS = {
    "/javascript",
    "/alternatepresenters",
    "/renditions",
}

DISALLOWED_URI_SCHEMES = (
    "javascript:",
    "data:",
    "file:",
    "vbscript:",
    "about:",
)

MAX_TRAVERSAL_DEPTH = 32


class PDFValidationError(Exception):
    """Base exception for controlled PDF validation failures."""

    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class InvalidPDFSignatureError(PDFValidationError):
    def __init__(self, message: str = "Invalid PDF file signature."):
        super().__init__(code="INVALID_PDF_SIGNATURE", message=message, status_code=415)


class MalformedPDFError(PDFValidationError):
    def __init__(self, message: str = "Malformed or unreadable PDF document."):
        super().__init__(code="MALFORMED_PDF", message=message, status_code=422)


class EncryptedPDFError(PDFValidationError):
    def __init__(self, message: str = "Encrypted or password-protected PDF is not accepted."):
        super().__init__(code="ENCRYPTED_PDF", message=message, status_code=422)


class EmptyPDFError(PDFValidationError):
    def __init__(self, message: str = "PDF document contains no pages."):
        super().__init__(code="EMPTY_PDF", message=message, status_code=422)


class PDFPageLimitExceededError(PDFValidationError):
    def __init__(self, message: str = "PDF document exceeds allowed page limit."):
        super().__init__(code="PDF_PAGE_LIMIT_EXCEEDED", message=message, status_code=422)


class UnsafePDFContentError(PDFValidationError):
    def __init__(
        self,
        message: str = "PDF contains unsupported active or embedded content.",
        rule_code: Optional[str] = None,
    ):
        super().__init__(code="UNSAFE_PDF_CONTENT", message=message, status_code=422)
        self.rule_code = rule_code
        if rule_code:
            logger.warning("PDF active-content rejection: %s", rule_code)


@dataclasses.dataclass(frozen=True)
class PDFValidationResult:
    detected_format: ArtifactFormat
    page_count: int


def _resolve_obj(obj: Any) -> Any:
    """Safely dereference indirect PDF objects without recursive loops."""
    if obj is None:
        return None
    if hasattr(obj, "get_object"):
        try:
            return obj.get_object()
        except Exception:
            return obj
    return obj


def _is_safe_c2pa_manifest(filespec_obj: Any) -> bool:
    """Check if a FileSpec object is a standard C2PA Content Credentials provenance manifest."""
    filespec = _resolve_obj(filespec_obj)
    if not isinstance(filespec, dict):
        return False

    af_rel = str(_resolve_obj(filespec.get("/AFRelationship", ""))).lower().strip()
    subtype = str(_resolve_obj(filespec.get("/Subtype", ""))).lower().strip()
    desc = str(_resolve_obj(filespec.get("/Desc", ""))).lower().strip()
    fname = str(_resolve_obj(filespec.get("/F", ""))).lower().strip()
    ufname = str(_resolve_obj(filespec.get("/UF", ""))).lower().strip()

    if af_rel == "/c2pa_manifest" or subtype in ("application/c2pa", "application/vnd.c2pa"):
        return True
    if "content credentials" in (desc, fname, ufname) or "c2pa" in (fname, ufname):
        return True
    return False


def _check_embedded_files_node(node: Any, visited: Set[int], depth: int = 0) -> None:
    """Inspect an EmbeddedFiles name tree node. Rejects any non-C2PA embedded attachments."""
    if depth > MAX_TRAVERSAL_DEPTH:
        raise MalformedPDFError("PDF object nesting depth exceeded.")

    node_dict = _resolve_obj(node)
    if not isinstance(node_dict, dict):
        return

    obj_id = id(node_dict)
    if obj_id in visited:
        return
    visited.add(obj_id)

    # Inspect /Names array: [name_1, filespec_1, name_2, filespec_2, ...]
    names_arr = _resolve_obj(node_dict.get("/Names"))
    if isinstance(names_arr, list):
        for item in names_arr:
            res_item = _resolve_obj(item)
            if isinstance(res_item, dict):
                if not _is_safe_c2pa_manifest(res_item):
                    raise UnsafePDFContentError(rule_code="PDF_REJECT_EMBEDDED_FILE")

    # Inspect /Kids array recursively
    kids_arr = _resolve_obj(node_dict.get("/Kids"))
    if isinstance(kids_arr, list):
        for kid in kids_arr:
            _check_embedded_files_node(kid, visited, depth + 1)


def _check_action_dict(raw_action: Any, visited: Set[int], depth: int = 0) -> None:
    """Inspect an action dictionary for unsafe action types (/S) or embedded scripts."""
    if depth > MAX_TRAVERSAL_DEPTH:
        raise MalformedPDFError("PDF object nesting depth exceeded.")

    action_dict = _resolve_obj(raw_action)
    if not isinstance(action_dict, dict):
        return

    obj_id = id(action_dict)
    if obj_id in visited:
        return
    visited.add(obj_id)

    # 1. Check for direct script keys in action dictionary
    if "/JS" in action_dict or "/JavaScript" in action_dict:
        raise UnsafePDFContentError(rule_code="PDF_REJECT_JS_ACTION")

    # 2. Check action type /S
    s_val = _resolve_obj(action_dict.get("/S"))
    if s_val is not None:
        s_str = str(s_val).lower().strip()
        if s_str in DISALLOWED_ACTION_TYPES:
            raise UnsafePDFContentError(rule_code="PDF_REJECT_DISALLOWED_ACTION")

        # Check URI scheme safety
        if s_str == "/uri":
            uri_val = _resolve_obj(action_dict.get("/URI"))
            if uri_val is not None:
                uri_str = str(uri_val).strip().lower()
                if any(uri_str.startswith(scheme) for scheme in DISALLOWED_URI_SCHEMES):
                    raise UnsafePDFContentError(rule_code="PDF_REJECT_UNSAFE_URI_SCHEME")

    # 3. Check for Launch action keys
    if "/F" in action_dict and str(action_dict.get("/S", "")).lower() == "/launch":
        raise UnsafePDFContentError(rule_code="PDF_REJECT_DISALLOWED_ACTION")

    # 4. If action has /Next sub-actions, inspect them boundedly
    next_action = _resolve_obj(action_dict.get("/Next"))
    if next_action is not None:
        if isinstance(next_action, list):
            for sub_act in next_action:
                _check_action_dict(sub_act, visited, depth + 1)
        elif isinstance(next_action, dict):
            _check_action_dict(next_action, visited, depth + 1)


def _check_names_tree(raw_node: Any, visited: Set[int], depth: int = 0) -> None:
    """Recursively inspect a PDF Names tree for disallowed name trees or embedded files."""
    if depth > MAX_TRAVERSAL_DEPTH:
        raise MalformedPDFError("PDF object nesting depth exceeded.")

    node = _resolve_obj(raw_node)
    if not isinstance(node, dict):
        return

    obj_id = id(node)
    if obj_id in visited:
        return
    visited.add(obj_id)

    for k, v in node.items():
        k_str = str(k).lower().strip()
        if k_str == "/embeddedfiles":
            _check_embedded_files_node(v, visited, depth + 1)
        elif k_str == "/javascript":
            raise UnsafePDFContentError(rule_code="PDF_REJECT_JS_ACTION")
        elif k_str in DISALLOWED_NAMES_KEYS:
            raise UnsafePDFContentError(rule_code="PDF_REJECT_DISALLOWED_ACTION")
        else:
            v_res = _resolve_obj(v)
            if isinstance(v_res, dict):
                _check_names_tree(v_res, visited, depth + 1)
            elif isinstance(v_res, list):
                for item in v_res:
                    item_res = _resolve_obj(item)
                    if isinstance(item_res, dict):
                        _check_names_tree(item_res, visited, depth + 1)


def _inspect_active_and_embedded_content(reader: pypdf.PdfReader) -> None:
    """Inspect known document and page locations for active or embedded content."""
    visited_objects: Set[int] = set()

    try:
        trailer = reader.trailer
        if not trailer or not isinstance(trailer, dict):
            return

        root = _resolve_obj(trailer.get("/Root"))
        if root is None or not isinstance(root, dict):
            return

        # 1. Catalog /OpenAction
        if "/OpenAction" in root:
            open_action = _resolve_obj(root["/OpenAction"])
            if isinstance(open_action, dict):
                _check_action_dict(open_action, visited_objects)
            elif isinstance(open_action, list):
                for item in open_action:
                    resolved_item = _resolve_obj(item)
                    if isinstance(resolved_item, dict):
                        _check_action_dict(resolved_item, visited_objects)

        # 2. Catalog /AA (Additional Actions)
        if "/AA" in root:
            raise UnsafePDFContentError(rule_code="PDF_REJECT_ADDITIONAL_ACTION")

        # 3. Catalog /Names
        if "/Names" in root:
            names_dict = _resolve_obj(root["/Names"])
            _check_names_tree(names_dict, visited_objects)

        # 4. Catalog /AcroForm (XFA or active form scripts)
        if "/AcroForm" in root:
            acro_form = _resolve_obj(root["/AcroForm"])
            if isinstance(acro_form, dict):
                if "/XFA" in acro_form:
                    raise UnsafePDFContentError(rule_code="PDF_REJECT_XFA_FORM")
                fields = _resolve_obj(acro_form.get("/Fields"))
                if isinstance(fields, list):
                    for f_ref in fields:
                        f_obj = _resolve_obj(f_ref)
                        if isinstance(f_obj, dict):
                            if "/AA" in f_obj:
                                raise UnsafePDFContentError(rule_code="PDF_REJECT_ADDITIONAL_ACTION")
                            if "/A" in f_obj:
                                _check_action_dict(f_obj["/A"], visited_objects)

        # 5. Catalog direct embedded file keys (/EmbeddedFiles, /AF)
        if "/EmbeddedFiles" in root:
            _check_embedded_files_node(root["/EmbeddedFiles"], visited_objects)
        if "/AF" in root:
            af_arr = _resolve_obj(root["/AF"])
            if isinstance(af_arr, list):
                for af_item in af_arr:
                    if not _is_safe_c2pa_manifest(af_item):
                        raise UnsafePDFContentError(rule_code="PDF_REJECT_EMBEDDED_FILE")
            elif not _is_safe_c2pa_manifest(af_arr):
                raise UnsafePDFContentError(rule_code="PDF_REJECT_EMBEDDED_FILE")

        # 6. Inspect each page for /AA, /Annots, /Trans
        for page in reader.pages:
            page_obj = _resolve_obj(page)
            if not isinstance(page_obj, dict):
                continue

            # Check Page /AA (Page Open/Close Actions)
            if "/AA" in page_obj:
                raise UnsafePDFContentError(rule_code="PDF_REJECT_ADDITIONAL_ACTION")

            # Check Page /Trans (Page Transition presentation effect)
            if "/Trans" in page_obj:
                trans_obj = _resolve_obj(page_obj["/Trans"])
                if isinstance(trans_obj, dict):
                    if "/JS" in trans_obj or "/JavaScript" in trans_obj or "/AA" in trans_obj:
                        raise UnsafePDFContentError(rule_code="PDF_REJECT_JS_ACTION")

            # Check Page /Annots
            if "/Annots" in page_obj:
                annots = _resolve_obj(page_obj["/Annots"])
                if isinstance(annots, list):
                    for annot_ref in annots:
                        annot = _resolve_obj(annot_ref)
                        if not isinstance(annot, dict):
                            continue

                        # Check annotation subtype safety
                        sub_type = _resolve_obj(annot.get("/Subtype"))
                        if sub_type is not None:
                            sub_str = str(sub_type).lower().strip()
                            if sub_str in DISALLOWED_ANNOT_SUBTYPES:
                                raise UnsafePDFContentError(rule_code="PDF_REJECT_UNSAFE_ANNOT_SUBTYPE")

                        # Check embedded files in annotations (/FS or /EF)
                        if "/FS" in annot or "/EF" in annot:
                            if not _is_safe_c2pa_manifest(annot):
                                raise UnsafePDFContentError(rule_code="PDF_REJECT_EMBEDDED_FILE")

                        # Check /A (Action)
                        if "/A" in annot:
                            action = _resolve_obj(annot["/A"])
                            _check_action_dict(action, visited_objects)

                        # Check /AA (Additional Actions)
                        if "/AA" in annot:
                            raise UnsafePDFContentError(rule_code="PDF_REJECT_ADDITIONAL_ACTION")

    except UnsafePDFContentError:
        raise
    except MalformedPDFError:
        raise
    except (PyPdfError, KeyError, ValueError, TypeError) as e:
        raise MalformedPDFError(f"Failed to inspect PDF catalog structure: {e}") from e


def validate_pdf_file(
    file_path: Path,
    max_pages: int,
) -> PDFValidationResult:
    """Validate PDF header signature, structure, encryption, page limits, and safety.

    Does not extract text, render pages, perform OCR, or modify the document.
    """
    # 1. Header signature check (%PDF-)
    try:
        with open(file_path, "rb") as f:
            header = f.read(5)
    except OSError as e:
        raise MalformedPDFError("Unable to read PDF file header.") from e

    if len(header) < 5 or header != b"%PDF-":
        raise InvalidPDFSignatureError()

    # 2. Defensive PDF parsing with pypdf (strict=False to tolerate benign 1-indexed xrefs)
    try:
        reader = pypdf.PdfReader(str(file_path), strict=False)
    except (EmptyFileError, PdfReadError, PyPdfError, ValueError, KeyError, EOFError, IndexError) as e:
        raise MalformedPDFError() from e
    except Exception as e:
        raise MalformedPDFError() from e

    # 3. Encryption check
    try:
        if reader.is_encrypted:
            raise EncryptedPDFError()
    except EncryptedPDFError:
        raise
    except Exception as e:
        raise MalformedPDFError() from e

    # 4. Page count and resolution
    try:
        page_count = len(reader.pages)
    except (PyPdfError, ValueError, KeyError, EOFError, IndexError) as e:
        raise MalformedPDFError() from e

    if page_count == 0:
        raise EmptyPDFError()

    if page_count > max_pages:
        raise PDFPageLimitExceededError(
            f"PDF exceeds maximum allowed page count of {max_pages} (found {page_count} pages)."
        )

    # 5. Resolve each page dictionary to confirm integrity
    try:
        for page in reader.pages:
            if page is None or not isinstance(page, dict):
                raise MalformedPDFError()
    except MalformedPDFError:
        raise
    except (PyPdfError, ValueError, KeyError, EOFError, IndexError) as e:
        raise MalformedPDFError() from e

    # 6. Active and embedded content inspection
    _inspect_active_and_embedded_content(reader)

    return PDFValidationResult(
        detected_format=ArtifactFormat.pdf,
        page_count=page_count,
    )
