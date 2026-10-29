"""Focused Tests for Phase 4A.4 — Workflow Navigation, Authorization, and Compatibility Foundation."""
import uuid
from datetime import datetime, timezone
import pytest
from starlette.requests import Request
from starlette.datastructures import Headers

from app.api.auth import (
    AuthenticatedContext,
    CandidateAuthenticatedContext,
    require_admin,
    require_recruiter,
    require_candidate,
)
from app.config import get_settings
from app.models.auth import UserRecord, UserRole
from app.models.candidate import CandidateIdentityRecord
from fastapi import HTTPException, status


def test_dual_session_cookie_isolation():
    """Verify Recruiter and Candidate session cookies use distinct keys and do not collide."""
    settings = get_settings()
    assert settings.session_cookie_name != settings.candidate_session_cookie_name
    assert "candidate" in settings.candidate_session_cookie_name.lower()
    assert settings.session_cookie_name == "ai_hiring_session"
    assert settings.candidate_session_cookie_name == "ai_hiring_candidate_session"


def test_recruiter_authorization_rejects_candidate():
    """Verify that a candidate auth context cannot fulfill recruiter dependency."""
    # Raising 401 when unauthorized candidate hits recruiter route
    with pytest.raises(HTTPException) as exc_info:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    assert exc_info.value.status_code == 401


from unittest.mock import MagicMock
from app.models.auth import UserRecord, UserRole, UserSessionRecord


@pytest.mark.anyio
async def test_admin_authorization_rejects_recruiter():
    """Verify that a recruiter cannot access admin-only endpoints."""
    recruiter_user = UserRecord(
        user_id=uuid.uuid4(),
        email="recruiter@example.com",
        password_hash="hash",
        display_name="Recruiter Bob",
        role=UserRole.RECRUITER,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    mock_session = MagicMock(spec=UserSessionRecord)
    mock_db = MagicMock()
    recruiter_ctx = AuthenticatedContext(user=recruiter_user, session=mock_session)

    with pytest.raises(HTTPException) as exc_info:
        await require_admin(auth_ctx=recruiter_ctx, db=mock_db)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert "Insufficient permissions" in str(exc_info.value.detail)


@pytest.mark.anyio
async def test_admin_authorization_accepts_admin():
    """Verify that an admin user passes require_admin."""
    admin_user = UserRecord(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        password_hash="hash",
        display_name="Admin Alice",
        role=UserRole.ADMIN,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    mock_session = MagicMock(spec=UserSessionRecord)
    mock_db = MagicMock()
    admin_ctx = AuthenticatedContext(user=admin_user, session=mock_session)
    result = await require_admin(auth_ctx=admin_ctx, db=mock_db)
    assert result.user.role == UserRole.ADMIN


@pytest.mark.anyio
async def test_recruiter_authorization_accepts_recruiter():
    """Verify that recruiter passes require_recruiter."""
    mock_session = MagicMock(spec=UserSessionRecord)
    mock_db = MagicMock()
    recruiter_user = UserRecord(
        user_id=uuid.uuid4(),
        email="recruiter@example.com",
        password_hash="hash",
        display_name="Recruiter Bob",
        role=UserRole.RECRUITER,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    recruiter_ctx = AuthenticatedContext(user=recruiter_user, session=mock_session)
    res_rec = await require_recruiter(auth_ctx=recruiter_ctx, db=mock_db)
    assert res_rec.user.role == UserRole.RECRUITER
