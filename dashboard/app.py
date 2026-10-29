"""Streamlit Recruitment Analytics Dashboard Entrypoint (Phase 4E.2)."""
import sys
from pathlib import Path

# Ensure both project root and dashboard directory are on sys.path
_current_dir = Path(__file__).resolve().parent
_project_root = _current_dir.parent
for _p in [str(_project_root), str(_current_dir)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import streamlit as st

from dashboard.auth import ensure_authenticated_dashboard_session, get_current_user_role
from dashboard.ui.components import render_expired_session_screen, render_sidebar_workspace_link
from dashboard.ui.styles import inject_editorial_mineral_styles
from dashboard.views.interviews import render_interviews_view
from dashboard.views.matches import render_matches_view
from dashboard.views.overview import render_overview_view
from dashboard.views.pipeline import render_pipeline_view
from dashboard.views.readiness import render_readiness_view

st.set_page_config(
    page_title="Recruitment Analytics — Smart Hiring",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_editorial_mineral_styles()


def main():
    # 1. Enforce session authentication or ticket exchange
    token = ensure_authenticated_dashboard_session()
    if not token:
        render_expired_session_screen()
        return

    # 2. Sidebar Navigation
    st.sidebar.markdown(
        """
        <div style="padding: 1rem 0 0.5rem 0;">
            <div style="font-size: 0.8rem; font-weight: 700; color: #52605A; text-transform: uppercase; letter-spacing: 0.05em;">
                Editorial Mineral
            </div>
            <div style="font-size: 1.25rem; font-weight: 600; color: #174F4A;">
                Smart Analytics
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    page = st.sidebar.radio(
        "Navigation",
        options=[
            "Overview",
            "Hiring pipeline",
            "Match insights",
            "Interview outcomes",
            "Service readiness",
        ],
        label_visibility="collapsed",
    )

    # 3. Render Selected View
    if page == "Overview":
        render_overview_view()
    elif page == "Hiring pipeline":
        render_pipeline_view()
    elif page == "Match insights":
        render_matches_view()
    elif page == "Interview outcomes":
        render_interviews_view()
    elif page == "Service readiness":
        render_readiness_view()


if __name__ == "__main__":
    main()
