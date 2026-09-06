"""Revenue performance dashboard.

Reads the committed DuckDB file directly, so the deployed app needs no
warehouse credentials and keeps working after the Snowflake trial expires.
Read-only connection: the dashboard is a consumer of the mart, never a writer.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

DB_PATH = Path(__file__).resolve().parent.parent / "warehouse.duckdb"

st.set_page_config(page_title="Revenue Performance", page_icon="📊", layout="wide")


@st.cache_data
def query(sql: str) -> pd.DataFrame:
    connection = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return connection.execute(sql).fetch_df()
    finally:
        connection.close()


def render_header() -> None:
    st.title("Revenue Performance")
    st.caption(
        "Actual against plan at product x region x month. Per-order delivery "
        "detail lives in fct_orders — the two grains are deliberately separate."
    )


def render_kpis(mart: pd.DataFrame) -> None:
    actual = mart["actual_revenue"].sum()
    forecast = mart["forecast_revenue"].sum()
    variance = actual - forecast
    on_time = mart["on_time_count"].sum()
    delivered = on_time + mart["late_count"].sum()

    columns = st.columns(4)
    columns[0].metric("Actual revenue", f"${actual:,.0f}")
    columns[1].metric("Forecast revenue", f"${forecast:,.0f}")
    columns[2].metric(
        "Variance",
        f"${variance:,.0f}",
        delta=f"{variance / forecast:.1%}" if forecast else None,
    )
    columns[3].metric(
        "On-time delivery",
        f"{on_time / delivered:.1%}" if delivered else "n/a",
    )


def render_variance_chart(mart: pd.DataFrame) -> None:
    st.subheader("Actual against plan by month")
    monthly = (
        mart.groupby("revenue_month", as_index=False)[["actual_revenue", "forecast_revenue"]]
        .sum()
        .melt(
            id_vars="revenue_month",
            var_name="measure",
            value_name="revenue",
        )
    )
    figure = px.bar(
        monthly,
        x="revenue_month",
        y="revenue",
        color="measure",
        barmode="group",
        labels={"revenue_month": "Month", "revenue": "Revenue"},
    )
    st.plotly_chart(figure, width="stretch")


def render_region_breakdown(mart: pd.DataFrame) -> None:
    st.subheader("Variance by region")
    st.caption(
        "UNMAPPED is revenue from a customer absent from Salesforce (defect D2). "
        "It has no region, so no forecast can explain it — retained and visible "
        "rather than dropped."
    )
    by_region = (
        mart.groupby("region", as_index=False)
        .agg(
            actual_revenue=("actual_revenue", "sum"),
            forecast_revenue=("forecast_revenue", "sum"),
            revenue_variance=("revenue_variance", "sum"),
        )
        .sort_values("revenue_variance")
    )
    st.dataframe(by_region, width="stretch", hide_index=True)


def render_data_quality() -> None:
    st.subheader("Data quality")
    st.caption(
        "Rows excluded from analytics, retained with the reason. Nothing is "
        "deleted — a dbt test reconciles every source dollar against these buckets."
    )
    st.dataframe(
        query("select * from analytics.dq_reject_summary order by rejected_row_count desc"),
        width="stretch",
        hide_index=True,
    )


def main() -> None:
    if not DB_PATH.exists():
        st.error("warehouse.duckdb not found. Run `make ingest && make build` first.")
        return

    render_header()
    mart = query("select * from analytics.mart_revenue_performance")

    regions = st.sidebar.multiselect(
        "Region", sorted(mart["region"].unique()), default=list(mart["region"].unique())
    )
    filtered = mart[mart["region"].isin(regions)] if regions else mart

    render_kpis(filtered)
    render_variance_chart(filtered)
    render_region_breakdown(filtered)
    render_data_quality()


main()
