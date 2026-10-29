import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models.auth import UserRecord, UserRole
from app.models.dashboard import DashboardTicketRecord
from app.services.candidate_access_service import DEFAULT_ORG_ID
from app.services.security import generate_secure_token, hash_token

logger = logging.getLogger("ai_hiring.services.dashboard_session")

DASHBOARD_TOKEN_PURPOSE = "RECRUITER_STREAMLIT_ANALYTICS"


class DashboardAuthenticatedContext:
    def __init__(self, user_id: uuid.UUID, organization_id: uuid.UUID, role: str):
        self.user_id = user_id
        self.organization_id = organization_id
        self.role = role


def create_dashboard_ticket(
    db: Session,
    user: UserRecord,
    settings: Settings,
    organization_id: Optional[uuid.UUID] = None,
) -> Tuple[str, str, int]:
    """
    Creates a single-use, short-lived dashboard launch ticket.
    Stores only the SHA-256 hash in `dashboard_tickets`.
    Returns (raw_ticket, launch_url, expires_in_seconds).
    """
    if user.role not in {UserRole.ADMIN, UserRole.RECRUITER}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Candidates cannot access analytics dashboard."},
        )

    raw_ticket = generate_secure_token()
    ticket_digest = hash_token(raw_ticket)

    org_id = organization_id or DEFAULT_ORG_ID
    now = datetime.now(timezone.utc)
    ttl_seconds = settings.dashboard_ticket_ttl_seconds
    expires_at = now + timedelta(seconds=ttl_seconds)

    ticket_record = DashboardTicketRecord(
        id=uuid.uuid4(),
        ticket_hash=ticket_digest,
        user_id=user.user_id,
        organization_id=org_id,
        role=user.role.value,
        expires_at=expires_at,
        consumed_at=None,
        created_at=now,
    )
    db.add(ticket_record)
    db.commit()

    base_url = settings.dashboard_public_url.rstrip("/")
    launch_url = f"{base_url}/?ticket={raw_ticket}"

    return raw_ticket, launch_url, ttl_seconds


def exchange_dashboard_ticket(
    db: Session,
    raw_ticket: str,
    settings: Settings,
) -> Tuple[str, int, uuid.UUID, str, uuid.UUID]:
    """
    Atomically verifies and marks consumed a one-time launch ticket.
    Returns (dashboard_token, expires_in_seconds, user_id, role, organization_id).
    """
    if not raw_ticket or not isinstance(raw_ticket, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TICKET", "message": "Invalid or missing launch ticket."},
        )

    ticket_digest = hash_token(raw_ticket.strip())
    now = datetime.now(timezone.utc)

    # Atomic lookup
    stmt = select(DashboardTicketRecord).where(DashboardTicketRecord.ticket_hash == ticket_digest)
    ticket_rec = db.execute(stmt).scalar_one_or_none()

    if ticket_rec is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TICKET", "message": "Ticket does not exist or has been invalidly formatted."},
        )

    if ticket_rec.consumed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "TICKET_CONSUMED", "message": "Ticket has already been consumed."},
        )

    exp = ticket_rec.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)

    if now >= exp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "TICKET_EXPIRED", "message": "Ticket has expired."},
        )

    # Verify user is active and has valid role
    user_stmt = select(UserRecord).where(UserRecord.user_id == ticket_rec.user_id)
    user = db.execute(user_stmt).scalar_one_or_none()
    if user is None or not user.is_active or user.role.value not in {UserRole.ADMIN.value, UserRole.RECRUITER.value}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "USER_DISABLED_OR_FORBIDDEN", "message": "User is inactive or unauthorized."},
        )

    # Atomically mark ticket consumed
    ticket_rec.consumed_at = now
    db.commit()

    # Generate signed dashboard token
    session_ttl_minutes = settings.dashboard_session_ttl_minutes
    expires_in_seconds = session_ttl_minutes * 60
    exp_timestamp = int(now.timestamp()) + expires_in_seconds

    payload = {
        "sub": str(ticket_rec.user_id),
        "org_id": str(ticket_rec.organization_id),
        "role": ticket_rec.role,
        "purpose": DASHBOARD_TOKEN_PURPOSE,
        "exp": exp_timestamp,
        "jti": str(uuid.uuid4()),
    }

    token = _sign_token_payload(payload, settings.dashboard_token_secret)
    return token, expires_in_seconds, ticket_rec.user_id, ticket_rec.role, ticket_rec.organization_id


def verify_dashboard_token(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> DashboardAuthenticatedContext:
    """
    Dependency for dashboard read endpoints.
    Requires `Authorization: Bearer <dashboard_token>`.
    Verifies HMAC signature, purpose, expiration, and user role.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "MISSING_DASHBOARD_TOKEN", "message": "Valid Bearer dashboard token is required."},
        )

    raw_token = authorization.split("Bearer ", 1)[1].strip()
    payload = _verify_token_payload(raw_token, settings.dashboard_token_secret)

    if not payload or payload.get("purpose") != DASHBOARD_TOKEN_PURPOSE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN_PURPOSE", "message": "Token purpose or signature is invalid."},
        )

    now_ts = int(time.time())
    if payload.get("exp", 0) <= now_ts:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "TOKEN_EXPIRED", "message": "Dashboard session has expired."},
        )

    role = payload.get("role")
    if role not in {UserRole.ADMIN.value, UserRole.RECRUITER.value}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Role not authorized for analytics dashboard."},
        )

    try:
        user_id = uuid.UUID(payload["sub"])
        organization_id = uuid.UUID(payload["org_id"])
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "MALFORMED_TOKEN_CLAIMS", "message": "Token claims are malformed."},
        )

    # Ensure user is still active in DB
    user = db.execute(select(UserRecord).where(UserRecord.user_id == user_id)).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "USER_INACTIVE", "message": "User is no longer active."},
        )

    return DashboardAuthenticatedContext(
        user_id=user_id,
        organization_id=organization_id,
        role=role,
    )


# --- Internal cryptographic helpers ---

def _sign_token_payload(payload: dict, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signing_input = f"{header_b64}.{payload_b64}".encode()
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def _verify_token_payload(token: str, secret: str) -> Optional[dict]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, sig_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}".encode()
        expected_sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()

        # Re-pad base64 for decoding
        rem = len(sig_b64) % 4
        padded_sig = sig_b64 + ("=" * (4 - rem) if rem else "")
        provided_sig = base64.urlsafe_b64decode(padded_sig)

        if not hmac.compare_digest(expected_sig, provided_sig):
            return None

        rem_payload = len(payload_b64) % 4
        padded_payload = payload_b64 + ("=" * (4 - rem_payload) if rem_payload else "")
        payload_bytes = base64.urlsafe_b64decode(padded_payload)
        return json.loads(payload_bytes.decode())
    except Exception:
        return None
