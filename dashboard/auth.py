import time
from typing import Optional, Tuple
import streamlit as st

from dashboard.api_client import DashboardAPIClient, DashboardAPIError
from dashboard.config import get_config


def ensure_authenticated_dashboard_session() -> Optional[str]:
    """
    Manages the Streamlit session authentication lifecycle:
    1. Checks if a valid bearer token exists in st.session_state and has not expired.
    2. If not, inspects st.query_params for a 'ticket'.
    3. If ticket exists, exchanges it with FastAPI for a short-lived bearer token.
    4. Immediately cleans up the ticket from query_params to prevent re-exposure or copy-paste reuse.
    5. Stores token only in st.session_state (in-memory).
    6. Returns the valid token or None if unauthenticated/expired.
    """
    now = int(time.time())

    # 1. Check existing session state token
    token = st.session_state.get("dashboard_token")
    token_exp = st.session_state.get("dashboard_token_expires_at", 0)

    if token and now < token_exp:
        return token

    # Token expired in session
    if token and now >= token_exp:
        clear_dashboard_session()

    # 2. Check for ticket in query parameters
    ticket = st.query_params.get("ticket")
    if not ticket:
        return None

    # Handle if ticket is a list in some Streamlit versions
    if isinstance(ticket, list):
        ticket = ticket[0] if ticket else None

    if not ticket:
        return None

    # 3. Exchange ticket via API client
    api_client = DashboardAPIClient()
    try:
        data = api_client.exchange_ticket(ticket)
        access_token = data["access_token"]
        expires_in = data.get("expires_in_seconds", 1800)
        role = data.get("role", "RECRUITER")
        org_id = data.get("organization_id")

        # 4. Store in session state
        st.session_state["dashboard_token"] = access_token
        st.session_state["dashboard_token_expires_at"] = now + expires_in
        st.session_state["dashboard_user_role"] = role
        st.session_state["dashboard_org_id"] = str(org_id) if org_id else None

        # 5. Clean up query parameters immediately
        try:
            if "ticket" in st.query_params:
                del st.query_params["ticket"]
        except Exception:
            pass

        return access_token
    except DashboardAPIError as e:
        # Clean query param on failure as well
        try:
            if "ticket" in st.query_params:
                del st.query_params["ticket"]
        except Exception:
            pass
        st.session_state["dashboard_auth_error"] = str(e)
        return None


def clear_dashboard_session() -> None:
    """Clears all session-scoped authentication credentials from memory."""
    keys = ["dashboard_token", "dashboard_token_expires_at", "dashboard_user_role", "dashboard_org_id", "dashboard_auth_error"]
    for k in keys:
        if k in st.session_state:
            del st.session_state[k]


def get_current_user_role() -> str:
    return st.session_state.get("dashboard_user_role", "RECRUITER")


def get_return_to_workspace_url() -> str:
    config = get_config()
    return config.react_app_public_url.rstrip("/")
