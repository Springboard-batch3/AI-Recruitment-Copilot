from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple

from app.models.ats import ATSConnectionRecord, ATSHealthStatus


@dataclass(frozen=True)
class ExternalJob:
    external_id: str
    title: str
    department: Optional[str] = None
    location: Optional[str] = None
    status: Optional[str] = "ACTIVE"
    raw_payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExternalCandidate:
    external_id: str
    full_name: str
    application_id: Optional[str] = None
    job_id: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    stage: Optional[str] = None
    raw_payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExternalAttachment:
    attachment_id: str
    candidate_id: str
    filename: str
    content_type: str
    file_size_bytes: int
    download_url: Optional[str] = None
    content_bytes: Optional[bytes] = None
    raw_payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterHealth:
    status: ATSHealthStatus
    latency_ms: float
    message: str


@dataclass(frozen=True)
class PushNoteResult:
    success: bool
    external_note_id: Optional[str] = None
    receipt: Dict[str, Any] = field(default_factory=dict)
    is_timeout: bool = False
    error_message: Optional[str] = None


class ATSAdapter(Protocol):
    def healthcheck(self, connection: ATSConnectionRecord) -> AdapterHealth:
        ...

    def list_jobs(
        self, connection: ATSConnectionRecord, cursor_token: Optional[str] = None, page: int = 1
    ) -> Tuple[List[ExternalJob], Optional[str], bool]:
        ...

    def list_candidates(
        self, connection: ATSConnectionRecord, cursor_token: Optional[str] = None, page: int = 1
    ) -> Tuple[List[ExternalCandidate], Optional[str], bool]:
        ...

    def list_attachments(
        self, connection: ATSConnectionRecord, candidate_id: Optional[str] = None, page: int = 1
    ) -> Tuple[List[ExternalAttachment], Optional[str], bool]:
        ...

    def download_attachment(
        self, connection: ATSConnectionRecord, attachment_id: str
    ) -> Optional[bytes]:
        ...

    def push_note(
        self, connection: ATSConnectionRecord, external_candidate_id: str, note_text: str, preview_hash: str
    ) -> PushNoteResult:
        ...
