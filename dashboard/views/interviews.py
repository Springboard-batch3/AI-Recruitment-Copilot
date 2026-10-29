"""Page 4: Official Interview Outcomes (Strict Text and Audio Separation)."""
import pandas as pd
import streamlit as st

from dashboard.api_client import DashboardAPIClient, DashboardAPIError
from dashboard.auth import ensure_authenticated_dashboard_session
from dashboard.ui.components import (
    render_expired_session_screen,
    render_page_header,
    render_sidebar_workspace_link,
)
from dashboard.ui.styles import inject_editorial_mineral_styles


def render_interviews_view() -> None:
    inject_editorial_mineral_styles()
    render_sidebar_workspace_link()

    token = ensure_authenticated_dashboard_session()
    if not token:
        render_expired_session_screen()
        return

    api_client = DashboardAPIClient()

    # Sidebar filters
    st.sidebar.markdown("### Interview Filters")
    try:
        jobs_data = api_client.get_jobs(token)
        job_options = {"All Jobs": None}
        for j in jobs_data.jobs:
            job_options[f"{j.title}"] = str(j.job_id)
    except Exception:
        job_options = {"All Jobs": None}

    selected_job_label = st.sidebar.selectbox("Filter by Job", options=list(job_options.keys()))
    selected_job_id = job_options[selected_job_label]

    status_option = st.sidebar.selectbox("Session Status", ["All Statuses", "SUBMITTED", "IN_PROGRESS"])
    selected_status = None if status_option == "All Statuses" else status_option

    render_page_header(
        title="Official Interview Outcomes",
        eyebrow="Evaluation & Session Analytics",
        subtitle="Independent analysis of Text and Audio interviews. Audio evaluations assess accepted transcript text only.",
    )

    tab_text, tab_audio = st.tabs(["📝 Text Interviews", "🎙️ Audio Interviews"])

    with tab_text:
        try:
            text_data = api_client.get_interviews(
                token=token,
                job_id=selected_job_id,
                interview_mode="TEXT",
                session_status=selected_status,
            )
            _render_mode_interviews(text_data.interviews, mode="TEXT")
        except DashboardAPIError as e:
            if e.status_code == 401:
                render_expired_session_screen()
                return
            st.error(f"Error loading text interviews: {e.message}")

    with tab_audio:
        try:
            audio_data = api_client.get_interviews(
                token=token,
                job_id=selected_job_id,
                interview_mode="AUDIO",
                session_status=selected_status,
            )
            _render_mode_interviews(audio_data.interviews, mode="AUDIO")
        except DashboardAPIError as e:
            if e.status_code == 401:
                render_expired_session_screen()
                return
            st.error(f"Error loading audio interviews: {e.message}")


def _render_mode_interviews(interviews: list, mode: str) -> None:
    if not interviews:
        st.info(f"No {mode.lower()} interview sessions found matching the criteria.")
        return

    # Counts
    completed = sum(1 for i in interviews if i.session_status == "SUBMITTED")
    evaluated = sum(1 for i in interviews if i.persisted_overall_score is not None)
    in_prog = sum(1 for i in interviews if i.session_status == "IN_PROGRESS")

    c1, c2, c3 = st.columns(3)
    c1.metric("Completed Sessions", completed)
    c2.metric("Evaluations Available", evaluated)
    c3.metric("In Progress", in_prog)

    if mode == "AUDIO":
        st.markdown(
            """
            <div style="padding: 0.85rem 1rem; background: #FFFFFF; border: 1px solid #D6DCD7; border-left: 3px solid #174F4A; border-radius: 6px; font-size: 0.95rem; color: #202824; margin: 1rem 0;">
                <strong>Audio Privacy Guarantee:</strong> Voice evaluations are strictly derived from accepted whisper.cpp transcript text.
                No acoustic, tone, pitch, pronunciation, confidence, hesitation, or personality analysis is performed.
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Table
    records = []
    for i in interviews:
        score_display = f"{i.persisted_overall_score:.1f}" if i.persisted_overall_score is not None else "Unavailable"
        sub_date = i.submitted_at.strftime("%Y-%m-%d %H:%M") if i.submitted_at else "In Progress"
        eval_date = i.evaluated_at.strftime("%Y-%m-%d %H:%M") if i.evaluated_at else "—"

        records.append({
            "Candidate": i.candidate_display_name,
            "Job Title": i.job_title,
            "Session Status": i.session_status,
            "Evaluation Status": i.evaluation_status,
            "Overall Score": score_display,
            "Submitted Date": sub_date,
            "Evaluated Date": eval_date,
        })

    st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    render_interviews_view()
