"""Editorial Mineral styling and CSS injection for Streamlit."""
import streamlit as st

EDITORIAL_MINERAL_CSS = """
<style>
/* Base typography and background overrides */
html, body, [data-testid="stAppViewContainer"] {
    background-color: #F6F7F5;
    color: #202824;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}

[data-testid="stSidebar"] {
    background-color: #FFFFFF;
    border-right: 1px solid #D6DCD7;
}

/* Header section */
.dashboard-header-container {
    padding: 1.5rem 0 1rem 0;
    border-bottom: 1px solid #D6DCD7;
    margin-bottom: 1.5rem;
}

.dashboard-eyebrow {
    font-size: 0.8rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #52605A;
    margin-bottom: 0.25rem;
}

.dashboard-title {
    font-size: 1.85rem;
    font-weight: 600;
    color: #174F4A;
    margin: 0;
    line-height: 1.2;
}

.dashboard-subtitle {
    font-size: 1rem;
    color: #52605A;
    margin-top: 0.35rem;
}

/* Metric Cards */
.mineral-card {
    background-color: #FFFFFF;
    border: 1px solid #D6DCD7;
    border-radius: 6px;
    padding: 1.25rem 1rem;
    margin-bottom: 1rem;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}

.mineral-card-label {
    font-size: 0.85rem;
    font-weight: 600;
    color: #52605A;
    margin-bottom: 0.35rem;
}

.mineral-card-value {
    font-size: 2rem;
    font-weight: 700;
    color: #174F4A;
    line-height: 1.1;
}

.mineral-card-hint {
    font-size: 0.8rem;
    color: #52605A;
    margin-top: 0.35rem;
}

/* Status Badges */
.mineral-badge {
    display: inline-block;
    padding: 0.25rem 0.65rem;
    border-radius: 6px;
    font-size: 0.875rem;
    font-weight: 600;
}

.badge-available {
    background-color: #F0F2EF;
    color: #174F4A;
    border: 1px solid #D6DCD7;
}

.badge-attention {
    background-color: #F0F2EF;
    color: #76551F;
    border: 1px solid #76551F;
}

.badge-unavailable {
    background-color: #F0F2EF;
    color: #654A32;
    border: 1px solid #654A32;
}

/* Links and actions */
a.mineral-link {
    color: #35566F;
    text-decoration: underline;
    font-weight: 600;
}

a.mineral-link:hover {
    color: #174F4A;
}

/* Tables styling */
[data-testid="stDataFrame"] {
    border: 1px solid #D6DCD7;
    border-radius: 6px;
    background-color: #FFFFFF;
}
</style>
"""


def inject_editorial_mineral_styles() -> None:
    st.markdown(EDITORIAL_MINERAL_CSS, unsafe_allow_html=True)
