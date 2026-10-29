"""Reusable Editorial Mineral UI components for Streamlit."""
from datetime import datetime
from typing import Any, Optional
import streamlit as st

from dashboard.auth import get_return_to_workspace_url


def render_page_header(
    title: str,
    eyebrow: str = "Recruitment Analytics",
    subtitle: Optional[str] = None,
    generated_at: Optional[datetime] = None,
) -> None:
    """Renders the top Editorial Mineral page header."""
    time_str = generated_at.strftime("%Y-%m-%d %H:%M:%S UTC") if generated_at else ""
    st.markdown(
        f"""
        <div class="dashboard-header-container">
            <div class="dashboard-eyebrow">{eyebrow}</div>
            <h1 class="dashboard-title">{title}</h1>
            {f'<div class="dashboard-subtitle">{subtitle}</div>' if subtitle else ''}
            {f'<div style="font-size:0.85rem; color:#52605A; margin-top:0.3rem;">Data generated: {time_str}</div>' if time_str else ''}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_metric_card(label: str, value: Any, hint: Optional[str] = None) -> None:
    """Renders a single limestone mineral metric card."""
    st.markdown(
        f"""
        <div class="mineral-card">
            <div class="mineral-card-label">{label}</div>
            <div class="mineral-card-value">{value}</div>
            {f'<div class="mineral-card-hint">{hint}</div>' if hint else ''}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_status_badge(status: str) -> str:
    """Returns HTML for an Editorial Mineral status badge."""
    norm = status.upper()
    if norm in {"AVAILABLE", "FINALIZED", "RELEASED", "ACTIVE", "HIGH"}:
        cls = "badge-available"
    elif norm in {"ATTENTION_NEEDED", "IN_REVIEW", "IN_PROGRESS", "MEDIUM", "DRAFT"}:
        cls = "badge-attention"
    else:
        cls = "badge-unavailable"
    return f'<span class="mineral-badge {cls}">{status}</span>'


def render_sidebar_workspace_link() -> None:
    """Renders the safe Return to Workspace link in the sidebar."""
    workspace_url = get_return_to_workspace_url()
    st.sidebar.markdown("---")
    st.sidebar.markdown(
        f"""
        <div style="padding: 0.5rem 0;">
            <a href="{workspace_url}" target="_self" class="mineral-link" style="display:inline-flex; align-items:center; gap:6px; font-size:0.95rem;">
                <span>← Return to Workspace</span>
            </a>
            <div style="font-size: 0.85rem; color: #52605A; margin-top: 0.25rem;">
                Back to React recruitment platform
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_expired_session_screen() -> None:
    """Renders safe unauthenticated/session expired notice."""
    workspace_url = get_return_to_workspace_url()
    st.markdown(
        f"""
        <div style="max-width: 550px; margin: 60px auto; padding: 2rem; background: #FFFFFF; border: 1px solid #D6DCD7; border-radius: 6px; text-align: center;">
            <div style="font-size: 0.85rem; font-weight: 700; color: #654A32; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem;">
                Session Notice
            </div>
            <h2 style="font-size: 1.5rem; font-weight: 600; color: #202824; margin-bottom: 0.75rem;">Dashboard Access Required</h2>
            <p style="font-size: 1rem; color: #52605A; line-height: 1.5; margin-bottom: 1.5rem;">
                This analytics dashboard requires a valid single-use ticket or active recruitment session.
                Please launch analytics directly from the recruitment workspace.
            </p>
            <a href="{workspace_url}" target="_self" style="display: inline-block; background: #174F4A; color: #FFFFFF; padding: 10px 22px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 1rem;">
                Return to Recruitment Workspace
            </a>
        </div>
        """,
        unsafe_allow_html=True,
    )
