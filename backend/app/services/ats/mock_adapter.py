import hashlib
import time
from typing import Any, Dict, List, Optional, Tuple

from app.models.ats import ATSConnectionRecord, ATSHealthStatus
from app.services.ats.adapter_interface import (
    AdapterHealth,
    ExternalAttachment,
    ExternalCandidate,
    ExternalJob,
    PushNoteResult,
)


# Minimal valid PDF binary header and trailer
MINIMAL_VALID_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\n2 0 obj\n<<\n/Type /Pages\n/Kids [3 0 R]\n/Count 1\n>>\nendobj\n3 0 obj\n<<\n/Type /Page\n/Parent 2 0 R\n/MediaBox [0 0 612 792]\n/Contents 4 0 R\n>>\nendobj\n4 0 obj\n<<\n/Length 44\n>>\nstream\nBT\n/F1 12 Tf\n100 700 Td\n(Mock Resume Content) Tj\nET\nendstream\nendobj\nxref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000189 00000 n \ntrailer\n<<\n/Size 5\n/Root 1 0 R\n>>\nstartxref\n283\n%%EOF\n"

# Unsafe/invalid attachment bytes (e.g. executable/corrupted)
UNSAFE_ATTACHMENT_BYTES = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00This is an unsafe executable binary"


class MockATSAdapter:
    """Mock ATS Adapter for testing and demonstrations.
    
    Provides deterministic fixtures, simulates pagination, partial errors,
    transient 429s, rate limits, timeouts, and safe attachments.
    """

    def healthcheck(self, connection: ATSConnectionRecord) -> AdapterHealth:
        cfg = connection.encrypted_config or {}
        if cfg.get("simulate_unhealthy"):
            return AdapterHealth(
                status=ATSHealthStatus.UNHEALTHY,
                latency_ms=120.5,
                message="Mock connection simulation: Service unreachable",
            )
        if cfg.get("simulate_degraded"):
            return AdapterHealth(
                status=ATSHealthStatus.DEGRADED,
                latency_ms=450.0,
                message="Mock connection simulation: High latency",
            )
        return AdapterHealth(
            status=ATSHealthStatus.HEALTHY,
            latency_ms=15.2,
            message="Mock ATS connection operational",
        )

    def list_jobs(
        self, connection: ATSConnectionRecord, cursor_token: Optional[str] = None, page: int = 1
    ) -> Tuple[List[ExternalJob], Optional[str], bool]:
        cfg = connection.encrypted_config or {}
        if cfg.get("simulate_429"):
            raise RuntimeError("429 Too Many Requests: Mock rate limit triggered")
        if cfg.get("simulate_permanent_error"):
            raise ValueError("500 Internal Error: Mock permanent failure")

        # Deterministic 2 pages of jobs
        if page == 1:
            jobs = [
                ExternalJob(
                    external_id="mock-job-001",
                    title="Staff Distributed Systems Engineer",
                    department="Infrastructure",
                    location="San Francisco, CA",
                    status="ACTIVE",
                    raw_payload={"requisition_id": "REQ-001", "headcount": 1},
                ),
                ExternalJob(
                    external_id="mock-job-002",
                    title="Senior Backend Python Developer",
                    department="Core Product",
                    location="Remote, US",
                    status="ACTIVE",
                    raw_payload={"requisition_id": "REQ-002", "headcount": 2},
                ),
            ]
            return jobs, "cursor-job-page-2", True
        elif page == 2:
            jobs = [
                ExternalJob(
                    external_id="mock-job-003",
                    title="Junior API Platform Developer",
                    department="Platform",
                    location="New York, NY",
                    status="ACTIVE",
                    raw_payload={"requisition_id": "REQ-003", "headcount": 1},
                ),
            ]
            return jobs, None, False
        return [], None, False

    def list_candidates(
        self, connection: ATSConnectionRecord, cursor_token: Optional[str] = None, page: int = 1
    ) -> Tuple[List[ExternalCandidate], Optional[str], bool]:
        cfg = connection.encrypted_config or {}
        if cfg.get("simulate_429"):
            raise RuntimeError("429 Too Many Requests: Mock rate limit triggered")

        if page == 1:
            candidates = [
                ExternalCandidate(
                    external_id="mock-cand-001",
                    application_id="app-101",
                    job_id="mock-job-001",
                    full_name="Alex Mercer",
                    email="alex.mercer@example.com",
                    phone="+1-555-0199",
                    stage="Reviewing",
                    raw_payload={"source": "LinkedIn", "years_exp": 8},
                ),
                ExternalCandidate(
                    external_id="mock-cand-002",
                    application_id="app-102",
                    job_id="mock-job-002",
                    full_name="Jordan Lee",
                    email="jordan.lee@example.com",
                    phone="+1-555-0188",
                    stage="Screening",
                    raw_payload={"source": "Direct", "years_exp": 5},
                ),
                # Invalid candidate (empty name) to test per-row validation
                ExternalCandidate(
                    external_id="mock-cand-invalid-003",
                    application_id="app-103",
                    job_id="mock-job-002",
                    full_name="",  # Invalid empty name
                    email="invalid@example.com",
                    stage="Screening",
                ),
            ]
            return candidates, "cursor-cand-page-2", True
        elif page == 2:
            candidates = [
                ExternalCandidate(
                    external_id="mock-cand-004",
                    application_id="app-104",
                    job_id="mock-job-003",
                    full_name="Taylor Swift",
                    email="taylor.swift@example.com",
                    phone="+1-555-0177",
                    stage="Interview",
                    raw_payload={"source": "Referral", "years_exp": 6},
                ),
            ]
            return candidates, None, False
        return [], None, False

    def list_attachments(
        self, connection: ATSConnectionRecord, candidate_id: Optional[str] = None, page: int = 1
    ) -> Tuple[List[ExternalAttachment], Optional[str], bool]:
        if page > 1:
            return [], None, False

        attachments = [
            ExternalAttachment(
                attachment_id="mock-att-001",
                candidate_id="mock-cand-001",
                filename="alex_mercer_resume.pdf",
                content_type="application/pdf",
                file_size_bytes=len(MINIMAL_VALID_PDF_BYTES),
                content_bytes=MINIMAL_VALID_PDF_BYTES,
                raw_payload={"is_resume": True},
            ),
            ExternalAttachment(
                attachment_id="mock-att-002",
                candidate_id="mock-cand-002",
                filename="jordan_lee_resume.pdf",
                content_type="application/pdf",
                file_size_bytes=len(MINIMAL_VALID_PDF_BYTES),
                content_bytes=MINIMAL_VALID_PDF_BYTES,
                raw_payload={"is_resume": True},
            ),
            # Unsafe attachment for testing rejection
            ExternalAttachment(
                attachment_id="mock-att-unsafe-003",
                candidate_id="mock-cand-004",
                filename="malicious_payload.exe",
                content_type="application/x-msdownload",
                file_size_bytes=len(UNSAFE_ATTACHMENT_BYTES),
                content_bytes=UNSAFE_ATTACHMENT_BYTES,
                raw_payload={"is_resume": True},
            ),
        ]

        if candidate_id:
            filtered = [a for a in attachments if a.candidate_id == candidate_id]
            return filtered, None, False

        return attachments, None, False

    def download_attachment(
        self, connection: ATSConnectionRecord, attachment_id: str
    ) -> Optional[bytes]:
        if attachment_id in ("mock-att-001", "mock-att-002"):
            return MINIMAL_VALID_PDF_BYTES
        elif attachment_id == "mock-att-unsafe-003":
            return UNSAFE_ATTACHMENT_BYTES
        return None

    def push_note(
        self, connection: ATSConnectionRecord, external_candidate_id: str, note_text: str, preview_hash: str
    ) -> PushNoteResult:
        cfg = connection.encrypted_config or {}

        if cfg.get("simulate_timeout"):
            # Timeout after a possible write -> triggers RECONCILIATION_REQUIRED
            return PushNoteResult(
                success=False,
                is_timeout=True,
                error_message="Gateway timeout connecting to ATS vendor note API",
            )

        if cfg.get("simulate_push_failure"):
            return PushNoteResult(
                success=False,
                is_timeout=False,
                error_message="ATS candidate record is locked or archived",
            )

        # Deterministic note ID
        note_id = f"mock-note-{hashlib.sha256(preview_hash.encode()).hexdigest()[:12]}"
        return PushNoteResult(
            success=True,
            external_note_id=note_id,
            receipt={
                "external_note_id": note_id,
                "external_candidate_id": external_candidate_id,
                "preview_hash": preview_hash,
                "pushed_at": "2026-09-18T12:00:00Z",
                "vendor_status": "201 Created",
            },
        )
