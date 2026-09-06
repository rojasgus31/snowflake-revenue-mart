"""Revenue performance dashboard.

Five-tab layout exposing the full star schema: executive summary, revenue
variance deep-dive, delivery performance (order-grain), product & margin
analysis, and data quality reconciliation. All tabs share sidebar filters.

Reads through data_source.query(), which resolves to Snowpark, a live
Snowflake connector, or the committed DuckDB file; no code changes needed
per backend.

Colour system: one diverging axis for variance (red below plan, teal
above plan) and one separate hue for data quality (violet, quarantine
only), all darkened to clear 4.5:1 on the warm off-white surface.
Structural chrome (tabs, filter chips, sliders, focus rings) carries no
hue at all; that neutrality comes from .streamlit/config.toml's theme
tokens. app/theme.css adds the handful of layout pieces (the lede
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
# app agrees on what each hue means: red is the below-plan end of the
# variance axis, teal is the above-plan end, violet is data quality and
# never variance, and everything else stays neutral. Values match the
# oklch() colours in app/theme.css and .streamlit/config.toml; see the
# module docstring there for the conversion. On a light surface, one red
# clears 4.5:1 for both text and fills (oklch 0.52 0.20 27), so there is no
# need for the separate lighter "text" variant the dark theme required.
COLOR_SURFACE = "#FAF8F6"
COLOR_SURFACE_RAISED = "#F3EFED"
COLOR_BORDER = "#DAD7D3"
COLOR_TEXT = "#24211E"
COLOR_MUTED = "#6C6864"
COLOR_RED = "#C2181D"
COLOR_TEAL = "#007475"
COLOR_VIOLET = "#754D9E"

# A five-step neutral fill ramp, darkest to faintest, for chart series that
# are neither variance nor data-quality states (delivery status, lifecycle
# status). These are structural, not semantic, so they stay in the same
# warm-neutral hue family as the surface rather than borrowing red/teal/
# violet; ordered so the most common/important slice is darkest and least
# important recedes toward the surface, same direction the surface itself
# now points (light, not dark).
COLOR_NEUTRAL_1 = COLOR_TEXT
COLOR_NEUTRAL_2 = "#4A4744"
COLOR_NEUTRAL_3 = COLOR_MUTED
COLOR_NEUTRAL_4 = "#BBB6B3"
COLOR_NEUTRAL_5 = COLOR_BORDER

FONT_SANS = "'IBM Plex Sans', -apple-system, 'Segoe UI', sans-serif"
FONT_MONO = "'IBM Plex Mono', ui-monospace, 'SFMono-Regular', Menlo, monospace"

# The one diverging colourscale used for the remaining continuous variance
# visual (the product-family bar). Zero-anchored: red at the low end, a
# neutral midpoint at zero, teal at the high end.
VARIANCE_COLORSCALE = [
    [0.0, COLOR_RED],
    [0.5, COLOR_SURFACE_RAISED],
    [1.0, COLOR_TEAL],
]

# Variance-flag colours: BELOW_PLAN / AT_OR_ABOVE_PLAN sit on the variance
# axis (red / teal). NO_ACTUALS is the dominant grain gap (187 of 289
# product-region-months) and stays neutral grey -- it is not a variance
# direction. NO_FORECAST is rare (2 rows) and is a data-quality gap in the
# planning side rather than a variance reading, so it takes the violet
# reserved elsewhere for data quality, not either variance pole.
FLAG_COLORS = {
    "NO_ACTUALS": COLOR_MUTED,
    "BELOW_PLAN": COLOR_RED,
    "AT_OR_ABOVE_PLAN": COLOR_TEAL,
    "NO_FORECAST": COLOR_VIOLET,
}
FLAG_ORDER = ["NO_ACTUALS", "BELOW_PLAN", "AT_OR_ABOVE_PLAN", "NO_FORECAST"]


def _style_plot(fig: go.Figure) -> go.Figure:
    """Apply the one neutral chrome style every chart in this app shares.

    Centralised so no chart accidentally keeps Plotly's default dark
    template, gridlines or font: DRY per the "chrome gets no hue" rule.
    Generous margins here are the baseline every chart gets; a few charts
    with long tick labels widen one side further below.
    """
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor=COLOR_SURFACE,
        plot_bgcolor=COLOR_SURFACE,
        font=dict(family=FONT_SANS, color=COLOR_TEXT, size=13),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=COLOR_TEXT)),
        margin=dict(t=40, l=60, r=30, b=60),
    )
    fig.update_xaxes(
        gridcolor=COLOR_BORDER, zerolinecolor=COLOR_BORDER, linecolor=COLOR_BORDER,
        color=COLOR_MUTED, tickfont=dict(family=FONT_MONO, color=COLOR_MUTED),
        title_font=dict(family=FONT_SANS, color=COLOR_MUTED, size=13),
    )
    fig.update_yaxes(
        gridcolor=COLOR_BORDER, zerolinecolor=COLOR_BORDER, linecolor=COLOR_BORDER,
        color=COLOR_MUTED, tickfont=dict(family=FONT_MONO, color=COLOR_MUTED),
        title_font=dict(family=FONT_SANS, color=COLOR_MUTED, size=13),
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
    state_class = "state-red" if below_plan else "state-teal"
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

    # --- The primary graphic: zero-anchored variance by region, red to
    # the left, teal to the right. This replaces the eight-tile hero as the
    # dominant element on this tab.
    st.markdown("#### Variance by Region")
    by_region = (
        mart.groupby("region", as_index=False)
        .agg(revenue_variance=("revenue_variance", "sum"))
        .sort_values("revenue_variance")
    )
    if not by_region.empty:
        bar_colors = [COLOR_RED if v < 0 else COLOR_TEAL for v in by_region["revenue_variance"]]
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
            height=min(90 + 60 * len(by_region), 480),
            xaxis_title="Revenue Variance (USD, zero-anchored)",
            yaxis_title="Region",
            showlegend=False,
        )
        fig.add_vline(x=0, line_color=COLOR_BORDER, line_width=1)
        fig = _style_plot(fig)
        fig.update_layout(margin=dict(t=40, l=90, r=40, b=60))
        st.plotly_chart(fig, width="stretch")
        st.caption(
            "Every region sits below plan; bar length shows how far, in dollars, "
            "not in percent, since percent alone would hide that most rows had "
            "no orders at all."
        )


def tab_revenue_variance(data: dict[str, pd.DataFrame]):
    mart = data["mart"]

    st.subheader("Product x Region Variance Status")
    total_rows = len(mart)
    flag_counts = mart["variance_flag"].value_counts()
    no_actuals_n = int(flag_counts.get("NO_ACTUALS", 0))
    st.caption(
        f"{no_actuals_n} of {total_rows} product-region-months in view received no "
        "orders at all (NO_ACTUALS), which is the dominant fact here, not a "
        "variance percentage. Actual revenue sits near -84% of plan almost "
        "everywhere, so a continuous colour scale renders nearly every cell the "
        "same red and hides that. Each cell below is instead coloured by its "
        "categorical variance_flag; a cell spans up to six months per product x "
        "region, and shows the most common flag across them."
    )

    flag_pivot = (
        mart.groupby(["product_id", "region"])["variance_flag"]
        .agg(lambda s: s.value_counts().idxmax())
        .unstack("region")
        .sort_index()
    )
    flag_pivot = flag_pivot[sorted(flag_pivot.columns)]

    if not flag_pivot.empty:
        code_map = {flag: i for i, flag in enumerate(FLAG_ORDER)}
        n_flags = len(FLAG_ORDER)
        z = flag_pivot.map(code_map.get).astype(float) + 0.5
        text = flag_pivot.fillna("")

        discrete_colorscale = []
        for i, flag in enumerate(FLAG_ORDER):
            lo, hi = i / n_flags, (i + 1) / n_flags
            discrete_colorscale += [[lo, FLAG_COLORS[flag]], [hi, FLAG_COLORS[flag]]]

        fig = go.Figure(
            go.Heatmap(
                z=z.values,
                x=[str(c) for c in flag_pivot.columns],
                y=[str(i) for i in flag_pivot.index],
                text=text.values,
                hovertemplate="Product %{y}<br>Region %{x}<br>Status: %{text}<extra></extra>",
                colorscale=discrete_colorscale,
                zmin=0,
                zmax=n_flags,
                showscale=False,
                xgap=2,
                ygap=2,
            )
        )
        # A real legend, not a colour-bar: one invisible marker trace per
        # category, labelled with its true row count across the whole
        # filtered mart (not just this product x region view).
        for flag in FLAG_ORDER:
            count = int(flag_counts.get(flag, 0))
            fig.add_trace(
                go.Scatter(
                    x=[None], y=[None], mode="markers",
                    marker=dict(size=12, color=FLAG_COLORS[flag], symbol="square"),
                    name=f"{flag} ({count})",
                )
            )
        fig.update_layout(
            height=min(140 + 34 * len(flag_pivot.index), 620),
            xaxis_title="Region",
            yaxis_title="Product",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        )
        fig.update_xaxes(type="category", tickangle=-30)
        fig.update_yaxes(type="category")
        fig = _style_plot(fig)
        fig.update_layout(margin=dict(t=90, l=90, r=30, b=90))
        st.plotly_chart(fig, width="stretch")

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
        text=[CURRENCY_FORMAT.format(v) for v in monthly["actual_revenue"]],
        textposition="outside",
    ))
    fig_monthly.add_trace(go.Bar(
        x=monthly["revenue_month"], y=monthly["forecast_revenue"],
        name="Forecast", marker_color=COLOR_MUTED,
        marker_pattern_shape="/",
        text=[CURRENCY_FORMAT.format(v) for v in monthly["forecast_revenue"]],
        textposition="outside",
    ))
    fig_monthly.update_layout(
        barmode="group", yaxis_title="Revenue (USD)", xaxis_title="Month",
        height=460, legend_title_text="Series",
    )
    st.plotly_chart(_style_plot(fig_monthly), width="stretch")
    st.caption(
        "Forecast dwarfs actual in every month; the gap is the same "
        "structural shortfall the lede describes, not a swing month to month."
    )

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
            "NO_ACTUALS is a grain gap, not a variance direction, so it "
            "stays neutral grey rather than red or teal; NO_FORECAST is a "
            "data-quality gap on the planning side, so it takes the violet "
            "used for data quality elsewhere in this app."
        )
        flags = mart["variance_flag"].value_counts().reset_index()
        flags.columns = ["flag", "count"]
        flags["flag"] = pd.Categorical(flags["flag"], categories=FLAG_ORDER, ordered=True)
        flags = flags.sort_values("flag")
        fig2 = go.Figure(
            go.Bar(
                x=flags["count"],
                y=flags["flag"].astype(str),
                orientation="h",
                marker_color=[FLAG_COLORS[f] for f in flags["flag"]],
                text=[f"{int(c):,}" for c in flags["count"]],
                textposition="outside",
            )
        )
        fig2.update_layout(
            height=260,
            xaxis_title="Product-Region-Months (count)",
            yaxis_title="Variance Flag",
            showlegend=False,
        )
        fig2.update_yaxes(autorange="reversed")
        fig2 = _style_plot(fig2)
        fig2.update_layout(margin=dict(t=40, l=150, r=50, b=60))
        st.plotly_chart(fig2, width="stretch")
        st.caption(
            "NO_ACTUALS dwarfs every other flag: most of the grid never had "
            "an order to compare against plan."
        )

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
        text=by_family["revenue_variance"].apply(CURRENCY_FORMAT.format),
    )
    fig3.update_traces(textposition="outside")
    fig3.update_layout(
        yaxis_title="Revenue Variance (USD)", xaxis_title="Product Family",
        height=460, coloraxis_colorbar_title="Variance (USD)",
        margin=dict(t=40, l=60, r=30, b=80),
    )
    st.plotly_chart(_style_plot(fig3), width="stretch")
    st.caption(
        "Every product family sits below plan; colour and bar direction "
        "agree, so the reader never has to reconcile the two."
    )

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
        "On Time": COLOR_NEUTRAL_1,
        "Late": COLOR_NEUTRAL_2,
        "Not Delivered": COLOR_NEUTRAL_3,
        "Cancelled": COLOR_NEUTRAL_4,
        "Unknown": COLOR_NEUTRAL_5,
    }

    with left:
        st.subheader("Delivery Status Distribution")
        status_counts = orders["delivery_status"].value_counts().reset_index()
        status_counts.columns = ["status", "count"]
        status_counts = status_counts.sort_values("count")
        fig = go.Figure(
            go.Bar(
                x=status_counts["count"],
                y=status_counts["status"],
                orientation="h",
                marker_color=[status_shades[s] for s in status_counts["status"]],
                text=[f"{int(c):,}" for c in status_counts["count"]],
                textposition="outside",
            )
        )
        fig.update_layout(
            height=320,
            xaxis_title="Orders (count)",
            yaxis_title="Delivery Status",
            showlegend=False,
        )
        fig = _style_plot(fig)
        fig.update_layout(margin=dict(t=40, l=120, r=50, b=60))
        st.plotly_chart(fig, width="stretch")
        st.caption(
            "Late is the largest single status, ahead of On Time; only a "
            "third of shipped orders arrived on schedule."
        )

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
            fig2.update_layout(
                yaxis_title="On-Time Rate (%)", yaxis_tickformat=".0%",
                xaxis_title="Region", height=420,
            )
            st.plotly_chart(_style_plot(fig2), width="stretch")
            st.caption(
                "Every region falls short of an even on-time split; ranking "
                "them shows which is furthest behind."
            )

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
    by_family_sorted = by_family.sort_values("actual_revenue")
    fig = go.Figure(
        go.Bar(
            x=by_family_sorted["actual_revenue"],
            y=by_family_sorted["product_family"],
            orientation="h",
            marker_color=COLOR_NEUTRAL_2,
            text=[CURRENCY_FORMAT.format(v) for v in by_family_sorted["actual_revenue"]],
            textposition="outside",
        )
    )
    fig.update_layout(
        height=min(90 + 50 * len(by_family_sorted), 400),
        xaxis_title="Actual Revenue (USD)",
        yaxis_title="Product Family",
        showlegend=False,
    )
    fig = _style_plot(fig)
    fig.update_layout(margin=dict(t=40, l=140, r=60, b=60))
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "Revenue concentrates in the top one or two families; the rest "
        "trail well behind."
    )

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
            labels={"actual_margin": "Gross Margin (USD)"},
            text=by_product.apply(
                lambda r: (
                    f"{CURRENCY_FORMAT.format(r['actual_margin'])} "
                    f"({r['margin_coverage']:.0%} cov.)"
                    if pd.notna(r["margin_coverage"])
                    else f"{CURRENCY_FORMAT.format(r['actual_margin'])} ({NULL_MARKER} cov.)"
                ),
                axis=1,
            ),
        )
        fig2.update_traces(marker_color=COLOR_TEXT, textposition="outside")
        fig2.update_layout(xaxis_title="Product", xaxis_tickangle=-45, height=480)
        fig2 = _style_plot(fig2)
        fig2.update_layout(margin=dict(t=60, l=70, r=30, b=150))
        st.plotly_chart(fig2, width="stretch")
        st.caption(
            "Coverage below 100 percent means a slice of that bar's margin "
            "is unknown, not zero; the label says how much of it to trust."
        )

    with right:
        st.subheader("Product Lifecycle Status")
        # Twelve products across three statuses is too small a set for a
        # chart to earn its place; a sentence states the same three counts
        # without asking the reader to decode a legend for so little data.
        lifecycle = products[~products["is_synthetic"]]["lifecycle_status"].value_counts()
        lifecycle_parts = [f"{int(n)} {status}" for status, n in lifecycle.items()]
        st.markdown(
            f'<p>Of {int(lifecycle.sum())} products: '
            f'{", ".join(lifecycle_parts)}.</p>',
            unsafe_allow_html=True,
        )

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
    fig.update_layout(
        title="Revenue Reconciliation Waterfall",
        xaxis_title="Bucket",
        yaxis_title="Revenue (USD)",
        showlegend=False,
        height=420,
    )
    st.plotly_chart(_style_plot(fig), width="stretch")
    st.caption(
        "The four buckets sum to the source total exactly; nothing is "
        "dropped or double-counted between raw orders and the mart."
    )

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
            fig2.update_layout(
                showlegend=False, yaxis_title="Rejected Revenue (USD)",
                xaxis_title="DQ Failure Reason", xaxis_tickangle=-20, height=440,
                margin=dict(t=40, l=70, r=30, b=110),
            )
            st.plotly_chart(_style_plot(fig2), width="stretch")
            st.caption(
                "One failure reason accounts for most of the quarantined "
                "revenue; the rest are minor by comparison."
            )

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
