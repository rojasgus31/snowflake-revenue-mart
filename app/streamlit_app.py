"""Revenue performance dashboard.

Five-tab layout exposing the full star schema: executive summary, revenue
variance deep-dive, delivery performance (order-grain), product & margin
analysis, and data quality reconciliation. All tabs share sidebar filters.

Reads through data_source.query(), which resolves to Snowpark, a live
Snowflake connector, or the committed DuckDB file -- no code changes needed
per backend.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from data_source import DataSourceError, backend_name, query

st.set_page_config(page_title="Revenue Performance", page_icon="📊", layout="wide")

CURRENCY_FORMAT = "${:,.0f}"
PCT_FORMAT = "{:.1%}"


def load_data() -> dict[str, pd.DataFrame]:
    mart = query("select * from analytics.mart_revenue_performance")
    orders = query("select * from analytics.fct_orders")
    products = query("select * from analytics.dim_product")
    dq = query("select * from analytics.dq_reject_summary order by rejected_row_count desc")
    rejects_detail = query(
        "select order_id, customer_id, product_id, quantity, unit_price, "
        "gross_revenue, order_status, dq_failure_reason "
        "from staging.stg_oracle__orders_rejects"
    )
    return {
        "mart": mart,
        "orders": orders,
        "products": products,
        "dq": dq,
        "rejects_detail": rejects_detail,
    }


def apply_filters(
    data: dict[str, pd.DataFrame],
    regions: list[str],
    families: list[str],
    months: tuple,
) -> dict[str, pd.DataFrame]:
    filtered = {}
    mart = data["mart"]
    orders = data["orders"]

    if regions:
        mart = mart[mart["region"].isin(regions)]
        orders = orders[orders["region"].isin(regions)]

    products_df = data["products"]
    family_map = products_df.set_index("product_id")["product_family"]

    if families:
        product_ids = products_df[products_df["product_family"].isin(families)]["product_id"]
        mart = mart[mart["product_id"].isin(product_ids)]
        orders = orders[orders["product_id"].isin(product_ids)]

    if months and len(months) == 2:
        mart_months = pd.to_datetime(mart["revenue_month"])
        mart = mart[(mart_months >= months[0]) & (mart_months <= months[1])]
        order_months = pd.to_datetime(orders["revenue_month"])
        orders = orders[(order_months >= months[0]) & (order_months <= months[1])]

    mart = mart.copy()
    mart["product_family"] = mart["product_id"].map(family_map).fillna("Unknown")

    orders = orders.copy()
    orders["product_family"] = orders["product_id"].map(family_map).fillna("Unknown")

    filtered["mart"] = mart
    filtered["orders"] = orders
    filtered["products"] = data["products"]
    filtered["dq"] = data["dq"]
    filtered["rejects_detail"] = data["rejects_detail"]
    return filtered


def render_sidebar(data: dict[str, pd.DataFrame]):
    st.sidebar.title("Filters")
    st.sidebar.caption(f"Source: {backend_name()}")

    all_regions = sorted(data["mart"]["region"].unique())
    regions = st.sidebar.multiselect("Region", all_regions, default=all_regions)

    all_families = sorted(data["products"]["product_family"].unique())
    families = st.sidebar.multiselect("Product Family", all_families, default=all_families)

    all_months = sorted(pd.to_datetime(data["mart"]["revenue_month"]).unique())
    if len(all_months) >= 2:
        month_range = st.sidebar.select_slider(
            "Month Range",
            options=all_months,
            value=(all_months[0], all_months[-1]),
            format_func=lambda x: pd.Timestamp(x).strftime("%b %Y"),
        )
    else:
        month_range = (all_months[0], all_months[0]) if all_months else None

    return regions, families, month_range


def tab_executive_summary(data: dict[str, pd.DataFrame]):
    mart = data["mart"]
    orders = data["orders"]

    actual = mart["actual_revenue"].sum()
    forecast = mart["forecast_revenue"].sum()
    variance = actual - forecast
    on_time = mart["on_time_count"].sum()
    late = mart["late_count"].sum()
    delivered = on_time + late
    margin = mart["actual_margin"].sum()

    # mart_revenue_performance does not carry a per-row known-cost count (it
    # only exposes the already-computed margin_coverage_pct), so coverage is
    # derived from fct_orders' own has_standard_cost flag instead -- the same
    # population dbt used to compute orders_with_known_cost upstream.
    known_cost_orders = orders["has_standard_cost"].sum()
    total_orders = len(orders)
    margin_coverage = known_cost_orders / total_orders if total_orders > 0 else 0

    dq = data["dq"]
    rejected_revenue = dq["rejected_revenue"].sum()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Actual Revenue", CURRENCY_FORMAT.format(actual))
    c2.metric("Forecast Revenue", CURRENCY_FORMAT.format(forecast))
    c3.metric(
        "Variance",
        CURRENCY_FORMAT.format(variance),
        delta=PCT_FORMAT.format(variance / forecast) if forecast else None,
    )
    c4.metric(
        "On-Time Delivery",
        PCT_FORMAT.format(on_time / delivered) if delivered else "n/a",
    )

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Gross Margin", CURRENCY_FORMAT.format(margin))
    c6.metric("Margin Coverage", PCT_FORMAT.format(margin_coverage))
    c7.metric("Orders", f"{int(total_orders):,}")
    c8.metric(
        "Rejected Revenue",
        CURRENCY_FORMAT.format(rejected_revenue),
        delta=f"-{rejected_revenue / (actual + rejected_revenue):.2%} of source"
        if (actual + rejected_revenue) > 0
        else None,
        delta_color="inverse",
    )

    st.markdown("---")
    st.subheader("Actual vs Forecast by Month")

    monthly = (
        mart.groupby("revenue_month", as_index=False)[["actual_revenue", "forecast_revenue"]]
        .sum()
        .sort_values("revenue_month")
    )
    monthly["revenue_month"] = pd.to_datetime(monthly["revenue_month"]).dt.strftime("%b %Y")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=monthly["revenue_month"], y=monthly["actual_revenue"],
        name="Actual", marker_color="#636EFA",
    ))
    fig.add_trace(go.Bar(
        x=monthly["revenue_month"], y=monthly["forecast_revenue"],
        name="Forecast", marker_color="#EF553B",
    ))
    fig.update_layout(barmode="group", yaxis_title="Revenue", xaxis_title="")
    st.plotly_chart(fig, width="stretch")

    left, right = st.columns(2)

    with left:
        st.subheader("Variance by Region")
        by_region = (
            mart.groupby("region", as_index=False)
            .agg(
                actual_revenue=("actual_revenue", "sum"),
                forecast_revenue=("forecast_revenue", "sum"),
                revenue_variance=("revenue_variance", "sum"),
            )
            .sort_values("revenue_variance")
        )
        by_region["variance_pct"] = (
            by_region["revenue_variance"] / by_region["forecast_revenue"].replace(0, float("nan"))
        )
        st.dataframe(
            by_region.style.format({
                "actual_revenue": "${:,.0f}",
                "forecast_revenue": "${:,.0f}",
                "revenue_variance": "${:,.0f}",
                "variance_pct": "{:.1%}",
            }),
            width="stretch",
            hide_index=True,
        )

    with right:
        st.subheader("Variance Flag Distribution")
        flags = mart["variance_flag"].value_counts().reset_index()
        flags.columns = ["flag", "count"]
        fig2 = px.pie(flags, names="flag", values="count", hole=0.4)
        st.plotly_chart(fig2, width="stretch")


def tab_revenue_variance(data: dict[str, pd.DataFrame]):
    mart = data["mart"]

    st.subheader("Product x Region Variance Heatmap")
    st.caption("Color intensity = variance percentage. Red = below plan, blue = above plan.")

    pivot = mart.pivot_table(
        index="product_id",
        columns="region",
        values="revenue_variance_pct",
        aggfunc="mean",
    )
    if not pivot.empty:
        fig = px.imshow(
            pivot,
            color_continuous_scale="RdBu",
            color_continuous_midpoint=0,
            labels=dict(color="Variance %"),
            aspect="auto",
        )
        fig.update_layout(height=500)
        st.plotly_chart(fig, width="stretch")

    st.markdown("---")
    left, right = st.columns(2)

    with left:
        st.subheader("Variance by Product Family")
        by_family = (
            mart.groupby("product_family", as_index=False)
            .agg(
                actual_revenue=("actual_revenue", "sum"),
                forecast_revenue=("forecast_revenue", "sum"),
                revenue_variance=("revenue_variance", "sum"),
            )
            .sort_values("revenue_variance")
        )
        fig3 = px.bar(
            by_family, x="product_family", y="revenue_variance",
            color="revenue_variance",
            color_continuous_scale="RdBu",
            color_continuous_midpoint=0,
        )
        fig3.update_layout(yaxis_title="Variance ($)", xaxis_title="")
        st.plotly_chart(fig3, width="stretch")

    with right:
        st.subheader("Top Under/Over Performers")
        top = mart.nlargest(5, "revenue_variance")[["product_id", "region", "revenue_month", "revenue_variance"]]
        bottom = mart.nsmallest(5, "revenue_variance")[["product_id", "region", "revenue_month", "revenue_variance"]]
        st.markdown("**Top 5 Above Plan**")
        st.dataframe(
            top.style.format({"revenue_variance": "${:,.0f}"}),
            width="stretch", hide_index=True,
        )
        st.markdown("**Top 5 Below Plan**")
        st.dataframe(
            bottom.style.format({"revenue_variance": "${:,.0f}"}),
            width="stretch", hide_index=True,
        )

    st.markdown("---")
    st.subheader("Detail Table")

    flag_filter = st.multiselect(
        "Variance Flag",
        sorted(mart["variance_flag"].unique()),
        default=list(mart["variance_flag"].unique()),
    )
    detail = mart[mart["variance_flag"].isin(flag_filter)][[
        "product_id", "product_family", "region", "revenue_month",
        "actual_revenue", "forecast_revenue", "revenue_variance",
        "revenue_variance_pct", "variance_flag",
    ]].sort_values("revenue_variance")

    st.dataframe(
        detail.style.format({
            "actual_revenue": "${:,.0f}",
            "forecast_revenue": "${:,.0f}",
            "revenue_variance": "${:,.0f}",
            "revenue_variance_pct": "{:.1%}",
        }),
        width="stretch",
        hide_index=True,
        height=400,
    )


def tab_delivery(data: dict[str, pd.DataFrame]):
    orders = data["orders"]
    shipped = orders[orders["order_status"] == "Shipped"].copy()

    left, right = st.columns(2)

    with left:
        st.subheader("Delivery Status Distribution")
        status_counts = orders["delivery_status"].value_counts().reset_index()
        status_counts.columns = ["status", "count"]
        color_map = {
            "On Time": "#2ecc71", "Late": "#e74c3c",
            "Not Delivered": "#95a5a6", "Cancelled": "#f39c12", "Unknown": "#9b59b6",
        }
        fig = px.pie(
            status_counts, names="status", values="count", hole=0.4,
            color="status", color_discrete_map=color_map,
        )
        st.plotly_chart(fig, width="stretch")

    with right:
        st.subheader("On-Time Rate by Region")
        delivered = shipped[shipped["delivery_status"].isin(["On Time", "Late"])]
        if not delivered.empty:
            by_region = delivered.groupby("region", as_index=False).agg(
                on_time=("delivery_status", lambda x: (x == "On Time").sum()),
                total=("delivery_status", "count"),
            )
            by_region["on_time_rate"] = by_region["on_time"] / by_region["total"]
            fig2 = px.bar(
                by_region.sort_values("on_time_rate"),
                x="region", y="on_time_rate",
                text=by_region.sort_values("on_time_rate")["on_time_rate"].apply(
                    lambda x: f"{x:.0%}"
                ),
            )
            fig2.update_layout(yaxis_title="On-Time Rate", yaxis_tickformat=".0%", xaxis_title="")
            fig2.update_traces(textposition="outside")
            st.plotly_chart(fig2, width="stretch")

    st.markdown("---")
    st.subheader("Late Orders Detail")
    late = shipped[shipped["delivery_status"] == "Late"].copy()

    if not late.empty:
        late["actual_delivery_date"] = pd.to_datetime(late["actual_delivery_date"])
        late["promised_delivery_date"] = pd.to_datetime(late["promised_delivery_date"])
        late["days_late"] = (late["actual_delivery_date"] - late["promised_delivery_date"]).dt.days

        st.metric("Late Orders", len(late))

        late_display = late[[
            "order_id", "customer_id", "product_id", "region",
            "order_date", "promised_delivery_date", "actual_delivery_date",
            "days_late", "gross_revenue",
        ]].sort_values("days_late", ascending=False)

        st.dataframe(
            late_display.style.format({"gross_revenue": "${:,.2f}"}),
            width="stretch",
            hide_index=True,
            height=400,
        )
    else:
        st.info("No late orders in the current filter selection.")


def tab_product_margin(data: dict[str, pd.DataFrame]):
    mart = data["mart"]
    products = data["products"]
    orders = data["orders"]

    st.subheader("Revenue by Product Family")
    by_family = (
        mart.groupby("product_family", as_index=False)
        .agg(actual_revenue=("actual_revenue", "sum"))
        .sort_values("actual_revenue", ascending=False)
    )
    fig = px.treemap(by_family, path=["product_family"], values="actual_revenue")
    fig.update_layout(height=350)
    st.plotly_chart(fig, width="stretch")

    st.markdown("---")
    left, right = st.columns(2)

    with left:
        st.subheader("Margin by Product")
        st.caption("Margin coverage < 100% means some orders lacked standard cost -- the total is partial.")
        by_product = (
            mart.groupby("product_id", as_index=False)
            .agg(
                actual_revenue=("actual_revenue", "sum"),
                actual_margin=("actual_margin", "sum"),
            )
        )
        # mart_revenue_performance has no per-row known-cost count (only the
        # pre-computed margin_coverage_pct), so coverage is derived here from
        # fct_orders' own has_standard_cost flag, grouped the same way dbt
        # grouped it upstream (all orders, regardless of shipped status).
        cost_coverage = orders.groupby("product_id").agg(
            orders_with_known_cost=("has_standard_cost", "sum"),
            order_count=("has_standard_cost", "count"),
        )
        by_product = by_product.merge(cost_coverage, on="product_id", how="left")
        by_product["margin_pct"] = by_product["actual_margin"] / by_product["actual_revenue"].replace(0, float("nan"))
        by_product["margin_coverage"] = by_product["orders_with_known_cost"] / by_product["order_count"].replace(0, float("nan"))

        product_names = products.set_index("product_id")["product_name"]
        by_product["product_name"] = by_product["product_id"].map(product_names).fillna(by_product["product_id"])

        fig2 = px.bar(
            by_product.sort_values("actual_margin", ascending=False),
            x="product_name", y="actual_margin",
            color="margin_coverage",
            color_continuous_scale="YlGn",
            labels={"actual_margin": "Gross Margin ($)", "margin_coverage": "Coverage"},
        )
        fig2.update_layout(xaxis_title="", xaxis_tickangle=-45)
        st.plotly_chart(fig2, width="stretch")

    with right:
        st.subheader("Product Lifecycle Status")
        lifecycle = products[~products["is_synthetic"]]["lifecycle_status"].value_counts().reset_index()
        lifecycle.columns = ["status", "count"]
        fig3 = px.pie(lifecycle, names="status", values="count", hole=0.4)
        st.plotly_chart(fig3, width="stretch")

        st.subheader("Products at a Glance")
        product_display = products[~products["is_synthetic"]][[
            "product_id", "product_name", "product_family",
            "lifecycle_status", "standard_cost",
        ]].sort_values("product_id")
        st.dataframe(
            product_display.style.format({"standard_cost": "${:,.2f}"}),
            width="stretch",
            hide_index=True,
        )


def tab_data_quality(data: dict[str, pd.DataFrame]):
    dq = data["dq"]
    rejects = data["rejects_detail"]
    mart = data["mart"]

    st.subheader("Reconciliation Proof")
    st.caption(
        "Every source dollar lands in exactly one bucket. If this stops balancing, dbt build fails. "
        "This reconciliation always covers the full dataset -- it ignores the sidebar filters, since "
        "a partial reconciliation would not prove anything."
    )

    recognized = mart["actual_revenue"].sum()

    orders_all = data["orders"]
    open_rev = orders_all[orders_all["order_status"] == "Open"]["gross_revenue"].sum()
    cancelled_rev = orders_all[orders_all["order_status"] == "Cancelled"]["gross_revenue"].sum()
    rejected_rev = dq["rejected_revenue"].sum()
    source_total = recognized + open_rev + cancelled_rev + rejected_rev

    rc1, rc2, rc3, rc4 = st.columns(4)
    rc1.metric("Recognized (Shipped)", CURRENCY_FORMAT.format(recognized))
    rc2.metric("Open Pipeline", CURRENCY_FORMAT.format(open_rev))
    rc3.metric("Cancelled", CURRENCY_FORMAT.format(cancelled_rev))
    rc4.metric("Rejected (DQ)", CURRENCY_FORMAT.format(rejected_rev))

    st.metric("Source Total (sum of above)", CURRENCY_FORMAT.format(source_total))

    fig = go.Figure(go.Waterfall(
        x=["Recognized", "Open", "Cancelled", "Rejected", "Source Total"],
        y=[recognized, open_rev, cancelled_rev, rejected_rev, 0],
        measure=["relative", "relative", "relative", "relative", "total"],
        textposition="outside",
        text=[
            CURRENCY_FORMAT.format(recognized),
            CURRENCY_FORMAT.format(open_rev),
            CURRENCY_FORMAT.format(cancelled_rev),
            CURRENCY_FORMAT.format(rejected_rev),
            CURRENCY_FORMAT.format(source_total),
        ],
        connector={"line": {"color": "rgb(63, 63, 63)"}},
    ))
    fig.update_layout(title="Revenue Reconciliation Waterfall", showlegend=False, height=400)
    st.plotly_chart(fig, width="stretch")

    st.markdown("---")
    left, right = st.columns(2)

    with left:
        st.subheader("Reject Summary")
        st.dataframe(
            dq.style.format({
                "rejected_revenue": "${:,.2f}",
                "pct_of_source_rows": "{:.2%}",
            }),
            width="stretch",
            hide_index=True,
        )

    with right:
        st.subheader("Rejected Revenue by Reason")
        dq_valid = dq[dq["rejected_revenue"].notna()]
        if not dq_valid.empty:
            fig2 = px.bar(
                dq_valid,
                x="dq_failure_reason", y="rejected_revenue",
                color="dq_failure_reason",
                text=dq_valid["rejected_revenue"].apply(lambda x: f"${x:,.0f}"),
            )
            fig2.update_layout(showlegend=False, yaxis_title="Rejected Revenue ($)", xaxis_title="")
            fig2.update_traces(textposition="outside")
            st.plotly_chart(fig2, width="stretch")

    st.markdown("---")
    st.subheader("Rejected Rows Detail")
    st.caption("Full payload of every rejected row, with the reason it was excluded.")
    st.dataframe(
        rejects.style.format({
            "gross_revenue": "${:,.2f}",
            "unit_price": "${:,.2f}",
        }),
        width="stretch",
        hide_index=True,
    )


def main():
    try:
        st.title("Revenue Performance")
        st.caption(
            "Actual vs plan at product x region x month, with order-level delivery "
            "detail and full DQ reconciliation."
        )

        data = load_data()
        regions, families, months = render_sidebar(data)
        filtered = apply_filters(data, regions, families, months)

        tabs = st.tabs([
            "Executive Summary",
            "Revenue Variance",
            "Delivery Performance",
            "Product & Margin",
            "Data Quality",
        ])

        with tabs[0]:
            tab_executive_summary(filtered)
        with tabs[1]:
            tab_revenue_variance(filtered)
        with tabs[2]:
            tab_delivery(filtered)
        with tabs[3]:
            tab_product_margin(filtered)
        with tabs[4]:
            # Unfiltered `data`, not `filtered`: the reconciliation proof must
            # cover the whole source dataset regardless of the sidebar's
            # region/family/month selections, or the totals stop reconciling.
            tab_data_quality(data)

    except DataSourceError as error:
        st.error(str(error))


main()
