"""Page 1: Operational Summary Overview."""
import pandas as pd
import streamlit as st

from dashboard.api_client import DashboardAPIClient, DashboardAPIError
from dashboard.auth import ensure_authenticated_dashboard_session, get_return_to_workspace_url
from dashboard.ui.components import (
    render_expired_session_screen,
    render_metric_card,
    render_page_header,
    render_sidebar_workspace_link,
)
from dashboard.ui.styles import inject_editorial_mineral_styles


def render_overview_view() -> None:
    inject_editorial_mineral_styles()
    render_sidebar_workspace_link()

    token = ensure_authenticated_dashboard_session()
    if not token:
        render_expired_session_screen()
        return

    api_client = DashboardAPIClient()

    col_title, col_action = st.columns([4, 1])
    with col_action:
        if st.button("↻ Refresh Data", use_container_width=True):
            st.rerun()

    try:
        overview = api_client.get_overview(token)
    except DashboardAPIError as e:
        if e.status_code == 401:
            render_expired_session_screen()
            return
        st.error(f"Error loading overview: {e.message}")
        return

    render_page_header(
        title="Recruitment Operations Overview",
        eyebrow="Authoritative Platform Overview",
        subtitle="Operational summary of active requisitions, applications, and evaluations.",
        generated_at=overview.metadata.generated_at,
    )

    # 1. Authoritative Top Metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        render_metric_card("Published Jobs", overview.published_jobs, "Active requisitions")
    with col2:
        render_metric_card("Applications", overview.submitted_applications, "Submitted candidates")
    with col3:
        render_metric_card("Awaiting Review", overview.awaiting_review, "Received status")
    with col4:
        render_metric_card("Interview Ready", overview.interview_ready, "Approved for release")

    col5, col6, col7, col8 = st.columns(4)
    with col5:
        render_metric_card("Active Releases", overview.active_interview_releases, "Candidate access active")
    with col6:
        render_metric_card("Completed Interviews", overview.completed_official_interviews, "Submitted sessions")
    with col7:
        render_metric_card("Evaluations Available", overview.evaluations_available, "Scored or finalized")
    with col8:
        render_metric_card("Draft / Ready Jobs", overview.draft_ready_jobs, "Unpublished revisions")

    # 2. Pipeline Summary Quick Table
    st.markdown("### Operational Pipeline Summary")
    try:
        pipeline = api_client.get_pipeline(token)
        stage_df = pd.DataFrame([{"Stage": s.stage, "Count": s.count, "Description": s.description} for s in pipeline.stages])
        st.dataframe(stage_df, use_container_width=True, hide_index=True)
    except Exception:
        st.info("Pipeline details available on the Hiring Pipeline page.")

    # 3. Quick Navigation Back Link
    workspace_url = get_return_to_workspace_url()
    st.markdown(
        f"""
        <div style="margin-top: 2rem; padding: 1rem; background: #FCFBF6; border: 1px solid #D6D1C4; border-radius: 6px;">
            <strong>Need to take action?</strong> Requisition editing, candidate approvals, and question releases are performed in the
            <a href="{workspace_url}" target="_self" class="mineral-link">Recruitment Workspace</a>.
        </div>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    render_overview_view()
