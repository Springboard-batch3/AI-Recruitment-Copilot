import csv
import io
import json
from typing import Any, Dict, List, Optional, Tuple

from app.models.ats import ATSConnectionRecord, ATSHealthStatus
from app.services.ats.adapter_interface import (
    AdapterHealth,
    ExternalAttachment,
    ExternalCandidate,
    ExternalJob,
    PushNoteResult,
)

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_ROW_COUNT = 1000

# Formula injection prefixes
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_formula_injection(val: Optional[str]) -> Optional[str]:
    """Sanitize values to prevent CSV formula injection / spreadsheet execution."""
    if val is None:
        return None
    cleaned = str(val).strip()
    if cleaned.startswith(FORMULA_PREFIXES):
        # Prefix with a safe single quote or strip prefix
        return f"'{cleaned}"
    return cleaned


class CSVJSONAdapter:
    """CSV and JSON Adapter for file-based ATS bulk imports and dry-run validation."""

    def healthcheck(self, connection: ATSConnectionRecord) -> AdapterHealth:
        return AdapterHealth(
            status=ATSHealthStatus.HEALTHY,
            latency_ms=1.0,
            message="CSV/JSON local file processor ready",
        )

    def parse_jobs_from_csv(self, csv_text: str) -> List[ExternalJob]:
        if len(csv_text.encode("utf-8")) > MAX_FILE_SIZE_BYTES:
            raise ValueError("CSV file exceeds maximum allowed size of 10 MB")

        reader = csv.reader(io.StringIO(csv_text))
        try:
            headers = next(reader)
        except StopIteration:
            return []

        # Check duplicate headers
        lower_headers = [h.strip().lower() for h in headers]
        if len(lower_headers) != len(set(lower_headers)):
            raise ValueError("Duplicate column headers detected in CSV file")

        header_map = {h: idx for idx, h in enumerate(lower_headers)}
        if "title" not in header_map:
            raise ValueError("CSV missing required 'title' column for jobs")

        jobs = []
        for row_idx, row in enumerate(reader, start=2):
            if row_idx > MAX_ROW_COUNT:
                break
            if not row or all(not cell.strip() for cell in row):
                continue

            title_idx = header_map["title"]
            title_val = row[title_idx].strip() if title_idx < len(row) else ""
            if not title_val:
                continue

            title = sanitize_formula_injection(title_val) or ""
            ext_id = (
                sanitize_formula_injection(row[header_map["id"]].strip())
                if "id" in header_map and header_map["id"] < len(row) and row[header_map["id"]].strip()
                else f"csv-job-{row_idx}"
            )
            dept = (
                sanitize_formula_injection(row[header_map["department"]].strip())
                if "department" in header_map and header_map["department"] < len(row)
                else None
            )
            loc = (
                sanitize_formula_injection(row[header_map["location"]].strip())
                if "location" in header_map and header_map["location"] < len(row)
                else None
            )

            jobs.append(
                ExternalJob(
                    external_id=ext_id,
                    title=title,
                    department=dept,
                    location=loc,
                    status="ACTIVE",
                    raw_payload={"source_row": row_idx},
                )
            )
        return jobs

    def parse_candidates_from_csv(self, csv_text: str) -> List[ExternalCandidate]:
        if len(csv_text.encode("utf-8")) > MAX_FILE_SIZE_BYTES:
            raise ValueError("CSV file exceeds maximum allowed size of 10 MB")

        reader = csv.reader(io.StringIO(csv_text))
        try:
            headers = next(reader)
        except StopIteration:
            return []

        lower_headers = [h.strip().lower() for h in headers]
        if len(lower_headers) != len(set(lower_headers)):
            raise ValueError("Duplicate column headers detected in CSV file")

        header_map = {h: idx for idx, h in enumerate(lower_headers)}
        if "full_name" not in header_map and "name" not in header_map:
            raise ValueError("CSV missing required 'full_name' or 'name' column for candidates")

        name_key = "full_name" if "full_name" in header_map else "name"
        candidates = []

        for row_idx, row in enumerate(reader, start=2):
            if row_idx > MAX_ROW_COUNT:
                break
            if not row or all(not cell.strip() for cell in row):
                continue

            name_idx = header_map[name_key]
            name_val = row[name_idx].strip() if name_idx < len(row) else ""
            full_name = sanitize_formula_injection(name_val) or ""

            ext_id = (
                sanitize_formula_injection(row[header_map["id"]].strip())
                if "id" in header_map and header_map["id"] < len(row) and row[header_map["id"]].strip()
                else f"csv-cand-{row_idx}"
            )
            email = (
                sanitize_formula_injection(row[header_map["email"]].strip())
                if "email" in header_map and header_map["email"] < len(row)
                else None
            )
            phone = (
                sanitize_formula_injection(row[header_map["phone"]].strip())
                if "phone" in header_map and header_map["phone"] < len(row)
                else None
            )
            stage = (
                sanitize_formula_injection(row[header_map["stage"]].strip())
                if "stage" in header_map and header_map["stage"] < len(row)
                else "Intake"
            )
            job_id = (
                sanitize_formula_injection(row[header_map["job_id"]].strip())
                if "job_id" in header_map and header_map["job_id"] < len(row)
                else None
            )

            candidates.append(
                ExternalCandidate(
                    external_id=ext_id,
                    full_name=full_name,
                    job_id=job_id,
                    email=email,
                    phone=phone,
                    stage=stage,
                    raw_payload={"source_row": row_idx},
                )
            )
        return candidates

    def parse_from_json(self, json_text: str) -> Tuple[List[ExternalJob], List[ExternalCandidate]]:
        if len(json_text.encode("utf-8")) > MAX_FILE_SIZE_BYTES:
            raise ValueError("JSON file exceeds maximum allowed size of 10 MB")

        data = json.loads(json_text)
        jobs = []
        candidates = []

        if isinstance(data, dict):
            raw_jobs = data.get("jobs", [])
            raw_cands = data.get("candidates", [])
        elif isinstance(data, list):
            raw_jobs = []
            raw_cands = data
        else:
            raise ValueError("Invalid JSON structure: Expected object or array")

        for idx, j in enumerate(raw_jobs):
            if idx >= MAX_ROW_COUNT:
                break
            if isinstance(j, dict) and j.get("title"):
                jobs.append(
                    ExternalJob(
                        external_id=str(j.get("id") or f"json-job-{idx+1}"),
                        title=str(j.get("title")),
                        department=j.get("department"),
                        location=j.get("location"),
                        status=j.get("status", "ACTIVE"),
                        raw_payload=j,
                    )
                )

        for idx, c in enumerate(raw_cands):
            if idx >= MAX_ROW_COUNT:
                break
            if isinstance(c, dict):
                name = c.get("full_name") or c.get("name") or ""
                candidates.append(
                    ExternalCandidate(
                        external_id=str(c.get("id") or f"json-cand-{idx+1}"),
                        full_name=str(name),
                        application_id=c.get("application_id"),
                        job_id=c.get("job_id"),
                        email=c.get("email"),
                        phone=c.get("phone"),
                        stage=c.get("stage", "Intake"),
                        raw_payload=c,
                    )
                )

        return jobs, candidates

    def list_jobs(
        self, connection: ATSConnectionRecord, cursor_token: Optional[str] = None, page: int = 1
    ) -> Tuple[List[ExternalJob], Optional[str], bool]:
        # CSV/JSON loads from file payload in import_service
        return [], None, False

    def list_candidates(
        self, connection: ATSConnectionRecord, cursor_token: Optional[str] = None, page: int = 1
    ) -> Tuple[List[ExternalCandidate], Optional[str], bool]:
        return [], None, False

    def list_attachments(
        self, connection: ATSConnectionRecord, candidate_id: Optional[str] = None, page: int = 1
    ) -> Tuple[List[ExternalAttachment], Optional[str], bool]:
        return [], None, False

    def download_attachment(
        self, connection: ATSConnectionRecord, attachment_id: str
    ) -> Optional[bytes]:
        return None

    def push_note(
        self, connection: ATSConnectionRecord, external_candidate_id: str, note_text: str, preview_hash: str
    ) -> PushNoteResult:
        # File adapters do not support pushing notes to remote ATS servers
        return PushNoteResult(
            success=False,
            is_timeout=False,
            error_message="CSV/JSON file connection does not support pushing notes to external server.",
        )
