"""Page 5: Subsystem & Speech Runtime Readiness."""
import streamlit as st

from dashboard.api_client import DashboardAPIClient, DashboardAPIError
from dashboard.auth import ensure_authenticated_dashboard_session
from dashboard.ui.components import (
    render_expired_session_screen,
    render_page_header,
    render_sidebar_workspace_link,
    render_status_badge,
)
from dashboard.ui.styles import inject_editorial_mineral_styles


def render_readiness_view() -> None:
    inject_editorial_mineral_styles()
    render_sidebar_workspace_link()

    token = ensure_authenticated_dashboard_session()
    if not token:
        render_expired_session_screen()
        return

    api_client = DashboardAPIClient()

    try:
        readiness = api_client.get_readiness(token)
    except DashboardAPIError as e:
        if e.status_code == 401:
            render_expired_session_screen()
            return
        st.error(f"Error loading system readiness: {e.message}")
        return

    render_page_header(
        title="Subsystem & Service Readiness",
        eyebrow="Diagnostic Status",
        subtitle="Honest operational status of local AI, PostgreSQL database, and speech transcription engines.",
        generated_at=readiness.metadata.generated_at,
    )

    # Informational notice
    st.markdown(
        f"""
        <div style="padding: 1rem; background: #FFFFFF; border: 1px solid #D6DCD7; border-radius: 6px; margin-bottom: 1.5rem; color: #202824; font-size: 1rem;">
            <strong>System Status:</strong> {readiness.user_safe_message}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Service Table
    services = [
        {"Subsystem": "FastAPI Application Core", "Status": readiness.api_status, "Scope": "HTTP API & Security Guardrails"},
        {"Subsystem": "PostgreSQL 16 Database", "Status": readiness.database_status, "Scope": "Authoritative Persistent Store"},
        {"Subsystem": "Local AI Engine", "Status": readiness.local_ai_status, "Scope": "Local Inference"},
        {"Subsystem": "Text Interview Service", "Status": readiness.text_interview_status, "Scope": "Asynchronous Candidate Messaging"},
        {"Subsystem": "Audio Question Playback (TTS)", "Status": readiness.audio_playback_status, "Scope": "Local Speech Synthesis"},
        {"Subsystem": "Audio Speech Transcription (STT)", "Status": readiness.audio_transcription_status, "Scope": "Local Zero-Retention Transcription Engine"},
    ]

    for s in services:
        col_name, col_status, col_scope = st.columns([3, 2, 4])
        with col_name:
            st.markdown(f"**{s['Subsystem']}**")
        with col_status:
            st.markdown(render_status_badge(s["Status"]), unsafe_allow_html=True)
        with col_scope:
            st.markdown(f"<span style='color:#52605A; font-size:0.95rem;'>{s['Scope']}</span>", unsafe_allow_html=True)
        st.markdown("<hr style='margin: 0.5rem 0; border: none; border-top: 1px solid #D6DCD7;' />", unsafe_allow_html=True)


if __name__ == "__main__":
    render_readiness_view()
