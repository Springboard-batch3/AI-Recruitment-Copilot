import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import get_settings
from app.models.ats import ATSConnectionRecord, ATSHealthStatus
from app.services.ats.adapter_interface import (
    AdapterHealth,
    ExternalAttachment,
    ExternalCandidate,
    ExternalJob,
    PushNoteResult,
)
from app.services.docx_validation import DOCXValidationError, validate_docx_file
from app.services.pdf_validation import PDFValidationError, validate_pdf_file

logger = logging.getLogger("ai_hiring.ats.local_folder_adapter")

CHUNK_SIZE_BYTES = 64 * 1024  # 64 KiB
MAX_FILES_PER_SCAN = 100

# Allowed file extensions
ALLOWED_EXTENSIONS = {".pdf", ".docx"}


@dataclass(frozen=True)
class LocalFolderScanItem:
    external_id: str
    filename: str
    sanitized_filename: str
    relative_path: str
    file_size_bytes: int
    content_hash: str
    format: str
    status: str  # READY, DUPLICATE_CONTENT, ALREADY_IMPORTED, UNSUPPORTED_TYPE, TOO_LARGE, UNSAFE_DOCUMENT, INVALID_FILE, INTERNAL_SCAN_ERROR
    safe_error_message: Optional[str] = None


def sanitize_display_filename(raw_filename: str) -> str:
    """Sanitizes filename for safe UI rendering and log output."""
    base = os.path.basename(raw_filename).strip()
    # Remove control characters and normalize spaces
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", base)
    # Replace dangerous filesystem characters
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", cleaned)
    # Truncate if excessively long
    if len(cleaned) > 200:
        cleaned = cleaned[:196] + "..."
    return cleaned or "unnamed_file"


class LocalFolderATSAdapter:
    """Local Folder ATS Demonstration Adapter.
    
    Inspects a dedicated local folder drop root (local_ats_demo/inbox) in read-only mode,
    validates candidate resumes against Phase 1 PDF/DOCX safety pipelines,
    and supports dry-run preview and explicit confirmation.
    """

    def _resolve_inbox_path(self, connection: Optional[ATSConnectionRecord] = None) -> Tuple[Path, Optional[str]]:
        """Resolves and validates the approved canonical inbox directory within repo root."""
        settings = get_settings()
        root_path = settings.get_ats_local_drop_root()

        # Check if connection has a custom folder config within allowed root
        if connection and connection.encrypted_config:
            custom_folder = connection.encrypted_config.get("folder_path")
            if custom_folder:
                candidate_path = (root_path / custom_folder).resolve()
                # Enforce containment within approved root
                if not str(candidate_path).startswith(str(root_path)):
                    return root_path / "inbox", "Configured folder path escapes approved application drop root."
                inbox_path = candidate_path if candidate_path.name == "inbox" else (candidate_path / "inbox")
                return inbox_path.resolve(), None

        inbox_path = (root_path / "inbox").resolve()
        if not str(inbox_path).startswith(str(root_path)):
            return inbox_path, "Inbox path escapes approved application drop root."

        return inbox_path, None

    def healthcheck(self, connection: ATSConnectionRecord) -> AdapterHealth:
        """Verifies local drop root and inbox accessibility without leaking PII or internal paths."""
        try:
            inbox_path, containment_error = self._resolve_inbox_path(connection)
            if containment_error:
                return AdapterHealth(
                    status=ATSHealthStatus.UNHEALTHY,
                    latency_ms=1.0,
                    message="Configuration error: Path traversal or invalid root detected.",
                )

            root_path = get_settings().get_ats_local_drop_root()
            if not root_path.exists() or not root_path.is_dir():
                return AdapterHealth(
                    status=ATSHealthStatus.UNHEALTHY,
                    latency_ms=1.0,
                    message="Local ATS drop root directory does not exist.",
                )

            if not inbox_path.exists() or not inbox_path.is_dir():
                return AdapterHealth(
                    status=ATSHealthStatus.UNHEALTHY,
                    latency_ms=1.0,
                    message="Local ATS demo inbox directory does not exist.",
                )

            # Check read permission
            if not os.access(inbox_path, os.R_OK):
                return AdapterHealth(
                    status=ATSHealthStatus.UNHEALTHY,
                    latency_ms=1.0,
                    message="Local ATS demo inbox directory is not readable by backend.",
                )

            return AdapterHealth(
                status=ATSHealthStatus.HEALTHY,
                latency_ms=1.5,
                message="Local ATS demo folder drop root and inbox accessible.",
            )
        except Exception as e:
            logger.error("LocalFolderATSAdapter healthcheck failed", extra={"error_type": type(e).__name__})
            return AdapterHealth(
                status=ATSHealthStatus.UNHEALTHY,
                latency_ms=2.0,
                message="Local ATS demo healthcheck encountered an internal error.",
            )

    def scan_inbox(
        self, connection: Optional[ATSConnectionRecord] = None, max_files: int = MAX_FILES_PER_SCAN
    ) -> List[LocalFolderScanItem]:
        """Scans files directly inside inbox, applies validation, and computes stable content hashes."""
        inbox_path, containment_error = self._resolve_inbox_path(connection)
        if containment_error or not inbox_path.exists() or not inbox_path.is_dir():
            logger.warning("LocalFolder scan_inbox failed: inbox path not available")
            return []

        settings = get_settings()
        max_bytes = settings.max_resume_upload_bytes
        items: List[LocalFolderScanItem] = []

        try:
            entries = sorted(os.scandir(inbox_path), key=lambda e: e.name.lower())
        except Exception as e:
            logger.error("Failed to list inbox directory", extra={"error_type": type(e).__name__})
            return []

        for entry in entries:
            if len(items) >= max_files:
                break

            filename = entry.name
            # Skip hidden files, .gitkeep, README files, and temporary/backup files
            if (
                filename.startswith(".")
                or filename.startswith("~")
                or filename.startswith("#")
                or filename.endswith("~")
                or filename.endswith(".tmp")
                or filename.endswith(".bak")
                or filename == ".gitkeep"
                or filename.lower().startswith("readme")
            ):
                continue

            sanitized_name = sanitize_display_filename(filename)

            # 1. Reject symlinks
            try:
                if entry.is_symlink():
                    items.append(
                        LocalFolderScanItem(
                            external_id=f"local-folder:symlink-{sanitized_name}",
                            filename=filename,
                            sanitized_filename=sanitized_name,
                            relative_path=filename,
                            file_size_bytes=0,
                            content_hash="",
                            format="UNKNOWN",
                            status="INVALID_FILE",
                            safe_error_message=f"Symbolic link '{sanitized_name}' rejected for security.",
                        )
                    )
                    continue
            except OSError:
                continue

            # 2. Reject directories, sockets, devices
            try:
                if not entry.is_file():
                    continue
            except OSError:
                continue

            entry_path = Path(entry.path).resolve()
            # Double check containment
            if not str(entry_path).startswith(str(inbox_path)):
                continue

            # 3. Check extension
            ext = entry_path.suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                items.append(
                    LocalFolderScanItem(
                        external_id=f"local-folder:unsupported-{sanitized_name}",
                        filename=filename,
                        sanitized_filename=sanitized_name,
                        relative_path=filename,
                        file_size_bytes=entry.stat().st_size if hasattr(entry, "stat") else 0,
                        content_hash="",
                        format=ext.lstrip(".").upper() or "UNKNOWN",
                        status="UNSUPPORTED_TYPE",
                        safe_error_message=f"Unsupported file format '{ext}'. Only PDF (.pdf) and DOCX (.docx) files are supported.",
                    )
                )
                continue

            # 4. Check file size
            try:
                stat_info = entry.stat()
                file_size = stat_info.st_size
            except OSError as e:
                items.append(
                    LocalFolderScanItem(
                        external_id=f"local-folder:error-{sanitized_name}",
                        filename=filename,
                        sanitized_filename=sanitized_name,
                        relative_path=filename,
                        file_size_bytes=0,
                        content_hash="",
                        format=ext.lstrip(".").upper(),
                        status="INVALID_FILE",
                        safe_error_message=f"Unable to read file metadata for '{sanitized_name}'.",
                    )
                )
                continue

            if file_size == 0:
                items.append(
                    LocalFolderScanItem(
                        external_id=f"local-folder:empty-{sanitized_name}",
                        filename=filename,
                        sanitized_filename=sanitized_name,
                        relative_path=filename,
                        file_size_bytes=0,
                        content_hash="",
                        format=ext.lstrip(".").upper(),
                        status="INVALID_FILE",
                        safe_error_message=f"File '{sanitized_name}' is empty.",
                    )
                )
                continue

            if file_size > max_bytes:
                items.append(
                    LocalFolderScanItem(
                        external_id=f"local-folder:oversize-{sanitized_name}",
                        filename=filename,
                        sanitized_filename=sanitized_name,
                        relative_path=filename,
                        file_size_bytes=file_size,
                        content_hash="",
                        format=ext.lstrip(".").upper(),
                        status="TOO_LARGE",
                        safe_error_message=f"File '{sanitized_name}' exceeds maximum upload size of {max_bytes // (1024*1024)} MB.",
                    )
                )
                continue

            # 5. Stream hash computation
            sha256 = hashlib.sha256()
            try:
                with open(entry_path, "rb") as f:
                    while True:
                        chunk = f.read(CHUNK_SIZE_BYTES)
                        if not chunk:
                            break
                        sha256.update(chunk)
                content_hash = sha256.hexdigest().lower()
            except OSError as e:
                items.append(
                    LocalFolderScanItem(
                        external_id=f"local-folder:unreadable-{sanitized_name}",
                        filename=filename,
                        sanitized_filename=sanitized_name,
                        relative_path=filename,
                        file_size_bytes=file_size,
                        content_hash="",
                        format=ext.lstrip(".").upper(),
                        status="INVALID_FILE",
                        safe_error_message=f"Failed to read document '{sanitized_name}'.",
                    )
                )
                continue

            external_id = f"local-folder:{content_hash}"

            # 6. Run Phase 1 Safety and Structure Validation
            try:
                if ext == ".pdf":
                    validate_pdf_file(entry_path, max_pages=settings.max_resume_pages)
                elif ext == ".docx":
                    validate_docx_file(
                        file_path=entry_path,
                        max_docx_expanded_bytes=settings.max_docx_expanded_bytes,
                        max_docx_archive_entries=settings.max_docx_archive_entries,
                        max_docx_compression_ratio=settings.max_docx_compression_ratio,
                    )

                items.append(
                    LocalFolderScanItem(
                        external_id=external_id,
                        filename=filename,
                        sanitized_filename=sanitized_name,
                        relative_path=filename,
                        file_size_bytes=file_size,
                        content_hash=content_hash,
                        format="PDF" if ext == ".pdf" else "DOCX",
                        status="READY",
                        safe_error_message=f"Valid {'PDF' if ext == '.pdf' else 'DOCX'} document",
                    )
                )
            except PDFValidationError as e:
                items.append(
                    LocalFolderScanItem(
                        external_id=external_id,
                        filename=filename,
                        sanitized_filename=sanitized_name,
                        relative_path=filename,
                        file_size_bytes=file_size,
                        content_hash=content_hash,
                        format="PDF",
                        status="UNSAFE_DOCUMENT" if "unsafe" in e.code.lower() else "INVALID_FILE",
                        safe_error_message=f"PDF validation failed: {e.message}",
                    )
                )
            except DOCXValidationError as e:
                items.append(
                    LocalFolderScanItem(
                        external_id=external_id,
                        filename=filename,
                        sanitized_filename=sanitized_name,
                        relative_path=filename,
                        file_size_bytes=file_size,
                        content_hash=content_hash,
                        format="DOCX",
                        status="UNSAFE_DOCUMENT" if "unsafe" in e.code.lower() else "INVALID_FILE",
                        safe_error_message=f"DOCX validation failed: {e.message}",
                    )
                )
            except Exception as e:
                logger.error("Validator exception for local file", extra={"doc_name": sanitized_name, "error_type": type(e).__name__})
                items.append(
                    LocalFolderScanItem(
                        external_id=external_id,
                        filename=filename,
                        sanitized_filename=sanitized_name,
                        relative_path=filename,
                        file_size_bytes=file_size,
                        content_hash=content_hash,
                        format=ext.lstrip(".").upper(),
                        status="INTERNAL_SCAN_ERROR",
                        safe_error_message=f"Validation failed for document '{sanitized_name}'.",
                    )
                )

        # Sort deterministically by (sanitized_filename, content_hash)
        items.sort(key=lambda x: (x.sanitized_filename.lower(), x.content_hash))
        return items

    def list_jobs(
        self, connection: ATSConnectionRecord, cursor_token: Optional[str] = None, page: int = 1
    ) -> Tuple[List[ExternalJob], Optional[str], bool]:
        """Local folder demo does not provide jobs; destination job is chosen by recruiter."""
        return [], None, False

    def list_candidates(
        self, connection: ATSConnectionRecord, cursor_token: Optional[str] = None, page: int = 1
    ) -> Tuple[List[ExternalCandidate], Optional[str], bool]:
        """Lists candidate representations generated from inbox scan items."""
        scan_items = self.scan_inbox(connection)
        candidates: List[ExternalCandidate] = []
        for item in scan_items:
            if item.status == "READY":
                cand_name = Path(item.filename).stem.replace("_", " ").replace("-", " ").title()
                candidates.append(
                    ExternalCandidate(
                        external_id=item.external_id,
                        full_name=cand_name,
                        raw_payload={
                            "filename": item.sanitized_filename,
                            "file_size_bytes": item.file_size_bytes,
                            "sha256": item.content_hash,
                            "format": item.format,
                        },
                    )
                )
        return candidates, None, False

    def list_attachments(
        self, connection: ATSConnectionRecord, candidate_id: Optional[str] = None, page: int = 1
    ) -> Tuple[List[ExternalAttachment], Optional[str], bool]:
        """Lists attachment representations from inbox scan items."""
        scan_items = self.scan_inbox(connection)
        attachments: List[ExternalAttachment] = []
        for item in scan_items:
            if candidate_id and item.external_id != candidate_id:
                continue
            if item.status == "READY":
                mime = "application/pdf" if item.format == "PDF" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                attachments.append(
                    ExternalAttachment(
                        attachment_id=item.external_id,
                        candidate_id=item.external_id,
                        filename=item.sanitized_filename,
                        content_type=mime,
                        file_size_bytes=item.file_size_bytes,
                        raw_payload={"relative_path": item.relative_path, "sha256": item.content_hash},
                    )
                )
        return attachments, None, False

    def download_attachment(
        self, connection: ATSConnectionRecord, attachment_id: str
    ) -> Optional[bytes]:
        """Reads file bytes safely from inbox by attachment external ID or filename."""
        inbox_path, containment_error = self._resolve_inbox_path(connection)
        if containment_error or not inbox_path.exists():
            return None

        # Extract target hash if attachment_id has format 'local-folder:<sha256>'
        target_hash = attachment_id.removeprefix("local-folder:") if attachment_id.startswith("local-folder:") else None

        for entry in os.scandir(inbox_path):
            if not entry.is_file() or entry.is_symlink():
                continue
            entry_path = Path(entry.path).resolve()
            if not str(entry_path).startswith(str(inbox_path)):
                continue

            # Match by hash or filename
            if target_hash:
                sha256 = hashlib.sha256()
                with open(entry_path, "rb") as f:
                    while True:
                        chunk = f.read(CHUNK_SIZE_BYTES)
                        if not chunk:
                            break
                        sha256.update(chunk)
                if sha256.hexdigest().lower() == target_hash:
                    with open(entry_path, "rb") as f:
                        return f.read()
            elif entry.name == attachment_id:
                with open(entry_path, "rb") as f:
                    return f.read()

        return None

    def push_note(
        self, connection: ATSConnectionRecord, external_candidate_id: str, note_text: str, preview_hash: str
    ) -> PushNoteResult:
        """Vendor note write is not supported for Local Folder Demo adapter."""
        return PushNoteResult(
            success=False,
            error_message="Vendor note export is not supported for the Local ATS Demo adapter.",
        )
