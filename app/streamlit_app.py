"""Revenue performance dashboard.

Five-tab layout exposing the full star schema: executive summary, revenue
variance deep-dive, delivery performance (order-grain), product & margin
analysis, and data quality reconciliation. All tabs share sidebar filters.

Reads through data_source.query(), which resolves to Snowpark, a live
Snowflake connector, or the committed DuckDB file; no code changes needed
per backend.

Colour system: one diverging axis for variance (amber below plan, teal
above plan) and one separate hue for data quality (violet, quarantine
only). Structural chrome (tabs, filter chips, sliders, focus rings)
carries no hue at all; that neutrality comes from .streamlit/config.toml's
theme tokens. app/theme.css adds the handful of layout pieces (the lede
sentence, the weighted figure cards, the reconciliation callouts) that
Streamlit's theming API cannot reach on its own.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from data_source import DataSourceError, backend_name, query

st.set_page_config(page_title="Revenue Performance", page_icon="📊", layout="wide")

CURRENCY_FORMAT = "${:,.0f}"
CURRENCY_FORMAT_PRECISE = "${:,.2f}"
PCT_FORMAT = "{:.1%}"
NULL_MARKER = "unknown"

# Kept in one place, and only here, so every chart, card and table in this
# app agrees on what each hue means: amber is the below-plan end of the
# variance axis, teal is the above-plan end, violet is data quality and
# never variance, and everything else stays neutral. Values match the
# oklch() colours in app/theme.css and .streamlit/config.toml; see the
# module docstrings there for the conversion.
COLOR_SURFACE = "#100D0A"
COLOR_SURFACE_RAISED = "#1B1815"
COLOR_BORDER = "#312D2A"
COLOR_TEXT = "#EBE7E4"
COLOR_MUTED = "#8A8581"
COLOR_AMBER = "#DC9242"
COLOR_TEAL = "#50BFBE"
COLOR_VIOLET = "#A886CF"

FONT_SANS = "'IBM Plex Sans', -apple-system, 'Segoe UI', sans-serif"
FONT_MONO = "'IBM Plex Mono', ui-monospace, 'SFMono-Regular', Menlo, monospace"

# The one diverging colourscale used for every variance visual (heatmap,
# family bar). Zero-anchored: amber at the low end, a neutral midpoint at
# zero, teal at the high end; never red, never green.
VARIANCE_COLORSCALE = [
    [0.0, COLOR_AMBER],
    [0.5, COLOR_SURFACE_RAISED],
    [1.0, COLOR_TEAL],
]

# Variance-flag colours: BELOW_PLAN / AT_OR_ABOVE_PLAN sit on the variance
# axis (amber / teal). NO_ACTUALS and NO_FORECAST are grain gaps, not a
# variance direction, so they stay neutral rather than borrowing either hue.
FLAG_COLORS = {
    "BELOW_PLAN": COLOR_AMBER,
    "AT_OR_ABOVE_PLAN": COLOR_TEAL,
    "NO_ACTUALS": COLOR_MUTED,
    "NO_FORECAST": COLOR_BORDER,
}


def _style_plot(fig: go.Figure) -> go.Figure:
    """Apply the one neutral chrome style every chart in this app shares.

    Centralised so no chart accidentally keeps Plotly's default light
    background, gridlines or font: DRY per the "chrome gets no hue" rule.
    """
    fig.update_layout(
        paper_bgcolor=COLOR_SURFACE,
        plot_bgcolor=COLOR_SURFACE,
        font=dict(family=FONT_SANS, color=COLOR_TEXT, size=13),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=COLOR_TEXT)),
        margin=dict(t=30, l=10, r=10, b=10),
    )
    fig.update_xaxes(
        gridcolor=COLOR_BORDER, zerolinecolor=COLOR_BORDER, linecolor=COLOR_BORDER,
        color=COLOR_MUTED, tickfont=dict(family=FONT_MONO, color=COLOR_MUTED),
    )
    fig.update_yaxes(
        gridcolor=COLOR_BORDER, zerolinecolor=COLOR_BORDER, linecolor=COLOR_BORDER,
        color=COLOR_MUTED, tickfont=dict(family=FONT_MONO, color=COLOR_MUTED),
    )
    return fig


def inject_theme():
    css_path = Path(__file__).resolve().parent / "theme.css"
    st.markdown(f"<style>{css_path.read_text()}</style>", unsafe_allow_html=True)


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


def figure_card(label: str, value_html: str, sub_html: str = "", size: str = "primary"):
    """Render one weighted figure card. `size` controls type scale, not colour."""
    st.markdown(
        f'<div class="figure-card">'
        f'<div class="figure-label">{label}</div>'
        f'<div class="figure-value figure-value--{size}">{value_html}</div>'
        f'<div class="figure-sub">{sub_html}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def tab_executive_summary(filtered: dict[str, pd.DataFrame], raw: dict[str, pd.DataFrame]):
    mart = filtered["mart"]

    # --- The lede: the actual story, in words, computed from the whole
    # dataset so it never contradicts the reconciliation facts below no
    # matter how the sidebar is set. -84% variance alone reads as a revenue
    # collapse; naming the unfulfilled-demand-planning story prevents that.
    mart_raw = raw["mart"]
    raw_actual = mart_raw["actual_revenue"].sum()
    raw_forecast = mart_raw["forecast_revenue"].sum()
    no_actuals_rows = int((mart_raw["variance_flag"] == "NO_ACTUALS").sum())
    total_rows = len(mart_raw)

    st.markdown(
        f'<p class="lede">Actual revenue reached '
        f'<span class="num">${raw_actual / 1e6:,.2f}M</span> against a '
        f'<span class="num">${raw_forecast / 1e6:,.2f}M</span> plan. '
        f'<span class="num">{no_actuals_rows}</span> of '
        f'<span class="num">{total_rows}</span> product-region-months received '
        f'no orders at all.</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="grain-note">Forecast and actuals below are stated at product '
        '&times; region &times; month. Delivery detail is order-grain; the two '
        'never share a row.</p>',
        unsafe_allow_html=True,
    )

    # --- Three weighted figures, not eight equal tiles. Weight is visible
    # in the column ratio and the type scale, not just a label.
    col_plan, col_delivery, col_recon = st.columns([3, 2, 2], gap="medium")

    actual = mart["actual_revenue"].sum()
    forecast = mart["forecast_revenue"].sum()
    variance = actual - forecast
    variance_pct = variance / forecast if forecast else None
    below_plan = variance < 0
    state_class = "state-amber" if below_plan else "state-teal"
    glyph = "▼" if below_plan else "▲"

    with col_plan:
        figure_card(
            "Actual vs Plan",
            f'<span class="{state_class}">{glyph}</span> {CURRENCY_FORMAT.format(actual)}',
            (
                f'against {CURRENCY_FORMAT.format(forecast)} plan &middot; '
                f'<span class="{state_class} num">'
                f'{PCT_FORMAT.format(variance_pct) if variance_pct is not None else NULL_MARKER}'
                f'</span> variance'
            ),
            size="primary",
        )

    on_time = mart["on_time_count"].sum()
    late = mart["late_count"].sum()
    delivered = on_time + late
    on_time_rate = on_time / delivered if delivered else None

    with col_delivery:
        figure_card(
            "On-Time Delivery",
            PCT_FORMAT.format(on_time_rate) if on_time_rate is not None else NULL_MARKER,
            f'{int(on_time):,} of {int(delivered):,} shipped orders, order-grain',
            size="secondary",
        )

    # Reconciliation always covers the whole source dataset, ignoring the
    # sidebar; a partially-filtered reconciliation would not prove
    # anything, the same rule tab_data_quality already follows.
    dq_raw = raw["dq"]
    orders_raw = raw["orders"]
    recognized = mart_raw["actual_revenue"].sum()
    open_rev = orders_raw[orders_raw["order_status"] == "Open"]["gross_revenue"].sum()
    cancelled_rev = orders_raw[orders_raw["order_status"] == "Cancelled"]["gross_revenue"].sum()
    rejected_rev = dq_raw["rejected_revenue"].sum()
    source_total = recognized + open_rev + cancelled_rev + rejected_rev
    rejected_share = rejected_rev / source_total if source_total else None

    with col_recon:
        figure_card(
            "Reconciliation",
            CURRENCY_FORMAT.format(source_total),
            (
                f'source revenue balances exactly &middot; '
                f'<span class="state-violet">&#9670;</span> '
                f'<span class="state-violet num">'
                f'{PCT_FORMAT.format(rejected_share) if rejected_share is not None else NULL_MARKER}'
                f'</span> quarantined, share of source revenue'
            ),
            size="secondary",
        )

    # --- The two facts that prove the engineering, surfaced here rather
    # than buried in a tab: the reconciliation balancing exactly, and the
    # one unmapped row with revenue no forecast can explain.
    unmapped = mart_raw[mart_raw["region"] == "UNMAPPED"]
    unmapped_revenue = unmapped["actual_revenue"].sum() if not unmapped.empty else 0

    st.markdown('<div class="callout-row">', unsafe_allow_html=True)
    st.markdown(
        f'<div class="callout">Reconciliation balances to the cent: recognized, '
        f'open, cancelled and quarantined revenue sum to exactly '
        f'<span class="num">{CURRENCY_FORMAT_PRECISE.format(source_total)}</span>.</div>',
        unsafe_allow_html=True,
    )
    if not unmapped.empty:
        st.markdown(
            f'<div class="callout">One row, region UNMAPPED, carries '
            f'<span class="num">{CURRENCY_FORMAT.format(unmapped_revenue)}</span> of '
            f'revenue that no forecast explains.</div>',
            unsafe_allow_html=True,
        )
    st.markdown('</div>', unsafe_allow_html=True)

    # --- The primary graphic: zero-anchored variance by region, amber to
    # the left, teal to the right. This replaces the eight-tile hero as the
    # dominant element on this tab.
    st.markdown("#### Variance by Region")
    by_region = (
        mart.groupby("region", as_index=False)
        .agg(revenue_variance=("revenue_variance", "sum"))
        .sort_values("revenue_variance")
    )
    if not by_region.empty:
        bar_colors = [COLOR_AMBER if v < 0 else COLOR_TEAL for v in by_region["revenue_variance"]]
        fig = go.Figure(
            go.Bar(
                x=by_region["revenue_variance"],
                y=by_region["region"],
                orientation="h",
                marker_color=bar_colors,
                text=[CURRENCY_FORMAT.format(v) for v in by_region["revenue_variance"]],
                textposition="outside",
            )
        )
        fig.update_layout(
            height=90 + 60 * len(by_region),
            xaxis_title="Variance ($, zero-anchored)",
            yaxis_title="",
            showlegend=False,
        )
        fig.add_vline(x=0, line_color=COLOR_BORDER, line_width=1)
        st.plotly_chart(_style_plot(fig), width="stretch")


def tab_revenue_variance(data: dict[str, pd.DataFrame]):
    mart = data["mart"]

    st.subheader("Product x Region Variance Heatmap")
    st.caption(
        "Colour intensity is the variance percentage: amber below plan, teal "
        "above plan, zero-anchored at the neutral midpoint."
    )

    pivot = mart.pivot_table(
        index="product_id",
        columns="region",
        values="revenue_variance_pct",
        aggfunc="mean",
    )
    if not pivot.empty:
        fig = px.imshow(
            pivot,
            color_continuous_scale=VARIANCE_COLORSCALE,
            color_continuous_midpoint=0,
            labels=dict(color="Variance %"),
            aspect="auto",
        )
        fig.update_layout(height=500)
        st.plotly_chart(_style_plot(fig), width="stretch")

    st.markdown("---")
    st.subheader("Actual vs Forecast by Month")
    st.caption(
        "Two neutral series, not a variance encoding on their own: the "
        "variance they imply is shown separately, above and below."
    )
    monthly = (
        mart.groupby("revenue_month", as_index=False)[["actual_revenue", "forecast_revenue"]]
        .sum()
        .sort_values("revenue_month")
    )
    monthly["revenue_month"] = pd.to_datetime(monthly["revenue_month"]).dt.strftime("%b %Y")

    fig_monthly = go.Figure()
    fig_monthly.add_trace(go.Bar(
        x=monthly["revenue_month"], y=monthly["actual_revenue"],
        name="Actual", marker_color=COLOR_TEXT,
    ))
    fig_monthly.add_trace(go.Bar(
        x=monthly["revenue_month"], y=monthly["forecast_revenue"],
        name="Forecast", marker_color=COLOR_MUTED,
        marker_pattern_shape="/",
    ))
    fig_monthly.update_layout(barmode="group", yaxis_title="Revenue", xaxis_title="")
    st.plotly_chart(_style_plot(fig_monthly), width="stretch")

    st.markdown("---")
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
            by_region.style.format(
                {
                    "actual_revenue": "${:,.0f}",
                    "forecast_revenue": "${:,.0f}",
                    "revenue_variance": "${:,.0f}",
                    "variance_pct": "{:.1%}",
                },
                na_rep=NULL_MARKER,
            ),
            width="stretch",
            hide_index=True,
        )

    with right:
        st.subheader("Variance Flag Distribution")
        st.caption(
            "NO_ACTUALS and NO_FORECAST are grain gaps, not a variance "
            "direction, so they stay neutral rather than amber or teal."
        )
        flags = mart["variance_flag"].value_counts().reset_index()
        flags.columns = ["flag", "count"]
        fig2 = px.pie(
            flags, names="flag", values="count", hole=0.4,
            color="flag", color_discrete_map=FLAG_COLORS,
        )
        fig2.update_traces(textinfo="label+percent", textfont=dict(family=FONT_SANS))
        st.plotly_chart(_style_plot(fig2), width="stretch")

    st.markdown("---")
    st.subheader("Variance by Product Family")
    by_family = (
        mart.groupby("product_family", as_index=False)
        .agg(revenue_variance=("revenue_variance", "sum"))
        .sort_values("revenue_variance")
    )
    fig3 = px.bar(
        by_family, x="product_family", y="revenue_variance",
        color="revenue_variance",
        color_continuous_scale=VARIANCE_COLORSCALE,
        color_continuous_midpoint=0,
    )
    fig3.update_layout(yaxis_title="Variance ($)", xaxis_title="")
    st.plotly_chart(_style_plot(fig3), width="stretch")

    st.markdown("---")
    left2, right2 = st.columns(2)
    with left2:
        st.markdown("**Top 5 Above Plan**")
        top = mart.nlargest(5, "revenue_variance")[
            ["product_id", "region", "revenue_month", "revenue_variance"]
        ]
        st.dataframe(
            top.style.format({"revenue_variance": "${:,.0f}"}, na_rep=NULL_MARKER),
            width="stretch", hide_index=True,
        )
    with right2:
        st.markdown("**Top 5 Below Plan**")
        bottom = mart.nsmallest(5, "revenue_variance")[
            ["product_id", "region", "revenue_month", "revenue_variance"]
        ]
        st.dataframe(
            bottom.style.format({"revenue_variance": "${:,.0f}"}, na_rep=NULL_MARKER),
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
        detail.style.format(
            {
                "actual_revenue": "${:,.0f}",
                "forecast_revenue": "${:,.0f}",
                "revenue_variance": "${:,.0f}",
                "revenue_variance_pct": "{:.1%}",
            },
            na_rep=NULL_MARKER,
        ),
        width="stretch",
        hide_index=True,
        height=400,
    )


def tab_delivery(data: dict[str, pd.DataFrame]):
    orders = data["orders"]
    shipped = orders[orders["order_status"] == "Shipped"].copy()

    st.caption(
        "Order-grain detail: every row below is one order, not a "
        "product x region x month aggregate."
    )

    left, right = st.columns(2)

    # Delivery status is operational, not variance and not data quality --
    # it stays neutral, and each slice carries its own label so hue is
    # never the only thing distinguishing one status from another.
    status_shades = {
        "On Time": COLOR_TEXT,
        "Late": COLOR_MUTED,
        "Not Delivered": COLOR_BORDER,
        "Cancelled": "#4A4540",
        "Unknown": "#211E1A",
    }

    with left:
        st.subheader("Delivery Status Distribution")
        status_counts = orders["delivery_status"].value_counts().reset_index()
        status_counts.columns = ["status", "count"]
        fig = px.pie(
            status_counts, names="status", values="count", hole=0.4,
            color="status", color_discrete_map=status_shades,
        )
        fig.update_traces(textinfo="label+percent", textfont=dict(family=FONT_SANS))
        st.plotly_chart(_style_plot(fig), width="stretch")

    with right:
        st.subheader("On-Time Rate by Region")
        delivered = shipped[shipped["delivery_status"].isin(["On Time", "Late"])]
        if not delivered.empty:
            by_region = delivered.groupby("region", as_index=False).agg(
                on_time=("delivery_status", lambda x: (x == "On Time").sum()),
                total=("delivery_status", "count"),
            )
            by_region["on_time_rate"] = by_region["on_time"] / by_region["total"]
            by_region = by_region.sort_values("on_time_rate")
            fig2 = px.bar(
                by_region, x="region", y="on_time_rate",
                text=by_region["on_time_rate"].apply(lambda x: f"{x:.0%}"),
            )
            fig2.update_traces(marker_color=COLOR_TEXT, textposition="outside")
            fig2.update_layout(yaxis_title="On-Time Rate", yaxis_tickformat=".0%", xaxis_title="")
            st.plotly_chart(_style_plot(fig2), width="stretch")

    st.markdown("---")
    st.subheader("Late Orders Detail")
    late = shipped[shipped["delivery_status"] == "Late"].copy()

    if not late.empty:
        late["actual_delivery_date"] = pd.to_datetime(late["actual_delivery_date"])
        late["promised_delivery_date"] = pd.to_datetime(late["promised_delivery_date"])
        late["days_late"] = (late["actual_delivery_date"] - late["promised_delivery_date"]).dt.days

        st.markdown(
            f'<span class="figure-value figure-value--secondary">{len(late)}</span> '
            f'<span class="figure-sub" style="display:inline">late orders</span>',
            unsafe_allow_html=True,
        )

        late_display = late[[
            "order_id", "customer_id", "product_id", "region",
            "order_date", "promised_delivery_date", "actual_delivery_date",
            "days_late", "gross_revenue",
        ]].sort_values("days_late", ascending=False)

        st.dataframe(
            late_display.style.format({"gross_revenue": "${:,.2f}"}, na_rep=NULL_MARKER),
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

    known_cost_orders = orders["has_standard_cost"].sum()
    total_orders = len(orders)
    margin_coverage = known_cost_orders / total_orders if total_orders > 0 else None
    total_margin = mart["actual_margin"].sum()

    m1, m2, m3 = st.columns(3)
    m1.metric("Orders", f"{int(total_orders):,}")
    m2.metric("Gross Margin", CURRENCY_FORMAT.format(total_margin))
    m3.metric(
        "Margin Coverage",
        PCT_FORMAT.format(margin_coverage) if margin_coverage is not None else NULL_MARKER,
    )
    st.caption(
        "Margin coverage below 100% means some orders lacked a standard "
        "cost, so those orders' margin is unknown, never rendered as zero."
    )

    st.markdown("---")
    st.subheader("Revenue by Product Family")
    by_family = (
        mart.groupby("product_family", as_index=False)
        .agg(actual_revenue=("actual_revenue", "sum"))
        .sort_values("actual_revenue", ascending=False)
    )
    fig = px.treemap(by_family, path=["product_family"], values="actual_revenue")
    fig.update_traces(marker_colors=[COLOR_SURFACE_RAISED] * len(by_family), textfont=dict(color=COLOR_TEXT))
    fig.update_layout(height=350)
    st.plotly_chart(_style_plot(fig), width="stretch")

    st.markdown("---")
    left, right = st.columns(2)

    with left:
        st.subheader("Margin by Product")
        st.caption(
            "Bars are neutral: margin coverage is not a variance or "
            "data-quality state, so it is not colour-encoded here. See the "
            "coverage figure above for the unknown share."
        )
        by_product = (
            mart.groupby("product_id", as_index=False)
            .agg(
                actual_revenue=("actual_revenue", "sum"),
                actual_margin=("actual_margin", "sum"),
            )
        )
        cost_coverage = orders.groupby("product_id").agg(
            orders_with_known_cost=("has_standard_cost", "sum"),
            order_count=("has_standard_cost", "count"),
        )
        by_product = by_product.merge(cost_coverage, on="product_id", how="left")
        by_product["margin_coverage"] = (
            by_product["orders_with_known_cost"] / by_product["order_count"].replace(0, float("nan"))
        )

        product_names = products.set_index("product_id")["product_name"]
        by_product["product_name"] = by_product["product_id"].map(product_names).fillna(by_product["product_id"])
        by_product = by_product.sort_values("actual_margin", ascending=False)

        fig2 = px.bar(
            by_product, x="product_name", y="actual_margin",
            labels={"actual_margin": "Gross Margin ($)"},
            text=by_product["margin_coverage"].apply(
                lambda c: f"{c:.0%} coverage" if pd.notna(c) else f"{NULL_MARKER} coverage"
            ),
        )
        fig2.update_traces(marker_color=COLOR_TEXT, textposition="outside")
        fig2.update_layout(xaxis_title="", xaxis_tickangle=-45)
        st.plotly_chart(_style_plot(fig2), width="stretch")

    with right:
        st.subheader("Product Lifecycle Status")
        lifecycle = products[~products["is_synthetic"]]["lifecycle_status"].value_counts().reset_index()
        lifecycle.columns = ["status", "count"]
        fig3 = px.pie(lifecycle, names="status", values="count", hole=0.4)
        fig3.update_traces(
            marker=dict(colors=[COLOR_TEXT, COLOR_MUTED, COLOR_BORDER, "#4A4540"]),
            textinfo="label+percent", textfont=dict(family=FONT_SANS),
        )
        st.plotly_chart(_style_plot(fig3), width="stretch")

        st.subheader("Products at a Glance")
        product_display = products[~products["is_synthetic"]][[
            "product_id", "product_name", "product_family",
            "lifecycle_status", "standard_cost",
        ]].sort_values("product_id")
        st.dataframe(
            product_display.style.format({"standard_cost": "${:,.2f}"}, na_rep=NULL_MARKER),
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
        "This reconciliation always covers the full dataset; it ignores the sidebar filters, since "
        "a partial reconciliation would not prove anything."
    )

    recognized = mart["actual_revenue"].sum()

    orders_all = data["orders"]
    open_rev = orders_all[orders_all["order_status"] == "Open"]["gross_revenue"].sum()
    cancelled_rev = orders_all[orders_all["order_status"] == "Cancelled"]["gross_revenue"].sum()
    rejected_rev = dq["rejected_revenue"].sum()
    source_total = recognized + open_rev + cancelled_rev + rejected_rev
    rejected_share = rejected_rev / source_total if source_total else None

    rc1, rc2, rc3, rc4 = st.columns(4)
    rc1.metric("Recognized (Shipped)", CURRENCY_FORMAT.format(recognized))
    rc2.metric("Open Pipeline", CURRENCY_FORMAT.format(open_rev))
    rc3.metric("Cancelled", CURRENCY_FORMAT.format(cancelled_rev))
    rc4.metric("Rejected (DQ)", CURRENCY_FORMAT.format(rejected_rev))
    st.markdown(
        f'<p class="section-caption"><span class="state-violet">&#9670;</span> '
        f'<span class="state-violet num">'
        f'{PCT_FORMAT.format(rejected_share) if rejected_share is not None else NULL_MARKER}'
        f'</span> share of source revenue, a data-quality cost, not a shortfall '
        f'against plan, which is why it is violet rather than the variance colours.</p>',
        unsafe_allow_html=True,
    )

    st.metric("Source Total (sum of above)", CURRENCY_FORMAT_PRECISE.format(source_total))

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
            CURRENCY_FORMAT_PRECISE.format(source_total),
        ],
        connector={"line": {"color": COLOR_BORDER}},
        increasing={"marker": {"color": COLOR_TEXT}},
        decreasing={"marker": {"color": COLOR_MUTED}},
        totals={"marker": {"color": COLOR_VIOLET}},
    ))
    fig.update_layout(title="Revenue Reconciliation Waterfall", showlegend=False, height=400)
    st.plotly_chart(_style_plot(fig), width="stretch")

    st.markdown("---")
    left, right = st.columns(2)

    with left:
        st.subheader("Reject Summary")
        st.dataframe(
            dq.style.format(
                {"rejected_revenue": "${:,.2f}", "pct_of_source_rows": "{:.2%}"},
                na_rep=NULL_MARKER,
            ),
            width="stretch",
            hide_index=True,
        )

    with right:
        st.subheader("Rejected Revenue by Reason")
        st.caption("Violet throughout: this chart is entirely data-quality scope.")
        dq_valid = dq[dq["rejected_revenue"].notna()]
        if not dq_valid.empty:
            fig2 = px.bar(
                dq_valid,
                x="dq_failure_reason", y="rejected_revenue",
                text=dq_valid["rejected_revenue"].apply(lambda x: f"${x:,.0f}"),
            )
            fig2.update_traces(marker_color=COLOR_VIOLET, textposition="outside")
            fig2.update_layout(showlegend=False, yaxis_title="Rejected Revenue ($)", xaxis_title="")
            st.plotly_chart(_style_plot(fig2), width="stretch")

    st.markdown("---")
    st.subheader("Rejected Rows Detail")
    st.caption("Full payload of every rejected row, with the reason it was excluded.")
    st.dataframe(
        rejects.style.format(
            {"gross_revenue": "${:,.2f}", "unit_price": "${:,.2f}"},
            na_rep=NULL_MARKER,
        ),
        width="stretch",
        hide_index=True,
    )


def main():
    try:
        inject_theme()
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
            tab_executive_summary(filtered, data)
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
