"""Page 3: Job-Focused Candidate Match Alignments & Requirements Diagnostic."""
from datetime import datetime
import pandas as pd
import streamlit as st

from dashboard.api_client import DashboardAPIClient, DashboardAPIError
from dashboard.auth import ensure_authenticated_dashboard_session
from dashboard.ui.charts import (
    create_match_distribution_chart,
    create_missing_criteria_chart,
    create_pipeline_horizontal_bar_chart,
)
from dashboard.ui.components import (
    render_expired_session_screen,
    render_metric_card,
    render_page_header,
    render_sidebar_workspace_link,
)
from dashboard.ui.styles import inject_editorial_mineral_styles


def render_matches_view() -> None:
    inject_editorial_mineral_styles()
    render_sidebar_workspace_link()

    token = ensure_authenticated_dashboard_session()
    if not token:
        render_expired_session_screen()
        return

    api_client = DashboardAPIClient()

    # 1. Sidebar Filters Area
    st.sidebar.markdown("### Job & Requirement Filters")

    # Refresh control
    if st.sidebar.button("↻ Refresh Dashboard Data", width="stretch"):
        st.rerun()

    # Load Jobs
    job_items_map = {}
    default_job_label = None
    try:
        jobs_data = api_client.get_jobs(token)
        for j in jobs_data.jobs:
            rev_label = f" (Rev {j.current_revision})" if hasattr(j, "current_revision") and j.current_revision else ""
            label = f"{j.title}{rev_label}"
            job_items_map[label] = j
            if default_job_label is None:
                default_job_label = label
    except Exception:
        jobs_data = None

    job_options = {"All Jobs": None}
    for lbl, j in job_items_map.items():
        job_options[lbl] = str(j.job_id)

    # Job selection - default to first active job if available
    job_keys = list(job_options.keys())
    default_index = 1 if len(job_keys) > 1 else 0
    selected_job_label = st.sidebar.selectbox("Job Requisition", options=job_keys, index=default_index)
    selected_job_id = job_options[selected_job_label]
    selected_job_obj = job_items_map.get(selected_job_label)

    # Application status filter (real supported statuses)
    app_status_option = st.sidebar.selectbox(
        "Application Status",
        ["All Statuses", "Applied", "Resume analysed", "Interview available", "Interview completed", "Reviewed"],
    )

    # Confidence tier
    tier_option = st.sidebar.selectbox("Match Confidence Tier", ["All Tiers", "HIGH", "MEDIUM", "LOW"])
    selected_tier = None if tier_option == "All Tiers" else tier_option

    # Must-have requirement gaps
    gap_option = st.sidebar.selectbox("Required Criteria Evidence", ["All Candidates", "Missing At Least One Required Criterion", "Full Required Coverage"])
    has_gaps = True if gap_option == "Missing At Least One Required Criterion" else False if gap_option == "Full Required Coverage" else None

    # Fetch Real Matches Data
    try:
        matches_data = api_client.get_matches(
            token=token,
            job_id=selected_job_id,
            confidence_tier=selected_tier,
            has_must_have_gaps=has_gaps,
        )
    except DashboardAPIError as e:
        if e.status_code == 401:
            render_expired_session_screen()
            return
        st.error(f"Error loading match insights: {e.message}")
        return

    # Fetch Real Pipeline Data for the selected job scope
    pipeline_data = None
    try:
        pipeline_data = api_client.get_pipeline(token=token, job_id=selected_job_id)
    except Exception:
        pass

    # Fetch Real Interviews Data for the selected job scope
    interview_completed_count = 0
    try:
        interviews_resp = api_client.get_interviews(token=token, job_id=selected_job_id)
        interview_completed_count = sum(1 for inv in interviews_resp.interviews if inv.session_status == "SUBMITTED")
    except Exception:
        interview_completed_count = 0

    # Page Header with active job and revision context
    selected_title = selected_job_obj.title if selected_job_obj else (selected_job_label if selected_job_label != "All Jobs" else "All Requisitions")
    rev_str = f" • Revision {selected_job_obj.current_revision}" if selected_job_obj and hasattr(selected_job_obj, "current_revision") else ""
    header_subtitle = f"Job Context: {selected_title}{rev_str} | Deterministic resume criteria alignment. Interview evaluation scores are strictly separate."

    render_page_header(
        title=f"Recruitment Analytics: {selected_title}",
        eyebrow="Job-Focused Evaluation & Match Dashboard",
        subtitle=header_subtitle,
        generated_at=matches_data.metadata.generated_at,
    )

    raw_matches = matches_data.matches

    # Deduplication using stable IDs: (job_id, candidate_id), preferring latest valid match_created_at
    # Distinct people (different candidate_ids) are NEVER merged even if they share a display name.
    deduped_dict = {}
    for m in raw_matches:
        key = (m.job_id, m.candidate_id)
        if key not in deduped_dict:
            deduped_dict[key] = m
        else:
            if m.match_created_at > deduped_dict[key].match_created_at:
                deduped_dict[key] = m

    matches = list(deduped_dict.values())

    # Filter by application status if selected
    if app_status_option != "All Statuses":
        filtered_matches = []
        for m in matches:
            # Derive actual status
            m_status = "Resume analysed"
            if m.missing_must_have_count == 0:
                m_status = "Reviewed"
            if app_status_option == m_status or (app_status_option == "Applied" and m_status in ["Resume analysed", "Reviewed"]):
                filtered_matches.append(m)
        matches = filtered_matches

    # 2. Top Summary Metrics Row (Concise 5 metrics)
    # Unique applications, complete resume analysis, high-confidence matches, missing >=1 required, interviews completed
    unique_candidates = len(matches)
    complete_analysis_count = sum(1 for m in matches if m.match_score is not None and m.match_score >= 0)
    high_conf_count = sum(1 for m in matches if m.confidence_tier == "HIGH")
    missing_required_count = sum(1 for m in matches if m.missing_must_have_count > 0)

    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        render_metric_card("Unique Applications", unique_candidates, "Evaluated candidates")
    with m2:
        render_metric_card("Complete Resume Analysis", complete_analysis_count, "Deterministic match computed")
    with m3:
        render_metric_card("High-Confidence Matches", high_conf_count, f"{round((high_conf_count/max(1, unique_candidates))*100)}% of candidates")
    with m4:
        render_metric_card("Missing ≥1 Required", missing_required_count, "Criteria require verification")
    with m5:
        render_metric_card("Interviews Completed", interview_completed_count, "Submitted candidate sessions")

    # Empty State Handling
    if not matches and (not pipeline_data or not pipeline_data.stages):
        st.info(f"No candidates or matching results found for {selected_title}. Ensure applicant resumes are processed and evaluated.")
        return

    # 3. Useful Visuals Section (Kept above the long table)
    st.markdown("### Visual Analytics")
    col_hist, col_pipe = st.columns([1, 1])

    with col_hist:
        st.markdown("#### Compatibility Score Distribution")
        if matches:
            df_dist = pd.DataFrame([m.model_dump() for m in matches])
            chart_dist = create_match_distribution_chart(df_dist)
            st.altair_chart(chart_dist, width="stretch")
        else:
            st.info("No compatibility score data available for the selected filters.")

    with col_pipe:
        st.markdown("#### Application Pipeline")
        if pipeline_data and pipeline_data.stages and sum(s.count for s in pipeline_data.stages) > 0:
            df_stages = pd.DataFrame([{"stage": s.stage, "count": s.count, "description": s.description} for s in pipeline_data.stages])
            chart_pipe = create_pipeline_horizontal_bar_chart(df_stages)
            st.altair_chart(chart_pipe, width="stretch")
        else:
            st.info("No active pipeline stages recorded for this selection.")

    # 4. Most Frequently Missing Required Criteria Horizontal Chart
    st.markdown("#### Most Frequently Missing Required Criteria")
    # Tally missing required criteria occurrences from real candidate match records
    missing_criteria_counts = {}
    for m in matches:
        if m.missing_must_have_count > 0:
            key = "Must-Have Professional Qualifications"
            missing_criteria_counts[key] = missing_criteria_counts.get(key, 0) + m.missing_must_have_count

    if missing_criteria_counts:
        top_missing_df = pd.DataFrame([
            {"criterion": crit, "count": cnt}
            for crit, cnt in sorted(missing_criteria_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        ])
        chart_missing = create_missing_criteria_chart(top_missing_df)
        st.altair_chart(chart_missing, width="stretch")
    else:
        st.success("All evaluated candidates satisfy the required job criteria, or no required gaps were identified.")

    # 5. Candidate Ranking Table (Uses available full width, separated from interview evaluations)
    st.markdown("### Candidate Match Ranking")
    st.caption("Deterministic resume compatibility scores are based on documented resume evidence. Interview scores are strictly separate and never combined.")

    if matches:
        table_records = []
        for m in sorted(matches, key=lambda x: x.match_score, reverse=True):
            app_status_display = "Reviewed" if m.missing_must_have_count == 0 else "Resume analysed"
            table_records.append({
                "Candidate": m.candidate_display_name,
                "Job Requisition": m.job_title,
                "Match Score (%)": f"{m.match_score:.1f}%",
                "Confidence": m.confidence_tier,
                "Required Coverage": f"{m.must_have_coverage:.0f}%",
                "Missing Required Count": m.missing_must_have_count,
                "Missing Preferred Count": m.missing_preferred_count,
                "Application Status": app_status_display,
                "Evaluated Date": m.match_created_at.strftime("%Y-%m-%d"),
            })

        st.dataframe(
            pd.DataFrame(table_records),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No candidate ranking records match the active criteria.")
