"""Page 2: Hiring Pipeline Funnel & Requisition Distribution."""
import pandas as pd
import streamlit as st

from dashboard.api_client import DashboardAPIClient, DashboardAPIError
from dashboard.auth import ensure_authenticated_dashboard_session
from dashboard.ui.charts import create_pipeline_horizontal_bar_chart
from dashboard.ui.components import (
    render_expired_session_screen,
    render_page_header,
    render_sidebar_workspace_link,
)
from dashboard.ui.styles import inject_editorial_mineral_styles


def render_pipeline_view() -> None:
    inject_editorial_mineral_styles()
    render_sidebar_workspace_link()

    token = ensure_authenticated_dashboard_session()
    if not token:
        render_expired_session_screen()
        return

    api_client = DashboardAPIClient()

    # Filters
    st.sidebar.markdown("### Pipeline Filters")
    try:
        jobs_data = api_client.get_jobs(token)
        job_options = {"All Jobs": None}
        for j in jobs_data.jobs:
            job_options[f"{j.title} (Rev {j.current_revision})"] = str(j.job_id)
    except Exception:
        job_options = {"All Jobs": None}

    selected_job_label = st.sidebar.selectbox("Filter by Job", options=list(job_options.keys()))
    selected_job_id = job_options[selected_job_label]

    try:
        pipeline = api_client.get_pipeline(token, job_id=selected_job_id)
    except DashboardAPIError as e:
        if e.status_code == 401:
            render_expired_session_screen()
            return
        st.error(f"Error loading pipeline: {e.message}")
        return

    render_page_header(
        title="Hiring Pipeline Stages",
        eyebrow="Lifecycle Distribution",
        subtitle="Stage-by-stage distribution of candidates across submitted requisitions.",
        generated_at=pipeline.metadata.generated_at,
    )

    # 1. Pipeline Funnel Chart
    st.markdown("### Stage Funnel")
    stage_records = [{"stage": s.stage, "count": s.count, "description": s.description} for s in pipeline.stages]
    df_stages = pd.DataFrame(stage_records)

    if not df_stages.empty and df_stages["count"].sum() > 0:
        chart = create_pipeline_horizontal_bar_chart(df_stages)
        st.altair_chart(chart, use_container_width=True)
    else:
        st.info("No applications in pipeline for the selected criteria.")

    # 2. Stage Breakdown Table
    st.markdown("### Stage Breakdown")
    st.dataframe(df_stages.rename(columns={"stage": "Lifecycle Stage", "count": "Candidate Count", "description": "Stage Definition"}), use_container_width=True, hide_index=True)

    # 3. Per-Job Pipeline Breakdown Table
    st.markdown("### Requisition Breakdown")
    job_records = [
        {
            "Job Title": j.title,
            "Department": j.department or "—",
            "Status": j.publication_status,
            "Submitted": j.submitted_applications,
            "In Review": j.awaiting_review,
            "Interview Ready": j.interview_ready,
            "Released": j.interview_released,
            "Completed": j.completed_interviews,
            "Evaluated": j.evaluations_available,
        }
        for j in pipeline.jobs
    ]
    if job_records:
        st.dataframe(pd.DataFrame(job_records), use_container_width=True, hide_index=True)
    else:
        st.info("No active job requisitions found.")


if __name__ == "__main__":
    render_pipeline_view()
