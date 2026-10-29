import dataclasses
import stat
import urllib.parse
import zipfile
from pathlib import Path
from typing import Optional, Set
import defusedxml.ElementTree as ET

from app.models.resume import ArtifactFormat

# Required package part entry paths in normalized form (forward slashes, no leading slash)
REQUIRED_PARTS: Set[str] = {
    "[content_types].xml",
    "_rels/.rels",
    "word/document.xml",
}

# XML namespaces used in Open XML packages
XML_NS_CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
XML_NS_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"

# Main document Content-Type required for standard non-macro DOCX
ALLOWED_DOCUMENT_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
}

# Forbidden macro / VBA content types
FORBIDDEN_CONTENT_TYPES = {
    "application/vnd.ms-word.document.macroenabled.main+xml",
    "application/vnd.ms-word.template.macroenabled.main+xml",
    "application/vnd.ms-office.vba/project",
    "application/vnd.ms-office.vbaproject",
}

# Forbidden entry prefixes / normalized names
FORBIDDEN_ENTRY_EXACT = {
    "word/vbaproject.bin",
    "word/vbadata.xml",
}

FORBIDDEN_ENTRY_PREFIXES = (
    "word/activex/",
    "word/embeddings/",
    "customui/",
)

# Relationship types that are forbidden or represent external execution/template risks
FORBIDDEN_REL_TYPE_SUBSTRINGS = (
    "attachedtemplate",
    "oleobject",
    "embeddedpackage",
    "alternatecontent",
    "subdocument",
)

ALLOWED_EXTERNAL_SCHEMES = {"http", "https", "mailto"}


class DOCXValidationError(Exception):
    """Base exception for controlled DOCX validation failures."""

    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class InvalidDOCXSignatureError(DOCXValidationError):
    def __init__(self, message: str = "Invalid DOCX file signature."):
        super().__init__(code="INVALID_DOCX_SIGNATURE", message=message, status_code=415)


class MalformedDOCXError(DOCXValidationError):
    def __init__(self, message: str = "Malformed or corrupted DOCX archive."):
        super().__init__(code="MALFORMED_DOCX", message=message, status_code=422)


class InvalidDOCXStructureError(DOCXValidationError):
    def __init__(self, message: str = "Invalid DOCX package structure."):
        super().__init__(code="INVALID_DOCX_STRUCTURE", message=message, status_code=422)


class EncryptedDOCXError(DOCXValidationError):
    def __init__(self, message: str = "Encrypted or password-protected DOCX is not accepted."):
        super().__init__(code="ENCRYPTED_DOCX", message=message, status_code=422)


class DOCXArchiveLimitExceededError(DOCXValidationError):
    def __init__(self, message: str = "DOCX archive exceeds safe expansion or entry limits."):
        super().__init__(code="DOCX_ARCHIVE_LIMIT_EXCEEDED", message=message, status_code=422)


class UnsafeDOCXContentError(DOCXValidationError):
    def __init__(self, message: str = "DOCX contains unsupported or unsafe package content."):
        super().__init__(code="UNSAFE_DOCX_CONTENT", message=message, status_code=422)


@dataclasses.dataclass(frozen=True)
class DOCXValidationResult:
    detected_format: ArtifactFormat
    entry_count: int
    total_expanded_bytes: int


def _is_symlink_entry(info: zipfile.ZipInfo) -> bool:
    """Check whether a ZipInfo entry has Unix symbolic link attribute bits set."""
    # High 16 bits of external_attr represent UNIX mode
    mode = info.external_attr >> 16
    return bool(stat.S_ISLNK(mode))


def _validate_relationships_xml(xml_bytes: bytes) -> None:
    """Safely parse relationship XML and check for dangerous targets or schemes."""
    try:
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        raise MalformedDOCXError("Corrupted relationship XML.") from e

    for elem in root.iter():
        # Match tag ending in 'Relationship' ignoring namespace
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag.lower() != "relationship":
            continue

        rel_type = elem.attrib.get("Type", "")
        target = elem.attrib.get("Target", "")
        target_mode = elem.attrib.get("TargetMode", "")

        rel_type_lower = rel_type.lower()
        for forbidden_substr in FORBIDDEN_REL_TYPE_SUBSTRINGS:
            if forbidden_substr in rel_type_lower:
                raise UnsafeDOCXContentError()

        # Check external relationships
        is_external = target_mode.lower() == "external"

        # Check URI scheme if target is URI or if TargetMode is External
        parsed = urllib.parse.urlparse(target)
        scheme = parsed.scheme.lower() if parsed.scheme else ""

        if is_external:
            if "hyperlink" not in rel_type_lower:
                # Disallow external targets for non-hyperlink relationships
                raise UnsafeDOCXContentError()

            if scheme not in ALLOWED_EXTERNAL_SCHEMES:
                raise UnsafeDOCXContentError()

        if scheme:
            if scheme not in ALLOWED_EXTERNAL_SCHEMES:
                raise UnsafeDOCXContentError()


def _validate_content_types_xml(xml_bytes: bytes) -> None:
    """Safely parse [Content_Types].xml and enforce allowed / non-macro types."""
    try:
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        raise MalformedDOCXError("Corrupted Content_Types XML.") from e

    found_main_doc = False

    for elem in root.iter():
        content_type = elem.attrib.get("ContentType", "").strip().lower()
        part_name = elem.attrib.get("PartName", "").strip().lower()

        if not content_type:
            continue

        # Check for forbidden macro content types
        if content_type in FORBIDDEN_CONTENT_TYPES:
            raise UnsafeDOCXContentError()

        if "vba" in content_type or "macroenabled" in content_type:
            raise UnsafeDOCXContentError()

        if content_type in ALLOWED_DOCUMENT_CONTENT_TYPES:
            found_main_doc = True

    if not found_main_doc:
        raise InvalidDOCXStructureError("Missing main document content type.")


def validate_docx_file(
    file_path: Path,
    max_docx_expanded_bytes: int = 52428800,
    max_docx_archive_entries: int = 1000,
    max_docx_compression_ratio: int = 100,
) -> DOCXValidationResult:
    """Validate internal ZIP/Open XML structure, bounds, and safety of a DOCX file.

    Note on DOCX Page Count:
    DOCX pagination is dynamic and depends on fonts, layout engine, margins, and rendering.
    Metadata page-counts in docProps/app.xml are frequently missing or stale and are
    deliberately not used for page limit enforcement.
    """
    # 1. Signature check: standard ZIP local-file header PK\x03\x04
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)
    except OSError as e:
        raise MalformedDOCXError("Unable to read DOCX file header.") from e

    # Reject OLE Compound Document signature (\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1)
    if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise InvalidDOCXSignatureError("OLE compound document format is not supported.")

    if len(header) < 4 or header[:4] != b"PK\x03\x04":
        raise InvalidDOCXSignatureError("Invalid DOCX/ZIP signature.")

    # 2. Open ZIP archive
    try:
        zf = zipfile.ZipFile(file_path, "r")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError, ValueError) as e:
        raise MalformedDOCXError() from e
    except Exception as e:
        raise MalformedDOCXError() from e

    with zf:
        # 3. Read info list
        try:
            entries = zf.infolist()
        except Exception as e:
            raise MalformedDOCXError() from e

        # Entry count limit
        if len(entries) > max_docx_archive_entries:
            raise DOCXArchiveLimitExceededError()

        if len(entries) == 0:
            raise InvalidDOCXStructureError("Empty ZIP archive.")

        seen_normalized_names: Set[str] = set()
        total_expanded_bytes = 0
        total_compressed_bytes = 0

        # 4. Metadata preflight
        for info in entries:
            raw_filename = info.filename

            # Encrypted member check (flag_bits bit 0: 0x1)
            if info.flag_bits & 0x1:
                raise EncryptedDOCXError()

            # Symbolic link check
            if _is_symlink_entry(info):
                raise MalformedDOCXError("Symbolic links are not supported.")

            # Path traversal / malformed path checks
            if "\x00" in raw_filename:
                raise MalformedDOCXError("Archive entry contains NUL byte.")

            # Normalize path separators
            normalized_path = raw_filename.replace("\\", "/")

            if normalized_path.startswith("/"):
                raise MalformedDOCXError("Absolute paths are not supported.")

            # Check Windows drive paths e.g. C:
            if len(normalized_path) >= 2 and normalized_path[1] == ":" and normalized_path[0].isalpha():
                raise MalformedDOCXError("Drive letters are not supported.")

            segments = [s for s in normalized_path.split("/") if s]
            if ".." in segments:
                raise MalformedDOCXError("Directory traversal is not supported.")

            # Check for duplicate normalized names (case-insensitive)
            norm_key = "/".join(segments).lower()
            if norm_key in seen_normalized_names:
                raise MalformedDOCXError("Duplicate archive entries detected.")
            seen_normalized_names.add(norm_key)

            # Check for unsafe entry names / prefixes
            if norm_key in FORBIDDEN_ENTRY_EXACT:
                raise UnsafeDOCXContentError()

            for prefix in FORBIDDEN_ENTRY_PREFIXES:
                if norm_key.startswith(prefix):
                    raise UnsafeDOCXContentError()

            # Resource bounds per-entry
            uncompressed_size = info.file_size
            compressed_size = info.compress_size

            if uncompressed_size < 0 or compressed_size < 0:
                raise MalformedDOCXError("Invalid entry size declaration.")

            if uncompressed_size > max_docx_expanded_bytes:
                raise DOCXArchiveLimitExceededError()

            total_expanded_bytes += uncompressed_size
            total_compressed_bytes += compressed_size

            if total_expanded_bytes > max_docx_expanded_bytes:
                raise DOCXArchiveLimitExceededError()

            # Per-entry compression ratio check (for non-trivial sizes)
            if compressed_size > 0:
                ratio = uncompressed_size / compressed_size
                if ratio > max_docx_compression_ratio:
                    raise DOCXArchiveLimitExceededError()
            elif uncompressed_size > 1024:
                # Compressed size is 0 but uncompressed is substantial
                raise DOCXArchiveLimitExceededError()

        # Total compression ratio check
        if total_compressed_bytes > 0:
            total_ratio = total_expanded_bytes / total_compressed_bytes
            if total_ratio > max_docx_compression_ratio:
                raise DOCXArchiveLimitExceededError()

        # 5. Archive integrity / CRC check after metadata limits pass
        try:
            bad_crc_file = zf.testzip()
            if bad_crc_file is not None:
                raise MalformedDOCXError("CRC integrity check failed.")
        except DOCXValidationError:
            raise
        except Exception as e:
            raise MalformedDOCXError() from e

        # 6. Verify required Open XML package parts
        for req_part in REQUIRED_PARTS:
            if req_part not in seen_normalized_names:
                raise InvalidDOCXStructureError(f"Missing required package part: {req_part}")

        # 7. Parse [Content_Types].xml safely
        try:
            content_types_info = next(i for i in entries if i.filename.replace("\\", "/").strip("/").lower() == "[content_types].xml")
            content_types_xml = zf.read(content_types_info)
        except Exception as e:
            raise MalformedDOCXError() from e

        _validate_content_types_xml(content_types_xml)

        # 8. Parse package and word relationship files safely
        for info in entries:
            norm_name = info.filename.replace("\\", "/").strip("/").lower()
            if norm_name.endswith(".rels") or norm_name == "_rels/.rels" or "/_rels/" in norm_name:
                try:
                    rel_xml = zf.read(info)
                except Exception as e:
                    raise MalformedDOCXError() from e
                _validate_relationships_xml(rel_xml)

    return DOCXValidationResult(
        detected_format=ArtifactFormat.docx,
        entry_count=len(entries),
        total_expanded_bytes=total_expanded_bytes,
    )
