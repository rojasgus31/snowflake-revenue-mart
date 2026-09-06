"""Revenue performance dashboard.

Reads the `analytics` mart through `data_source.py`, which resolves at
runtime to a live Snowflake connection (a Snowpark session when deployed as
Streamlit in Snowflake, or SNOWFLAKE_* environment settings otherwise) or,
failing both, the committed DuckDB file -- so the app keeps working with no
warehouse credentials and after the Snowflake trial expires. Read-only in
every case: the dashboard is a consumer of the mart, never a writer.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from data_source import DataSourceError, backend_name, query

st.set_page_config(page_title="Revenue Performance", page_icon="📊", layout="wide")


def render_header() -> None:
    st.title("Revenue Performance")
    st.caption(
        "Actual against plan at product x region x month. Per-order delivery "
        "detail lives in fct_orders — the two grains are deliberately separate."
    )
    st.caption(f"Data source: {backend_name()}")


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
    try:
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
    except DataSourceError as error:
        st.error(str(error))
        return


main()
