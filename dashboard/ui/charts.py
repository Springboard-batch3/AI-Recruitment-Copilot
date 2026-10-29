"""Editorial Mineral chart helpers using Altair."""
import altair as alt
import pandas as pd


def create_pipeline_horizontal_bar_chart(df: pd.DataFrame) -> alt.Chart:
    """
    Creates a restrained horizontal stage bar chart using Editorial Mineral colors (#174F4A).
    """
    chart = (
        alt.Chart(df)
        .mark_bar(color="#174F4A", cornerRadiusTopRight=3, cornerRadiusBottomRight=3)
        .encode(
            x=alt.X("count:Q", title="Candidate Count", axis=alt.Axis(grid=True, gridColor="#D6DCD7", labelColor="#202824", titleColor="#202824", labelFontSize=13, titleFontSize=14)),
            y=alt.Y("stage:N", title=None, sort=None, axis=alt.Axis(labelFontSize=14, labelColor="#202824")),
            tooltip=["stage", "count", "description"],
        )
        .properties(height=260)
        .configure_view(strokeWidth=0)
    )
    return chart


def create_match_distribution_chart(df: pd.DataFrame) -> alt.Chart:
    """
    Creates an accessible score distribution histogram with high contrast and binning tailored for small and large datasets.
    """
    if df.empty:
        # Return empty placeholder chart
        return alt.Chart(pd.DataFrame({"x": []})).mark_bar()

    chart = (
        alt.Chart(df)
        .mark_bar(color="#174F4A", stroke="#0D302D", strokeWidth=1, cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X(
                "match_score:Q",
                bin=alt.Bin(maxbins=10, step=10),
                scale=alt.Scale(domain=[0, 100]),
                title="Compatibility Score (%)",
                axis=alt.Axis(labelColor="#202824", titleColor="#202824", labelFontSize=13, titleFontSize=14),
            ),
            y=alt.Y(
                "count():Q",
                title="Candidate Count",
                axis=alt.Axis(tickMinStep=1, labelColor="#202824", titleColor="#202824", labelFontSize=13, titleFontSize=14, grid=True, gridColor="#D6DCD7"),
            ),
            tooltip=[
                alt.Tooltip("count():Q", title="Candidate Count"),
            ],
        )
        .properties(height=220)
        .configure_view(strokeWidth=0)
    )
    return chart


def create_missing_criteria_chart(df: pd.DataFrame) -> alt.Chart:
    """
    Creates a horizontal bar chart displaying candidate counts for frequently missing required criteria.
    """
    if df.empty:
        return alt.Chart(pd.DataFrame({"criterion": [], "count": []})).mark_bar()

    chart = (
        alt.Chart(df)
        .mark_bar(color="#76551F", cornerRadiusTopRight=3, cornerRadiusBottomRight=3)
        .encode(
            x=alt.X(
                "count:Q",
                title="Candidates Missing Evidence",
                axis=alt.Axis(tickMinStep=1, grid=True, gridColor="#D6DCD7", labelColor="#202824", titleColor="#202824", labelFontSize=13, titleFontSize=14),
            ),
            y=alt.Y(
                "criterion:N",
                title=None,
                sort="-x",
                axis=alt.Axis(labelLimit=300, labelFontSize=13, labelColor="#202824"),
            ),
            tooltip=[
                alt.Tooltip("criterion:N", title="Required Criterion"),
                alt.Tooltip("count:Q", title="Missing Count"),
            ],
        )
        .properties(height=max(160, len(df) * 32))
        .configure_view(strokeWidth=0)
    )
    return chart
