"""Revenue performance dashboard.

Five-tab layout exposing the full star schema: executive summary, revenue
variance deep-dive, delivery performance (order-grain), product & margin
analysis, and data quality reconciliation. All tabs share one inline
filter row.

Reads through data_source.query(), which resolves to Snowpark, a live
Snowflake connector, or the committed DuckDB file; no code changes needed
per backend.

Colour system: one diverging axis for variance (red below plan, teal
above plan), one separate hue for data quality (violet, quarantine only),
and one warm amber reserved for states that are genuinely pending or
in-flight rather than judged good or bad, all darkened to clear 4.5:1 on
the warm off-white surface. A bar fill is never the text colour -- text is
for text -- so a truly excluded, no-judgement category (an order that was
Cancelled, and so is deliberately excluded from revenue) stays grey
instead. Structural chrome (tabs, filter chips, sliders, focus rings, the
title rule, the heading marker) carries the same red hue as the data, but
at a second, much lighter weight -- low saturation, a tint rather than a
fill -- so the house accent threads through the page without ever being
mistaken for a semantic reading. That split comes from
.streamlit/config.toml's theme tokens (primaryColor/linkColor set to the
accent weight) plus app/theme.css, which adds the layout pieces
Streamlit's theming API cannot reach (the lede sentence, the weighted
figure cards, the reconciliation callouts, the filter strip, the
multiselect chip softening, the title rule and heading marker).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from data_source import DataSourceError, backend_name, query

st.set_page_config(page_title="Revenue Performance", page_icon="📊", layout="wide")

def money(value: float) -> str:
    """Format a currency amount with the sign OUTSIDE the symbol.

    Python's "${:,.0f}" puts the minus between the symbol and the digits,
    giving "$-8,385,132". Currency is not written that way: the sign belongs
    in front of the whole amount. This matters here because most of the
    variance in this mart is negative, so the wrong form would appear on
    almost every figure in the app.
    """
    if value is None or pd.isna(value):
        return "n/a"
    return f"-${abs(value):,.0f}" if value < 0 else f"${value:,.0f}"


def money_compact(value: float) -> str:
    """A short currency form that fits inside a narrow bar.

    "$646,401 (100% cov.)" is too wide for a vertical bar, so the label gets
    pushed outside and has to be dark to stay legible on the panel. "$646k"
    fits, which lets the value sit on the bar in the light label colour. The
    exact figure and its coverage stay available on hover.
    """
    if value is None or pd.isna(value):
        return "n/a"
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1_000_000:
        return f"{sign}${v / 1_000_000:,.1f}M"
    if v >= 1_000:
        return f"{sign}${v / 1_000:,.0f}k"
    return f"{sign}${v:,.0f}"


def money_precise(value: float) -> str:
    """money() to the cent, for the reconciliation figures that must tie."""
    if value is None or pd.isna(value):
        return "n/a"
    return f"-${abs(value):,.2f}" if value < 0 else f"${value:,.2f}"
PCT_FORMAT = "{:.1%}"
NULL_MARKER = "unknown"

# Kept in one place, and only here, so every chart, card and table in this
# app agrees on what each hue means: red is the below-plan end of the
# variance axis, teal is the above-plan end, violet is data quality and
# never variance, amber is a state still in flight and not yet judged, and
# everything else stays neutral. Values match the oklch() colours in
# app/theme.css and .streamlit/config.toml; see the module docstring there
# for the conversion. On a light surface, one red clears 4.5:1 for both
# text and fills (oklch 0.52 0.20 27), so there is no need for the separate
# lighter "text" variant the dark theme required.
COLOR_SURFACE = "#FAF8F6"
COLOR_SURFACE_RAISED = "#F3EFED"
COLOR_BORDER = "#DAD7D3"
COLOR_TEXT = "#24211E"
COLOR_MUTED = "#6C6864"
COLOR_RED = "#C2181D"
COLOR_TEAL = "#007475"
COLOR_VIOLET = "#754D9E"
# Warm amber, the one hue added in this pass, reserved for a state that is
# genuinely pending -- in flight, not yet judged good or bad -- never for a
# structural exclusion like Cancelled. Sits in the same warm family as
# COLOR_RED rather than a cool or neon orange, and clears 4.5:1 as text
# weight against COLOR_SURFACE (measured 4.74:1).
COLOR_AMBER = "#B45309"

# One grey step for genuinely neutral fills -- a category that carries no
# judgement, is not variance, not data quality, not in flight, just
# structurally excluded (a cancelled order, out of revenue scope by
# design). Never the text colour: lighter than COLOR_TEXT/COLOR_MUTED, so a
# bar never reads as near-black.
COLOR_GREY_MID = "#8A8580"    # structurally excluded, no judgement

# Chart chrome, against the RAISED PANEL surface every chart now sits on
# directly (Defect 2: charts are transparent, not painted their own
# COLOR_SURFACE rectangle -- see _style_plot). COLOR_BORDER (#DAD7D3) is a
# 1.26:1 contrast against the panel and effectively disappears, so
# gridlines, zero-reference lines and axis lines reuse COLOR_GREY_MID
# instead, which measures 3.20:1 against #F3EFED -- clearly visible without
# competing with data. Tick and axis-title text stays COLOR_MUTED, which
# already clears 4.83:1 against the panel, so it needed no change.
COLOR_CHART_GRIDLINE = COLOR_GREY_MID

# Defect 3: the in-bar value-label colour for the rare case a label stays
# inside a bar (Plotly's own "auto" placement already prefers dark text
# outside the bar whenever the bar and margins have room for it -- see
# _style_plot). Never pure white or COLOR_SURFACE: a warm ivory that clears
# 4.5:1 against every saturated fill it can land on: 5.78:1 on COLOR_RED,
# 5.30:1 on COLOR_TEAL, 6.01:1 on COLOR_VIOLET, 4.76:1 on COLOR_AMBER,
# 5.23:1 on COLOR_MUTED (ratios computed with the WCAG relative-luminance
# formula against each fill's own hex).
COLOR_INSIDE_LABEL = "#FFF8ED"

FONT_SANS = "'IBM Plex Sans', -apple-system, 'Segoe UI', sans-serif"
FONT_MONO = "'IBM Plex Mono', ui-monospace, 'SFMono-Regular', Menlo, monospace"

# The one diverging colourscale used for the remaining continuous variance
# visual (the product-family variance bar). Zero-anchored: red at the low
# end, a neutral midpoint at zero, teal at the high end.
VARIANCE_COLORSCALE = [
    [0.0, COLOR_RED],
    [0.5, COLOR_SURFACE_RAISED],
    [1.0, COLOR_TEAL],
]

# A single-hue sequential ramp, teal only, for charts where one measure
# (here, revenue) varies across many categories with no below/above-plan
# reading to make -- magnitude should read from colour as well as bar
# length, not just length alone. Not a diverging scale: there is no
# meaningful zero or midpoint for a revenue total, so it runs light to
# full teal rather than red-to-teal.
REVENUE_SEQUENTIAL_COLORSCALE = [
    [0.0, COLOR_SURFACE_RAISED],
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

# Collapsing rule for the product x region heatmap: a cell spans up to six
# months, and this is the order in which those months' flags outrank one
# another when picked down to a single cell colour. AT_OR_ABOVE_PLAN has
# only 11 rows out of 289, so the statistical mode of six months almost
# never lands on it -- the good news is structurally invisible under a
# most-common-flag rule. Ranking it first instead means a cell that ever
# beat plan in any of its months shows as having beaten plan, full stop.
# NO_FORECAST is the next rarest and is a planning-side data-quality gap,
# so it outranks the routine BELOW_PLAN reading, which in turn outranks
# NO_ACTUALS, the dominant but least informative flag (an absence of
# orders, not a result).
CELL_PRIORITY = ["AT_OR_ABOVE_PLAN", "NO_FORECAST", "BELOW_PLAN", "NO_ACTUALS"]


def _collapse_cell_flag(month_flags: pd.Series) -> str:
    """Pick one flag to represent a heatmap cell's up-to-six months.

    Highest-priority flag present wins (see CELL_PRIORITY), not the most
    frequent one -- the whole point of Defect 1's fix is that a single
    month above plan must never be buried by five months with no orders.
    """
    present = set(month_flags)
    for flag in CELL_PRIORITY:
        if flag in present:
            return flag
    return month_flags.value_counts().idxmax()


def _style_plot(fig: go.Figure) -> go.Figure:
    """Apply the one neutral chrome style every chart in this app shares.

    Centralised so no chart accidentally keeps Plotly's default dark
    template, gridlines or font: DRY per the "chrome gets no hue" rule.
    Generous margins here are the baseline every chart gets; a few charts
    with long tick labels widen one side further below.

    Backgrounds are fully transparent (Defect 2), not painted COLOR_SURFACE:
    a chart sits directly on its panel's raised surface with no rectangle of
    its own, so gridlines/axis lines use COLOR_CHART_GRIDLINE, the shade
    that stays visible against that raised surface (see its definition
    above for the measured contrast ratios).
    """
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT_SANS, color=COLOR_TEXT, size=13),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=COLOR_TEXT)),
        margin=dict(t=40, l=60, r=30, b=60),
    )
    fig.update_xaxes(
        gridcolor=COLOR_CHART_GRIDLINE, zerolinecolor=COLOR_CHART_GRIDLINE,
        linecolor=COLOR_CHART_GRIDLINE,
        color=COLOR_MUTED, tickfont=dict(family=FONT_MONO, color=COLOR_MUTED),
        title_font=dict(family=FONT_SANS, color=COLOR_MUTED, size=13),
    )
    fig.update_yaxes(
        gridcolor=COLOR_CHART_GRIDLINE, zerolinecolor=COLOR_CHART_GRIDLINE,
        linecolor=COLOR_CHART_GRIDLINE,
        color=COLOR_MUTED, tickfont=dict(family=FONT_MONO, color=COLOR_MUTED),
        title_font=dict(family=FONT_SANS, color=COLOR_MUTED, size=13),
    )
    # Value labels sit outside the end of a bar, which puts them past the axis
    # range. Without this they are silently trimmed to fit the plot area, so a
    # figure reads as "05,857" when it is -8,205,857. Let them draw beyond the
    # axis and pad the plot so there is somewhere for them to go.
    # "auto" puts the value inside the bar when it fits and outside when it
    # does not, which means dark text outside is already the default for
    # every bar with room for it (Defect 3). Only a bar long enough to fill
    # the plot keeps its label inside, where it draws in COLOR_INSIDE_LABEL,
    # a warm ivory rather than white or COLOR_SURFACE, so it stays legible on
    # a saturated fill without reading as stark white-on-red.
    # Let labels place themselves ONLY where a chart has not already decided.
    # Blanket-setting "auto" here used to overwrite an explicit "outside", which
    # pushed labels inside tall bars; on a pale fill that put pale text on a
    # pale background. A chart that states a position keeps it.
    fig.update_traces(cliponaxis=False, selector=dict(type="bar"))
    for trace in fig.data:
        if trace.type != "bar":
            continue
        if trace.textposition is None:
            trace.textposition = "auto"
        # Inside text sits on the bar's own fill, so its colour follows that
        # fill rather than the page: pale fills take dark text, saturated
        # fills take the light one.
        fill = trace.marker.color if isinstance(trace.marker.color, str) else None
        pale = fill is None or fill in (COLOR_SURFACE, COLOR_SURFACE_RAISED)
        trace.insidetextfont = dict(
            family=FONT_MONO, color=COLOR_TEXT if pale else COLOR_INSIDE_LABEL
        )
        trace.outsidetextfont = dict(family=FONT_MONO, color=COLOR_TEXT)
    fig.update_xaxes(automargin=True)
    fig.update_yaxes(automargin=True)
    return fig


def inject_theme():
    css_path = Path(__file__).parent / "theme.css"
    if css_path.exists():
        st.markdown(f"<style>{css_path.read_text()}</style>", unsafe_allow_html=True)


def panel(key: str):
    """One panel: a region of the composed page, not a floating card.

    Filled background, hairline border, radius capped at 4px, no shadow;
    tight (12px) gutters between panels and snug (16px) internal padding
    all come from the one shared `[class*="st-key-panel-"]` rule in
    app/theme.css (`key` becomes the `st-key-panel-<key>` class Streamlit
    attaches to the container), so every call site stays visually
    identical without repeating any CSS. See DESIGN.md's Layout rules for
    the full definition of a panel.
    """
    return st.container(key=f"panel-{key}")


def section_rule(weight: str = "section"):
    """One hairline rule, replacing Streamlit's default `st.markdown('---')`.

    `weight` picks the spacing either side of the rule from the 8px scale:
    "section" (32px) between distinct regions of a tab, "tight" (24px)
    between two closely related blocks that still deserve a visible split.
    Never used between a figure and its own sub-line -- that gap stays at
    8px and carries no rule at all.
    """
    css_class = "band-rule" if weight == "section" else "band-rule--tight"
    st.markdown(f'<hr class="{css_class}" />', unsafe_allow_html=True)


RUN_RESULTS_PATH = Path(__file__).parent.parent / "transform" / "target" / "run_results.json"


def _load_dbt_run_results() -> dict | None:
    """The dbt build's own run_results.json, if one exists.

    A fresh clone has never run `make build`, so the file may simply be
    absent -- that is not an error, it is the expected state before a first
    build, and the caller shows a short message instead of a traceback.
    """
    if not RUN_RESULTS_PATH.exists():
        return None
    try:
        return json.loads(RUN_RESULTS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None


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


def render_filter_row(data: dict[str, pd.DataFrame]):
    """The three filters, inline above the tabs rather than in a sidebar.

    A plain horizontal row, not an expander or a modal: the filters apply to
    every tab exactly as before, they are just no longer hidden in a drawer.
    No hue here -- a filter chip is not a semantic state -- so this strip
    earns its presence from being one panel (see `panel()`), a clear
    boundary, heavier label type, and an explicit count of how many values
    are active, the same way `.st-key-panel-filter-strip` is styled in
    app/theme.css. The source and backend used to sit in a fourth column
    here; it now lives in the instrument header above, so this row is only
    the three filters.
    """
    with panel("filter-strip"):
        col_region, col_family, col_months = st.columns([1, 1, 1])

        with col_region:
            all_regions = sorted(data["mart"]["region"].unique())
            regions = st.multiselect("Region", all_regions, default=all_regions)
            st.caption(f"{len(regions)} of {len(all_regions)} active")

        with col_family:
            all_families = sorted(data["products"]["product_family"].unique())
            families = st.multiselect("Product Family", all_families, default=all_families)
            st.caption(f"{len(families)} of {len(all_families)} active")

        with col_months:
            all_months = sorted(pd.to_datetime(data["mart"]["revenue_month"]).unique())
            if len(all_months) >= 2:
                month_range = st.select_slider(
                    "Month Range",
                    options=all_months,
                    value=(all_months[0], all_months[-1]),
                    format_func=lambda x: pd.Timestamp(x).strftime("%b %Y"),
                )
                n_months_active = sum(
                    1 for m in all_months if month_range[0] <= m <= month_range[1]
                )
                st.caption(f"{n_months_active} of {len(all_months)} months active")
            else:
                month_range = (all_months[0], all_months[0]) if all_months else None

    return regions, families, month_range


def figure_card(label: str, value_html: str, sub_html: str = "", size: str = "primary"):
    """Render one weighted figure, sitting directly on the surface.

    No border, no fill, no radius: this used to be a bordered "card" and is
    now a plain block, grouped with its neighbours by the hairline rule
    section_rule() draws above the row, not by a box around each one.
    `size` controls type scale, not colour.
    """
    st.markdown(
        f'<div class="figure-block">'
        f'<div class="figure-label">{label}</div>'
        f'<div class="figure-value figure-value--{size}">{value_html}</div>'
        f'<div class="figure-sub">{sub_html}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _reconciliation_totals(raw: dict[str, pd.DataFrame]) -> dict[str, float]:
    """The four buckets every source dollar lands in, computed once.

    Always over the whole, unfiltered dataset -- a partially-filtered
    reconciliation would not prove anything, so every caller (the
    instrument header, the executive summary, the data-quality tab) passes
    the same unfiltered `data`, never `filtered`. Centralised here so the
    three call sites can never disagree about how the total is built.
    """
    mart_raw = raw["mart"]
    orders_raw = raw["orders"]
    dq_raw = raw["dq"]
    recognized = mart_raw["actual_revenue"].sum()
    open_rev = orders_raw[orders_raw["order_status"] == "Open"]["gross_revenue"].sum()
    cancelled_rev = orders_raw[orders_raw["order_status"] == "Cancelled"]["gross_revenue"].sum()
    rejected_rev = dq_raw["rejected_revenue"].sum()
    source_total = recognized + open_rev + cancelled_rev + rejected_rev
    return {
        "recognized": recognized,
        "open": open_rev,
        "cancelled": cancelled_rev,
        "quarantined": rejected_rev,
        "source_total": source_total,
    }


def _load_freshness():
    """Max load timestamp across the raw layer, or None if unavailable.

    Shared by the instrument header and the observability tab so both read
    the same freshness fact; `query()` already caches the underlying call,
    so asking twice costs nothing extra.
    """
    try:
        freshness = query(
            "select max(_loaded_at) as last_loaded_at, "
            "arg_max(_batch_id, _loaded_at) as latest_batch_id, "
            "count(distinct _batch_id) as batch_count from ("
            "select _loaded_at, _batch_id from raw.raw_oracle_orders "
            "union all "
            "select _loaded_at, _batch_id from raw.raw_erp_products "
            "union all "
            "select _loaded_at, _batch_id from raw.raw_adaptive_forecast "
            "union all "
            "select _loaded_at, _batch_id from raw.raw_salesforce_accounts"
            ")"
        )
    except DataSourceError:
        return None
    if freshness.empty or pd.isna(freshness.loc[0, "last_loaded_at"]):
        return None
    return freshness.iloc[0]


def render_balance_bar(totals: dict[str, float]):
    """The signature element: one thin, proportional bar, always visible.

    Four segments -- recognised, open, cancelled, quarantined -- sized to
    their share of source revenue, in the existing semantic colours
    (recognised teal, open/cancelled neutral greys, quarantined violet).
    Exact values sit in each segment's `title` attribute, a native
    tooltip, so the bar itself stays a thin instrument rather than a hero
    graphic; the compact label states the source total once, in words.
    """
    source_total = totals["source_total"]
    segments = [
        ("recognized", "Recognized (shipped)", totals["recognized"]),
        ("open", "Open pipeline", totals["open"]),
        ("cancelled", "Cancelled", totals["cancelled"]),
        ("quarantined", "Quarantined (DQ)", totals["quarantined"]),
    ]
    bar_html = ['<div class="balance-bar">']
    for css_key, label, value in segments:
        pct = (value / source_total * 100) if source_total else 0
        tooltip = f"{label}: {money_precise(value)} ({pct:.1f}%)"
        bar_html.append(
            f'<div class="balance-seg balance-seg--{css_key}" '
            f'style="width:{pct:.4f}%" title="{tooltip}"></div>'
        )
    bar_html.append("</div>")
    st.markdown("".join(bar_html), unsafe_allow_html=True)
    st.markdown(
        f'<div class="balance-bar-label">Recognized + open + cancelled + '
        f'quarantined = source total '
        f'<span class="num">{money_precise(source_total)}</span></div>',
        unsafe_allow_html=True,
    )


def render_instrument_header(data: dict[str, pd.DataFrame]):
    """The persistent instrument band: name, source, freshness, run status.

    Stays above the tabs on every tab (main() draws it once, before
    st.tabs), anchoring the page the way the old floating title never did.
    Distinct from the content below through the raised surface and a
    rule, per PRODUCT.md's "chrome carries the accent at low saturation" --
    not a card.
    """
    freshness_row = _load_freshness()
    if freshness_row is not None:
        last_loaded = pd.Timestamp(freshness_row["last_loaded_at"]).strftime("%Y-%m-%d %H:%M")
        freshness_html = f'<span class="num">{last_loaded}</span>'
    else:
        freshness_html = NULL_MARKER

    run_results = _load_dbt_run_results()
    if run_results is not None:
        test_results = [
            r for r in run_results.get("results", []) if r["unique_id"].startswith("test.")
        ]
        tests_passed = sum(1 for r in test_results if r["status"] == "pass")
        run_html = f'<span class="num">{tests_passed}/{len(test_results)}</span> dbt tests passing'
    else:
        run_html = "dbt build not yet run"

    with st.container(key="instrument-header"):
        st.markdown(
            f'<span class="instrument-title">Revenue Performance</span>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="instrument-meta">'
            f'{backend_name()}'
            f'<span class="sep">&middot;</span>'
            f'Last loaded {freshness_html}'
            f'<span class="sep">&middot;</span>'
            f'{run_html}'
            f'</div>',
            unsafe_allow_html=True,
        )
        totals = _reconciliation_totals(data)
        render_balance_bar(totals)


def tab_executive_summary(filtered: dict[str, pd.DataFrame], raw: dict[str, pd.DataFrame]):
    mart = filtered["mart"]

    # --- The lede: the actual story, in words, computed from the whole
    # dataset so it never contradicts the reconciliation facts below no
    # matter how the filter row is set. -84% variance alone reads as a revenue
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
        with panel("fig-actual-vs-plan"):
            figure_card(
                "Actual vs Plan",
                f'<span class="{state_class}">{glyph}</span> {money(actual)}',
                (
                    f'against {money(forecast)} plan &middot; '
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
        with panel("fig-on-time-delivery"):
            figure_card(
                "On-Time Delivery",
                PCT_FORMAT.format(on_time_rate) if on_time_rate is not None else NULL_MARKER,
                f'{int(on_time):,} of {int(delivered):,} shipped orders, order-grain',
                size="secondary",
            )

    # Reconciliation always covers the whole source dataset, ignoring the
    # filter row; a partially-filtered reconciliation would not prove
    # anything, the same rule tab_data_quality already follows.
    totals = _reconciliation_totals(raw)
    source_total = totals["source_total"]
    rejected_rev = totals["quarantined"]
    rejected_share = rejected_rev / source_total if source_total else None

    with col_recon:
        with panel("fig-reconciliation"):
            figure_card(
                "Reconciliation",
                money(source_total),
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

    with panel("callouts-executive-summary"):
        st.markdown('<div class="callout-row">', unsafe_allow_html=True)
        st.markdown(
            f'<div class="callout">Reconciliation balances to the cent: recognized, '
            f'open, cancelled and quarantined revenue sum to exactly '
            f'<span class="num">{money_precise(source_total)}</span>.</div>',
            unsafe_allow_html=True,
        )
        if not unmapped.empty:
            st.markdown(
                f'<div class="callout">One row, region UNMAPPED, carries '
                f'<span class="num">{money(unmapped_revenue)}</span> of '
                f'revenue that no forecast explains.</div>',
                unsafe_allow_html=True,
            )
        st.markdown('</div>', unsafe_allow_html=True)

    # --- The primary graphic: zero-anchored variance by region, red to
    # the left, teal to the right. This replaces the eight-tile hero as the
    # dominant element on this tab.
    with panel("chart-variance-by-region"):
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
                    text=[money(v) for v in by_region["revenue_variance"]],
                    textposition="auto",
                )
            )
            fig.update_layout(
                height=min(90 + 60 * len(by_region), 480),
                xaxis_title="Revenue Variance (USD, zero-anchored)",
                yaxis_title="Region",
                showlegend=False,
            )
            fig.add_vline(x=0, line_color=COLOR_CHART_GRIDLINE, line_width=1)
            fig = _style_plot(fig)
            fig.update_layout(margin=dict(t=40, l=90, r=40, b=60))
            st.plotly_chart(fig, use_container_width=True)
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
        "orders at all (NO_ACTUALS), which is common but not the most notable "
        "thing that can happen in a cell. A cell spans up to six months per "
        "product x region and shows the single most notable outcome any month "
        "in it achieved: beating plan outranks a forecasting gap, which "
        "outranks missing plan, which outranks no orders at all, so one good "
        "month is never buried by five quiet ones. Hover a cell for its "
        "month-by-month composition."
    )

    flag_pivot = (
        mart.groupby(["product_id", "region"])["variance_flag"]
        .agg(_collapse_cell_flag)
        .unstack("region")
        .sort_index()
    )
    flag_pivot = flag_pivot[sorted(flag_pivot.columns)]

    # Defect 1: the legend must count what the grid actually draws -- one
    # collapsed flag per product x region CELL -- not the underlying
    # product-region-MONTH rows `flag_counts` holds. `.stack()` drops the
    # product x region pairs with no mart row at all (a cell that is blank,
    # not coloured), so this is exactly the population of cells the heatmap
    # renders. Two numbers, two grains, computed fresh every render so they
    # can never drift out of sync with each other or with the data.
    cell_flags = flag_pivot.stack()
    cell_flag_counts = cell_flags.value_counts()
    n_cells_with_data = int(len(cell_flags))
    above_plan_cells = int(cell_flag_counts.get("AT_OR_ABOVE_PLAN", 0))
    above_plan_rows = int(flag_counts.get("AT_OR_ABOVE_PLAN", 0))

    with panel("chart-variance-status-heatmap"):
        if not flag_pivot.empty:
            # Month-by-month composition per cell, for the hover text: e.g.
            # "Jan 2024: BELOW_PLAN; Mar 2024: AT_OR_ABOVE_PLAN". Built once
            # here from the same grouping the pivot uses, so hover and cell
            # colour can never disagree about what a cell contains.
            def _composition(rows: pd.DataFrame) -> str:
                ordered = rows.sort_values("revenue_month")
                months = pd.to_datetime(ordered["revenue_month"]).dt.strftime("%b %Y")
                return "; ".join(f"{m}: {f}" for m, f in zip(months, ordered["variance_flag"]))

            composition_pivot = (
                mart.groupby(["product_id", "region"])[["revenue_month", "variance_flag"]]
                .apply(_composition)
                .unstack("region")
                .reindex(index=flag_pivot.index, columns=flag_pivot.columns)
                .fillna("")
            )

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
                    customdata=composition_pivot.values,
                    hovertemplate=(
                        "Product %{y}<br>Region %{x}<br>Shown: %{text}"
                        "<br>Months: %{customdata}<extra></extra>"
                    ),
                    colorscale=discrete_colorscale,
                    zmin=0,
                    zmax=n_flags,
                    showscale=False,
                    xgap=2,
                    ygap=2,
                )
            )
            # A real legend, not a colour-bar: one invisible marker trace per
            # category, labelled with the CELL count -- how many
            # product-region boxes the grid actually draws in that colour --
            # not the row count. Naming the unit explicitly ("cells") keeps
            # the grain unambiguous next to the month-level caption below.
            for flag in FLAG_ORDER:
                count = int(cell_flag_counts.get(flag, 0))
                fig.add_trace(
                    go.Scatter(
                        x=[None], y=[None], mode="markers",
                        marker=dict(size=12, color=FLAG_COLORS[flag], symbol="square"),
                        name=f"{flag} ({count} cells)",
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
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                f"{above_plan_cells} of {n_cells_with_data} product-region cells "
                "contain at least one month at or above plan; "
                f"{above_plan_rows} of the {total_rows} product-region-months "
                "were at or above plan. A cell can hold up to six months, so "
                "more months than cells clear plan whenever a cell's good month "
                "shares a cell with others that did not."
            )

    section_rule()
    with panel("chart-actual-vs-forecast-by-month"):
        st.subheader("Actual vs Forecast by Month")
        st.caption(
            "Actual is the series worth reading: teal, solid, in the "
            "foreground. Forecast is drawn as a neutral outline behind it, not "
            "a second solid colour competing for attention. The variance the "
            "two imply is shown separately, above and below."
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
            name="Actual", marker_color=COLOR_TEAL,
            text=[money(v) for v in monthly["actual_revenue"]],
            textposition="outside",
        ))
        fig_monthly.add_trace(go.Bar(
            x=monthly["revenue_month"], y=monthly["forecast_revenue"],
            name="Forecast", marker_color=COLOR_SURFACE,
            marker_line=dict(color=COLOR_MUTED, width=1.5),
            marker_pattern_shape="/",
            marker_pattern_fgcolor=COLOR_MUTED,
            text=[money(v) for v in monthly["forecast_revenue"]],
            textposition="outside",
        ))
        fig_monthly.update_layout(
            barmode="group", yaxis_title="Revenue (USD)", xaxis_title="Month",
            height=460, legend_title_text="Series",
        )
        st.plotly_chart(_style_plot(fig_monthly), use_container_width=True)
        st.caption(
            "Forecast dwarfs actual in every month; the gap is the same "
            "structural shortfall the lede describes, not a swing month to month."
        )

    section_rule()
    left, right = st.columns(2)

    with left:
        with panel("table-variance-by-region"):
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
                use_container_width=True,
                hide_index=True,
            )

    with right:
        with panel("chart-variance-flag-distribution"):
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
                    textposition="auto",
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
            st.plotly_chart(fig2, use_container_width=True)
            st.caption(
                "NO_ACTUALS dwarfs every other flag: most of the grid never had "
                "an order to compare against plan."
            )

    section_rule()
    with panel("chart-variance-by-product-family"):
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
            text=by_family["revenue_variance"].apply(money),
        )
        fig3.update_traces(textposition="outside")
        fig3.update_layout(
            yaxis_title="Revenue Variance (USD)", xaxis_title="Product Family",
            height=460, coloraxis_colorbar_title="Variance (USD)",
            margin=dict(t=40, l=60, r=30, b=80),
        )
        st.plotly_chart(_style_plot(fig3), use_container_width=True)
        st.caption(
            "Every product family sits below plan; colour and bar direction "
            "agree, so the reader never has to reconcile the two."
        )

    section_rule()
    left2, right2 = st.columns(2)
    with left2:
        with panel("table-top5-above-plan"):
            st.markdown("**Top 5 Above Plan**")
            top = mart.nlargest(5, "revenue_variance")[
                ["product_id", "region", "revenue_month", "revenue_variance"]
            ]
            st.dataframe(
                top.style.format({"revenue_variance": "${:,.0f}"}, na_rep=NULL_MARKER),
            )
    with right2:
        with panel("table-top5-below-plan"):
            st.markdown("**Top 5 Below Plan**")
            bottom = mart.nsmallest(5, "revenue_variance")[
                ["product_id", "region", "revenue_month", "revenue_variance"]
            ]
            st.dataframe(
                bottom.style.format({"revenue_variance": "${:,.0f}"}, na_rep=NULL_MARKER),
            )

    section_rule()
    with panel("table-detail"):
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
            use_container_width=True,
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

    # Delivery status carries real meaning, not a neutral operational label:
    # On Time/Late sit on the same good/bad axis as revenue variance, Not
    # Delivered is genuinely pending -- in flight, not yet judged -- so it
    # takes amber rather than a grey that would flatten it into Cancelled.
    # Cancelled is a structural exclusion from revenue by design, so it
    # keeps the one neutral grey. Unknown exists only because of a data
    # defect (D8: one row is delivered before it was ordered), so it takes
    # the data-quality hue.
    status_shades = {
        "On Time": COLOR_TEAL,
        "Late": COLOR_RED,
        "Not Delivered": COLOR_AMBER,
        "Cancelled": COLOR_GREY_MID,
        "Unknown": COLOR_VIOLET,
    }

    left, right = st.columns(2)

    with left:
        with panel("chart-delivery-status-distribution"):
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
                    textposition="auto",
                )
            )
            fig.update_layout(
                height=320,
                xaxis_title="Orders (count)",
                yaxis_title="Delivery Status",
                showlegend=False,
            )
            fig = _style_plot(fig)
            fig.update_layout(margin=dict(t=60, l=120, r=50, b=60))
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                "Late is the largest single status, ahead of On Time; only a "
                "third of shipped orders arrived on schedule. Not Delivered is "
                "amber because it is still in flight, not yet a judged outcome; "
                "Cancelled is grey because it is excluded from revenue by "
                "design; Unknown is a single data-defect row explained below. "
                "None of the three are scored against the plan."
            )

    with right:
        with panel("chart-on-time-rate-by-region"):
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
                fig2.update_traces(marker_color=COLOR_TEAL, textposition="outside")
                fig2.update_layout(
                    yaxis_title="On-Time Rate (% of On Time + Late)",
                    yaxis_tickformat=".0%",
                    xaxis_title="Region", height=420,
                )
                st.plotly_chart(_style_plot(fig2), use_container_width=True)
                st.caption(
                    "Every region falls short of an even on-time split; ranking "
                    "them shows which is furthest behind. Denominator is On Time "
                    "plus Late only, the same convention the mart uses."
                )

    section_rule()
    with panel("chart-delivery-gap-days"):
        st.subheader("Promised vs Actual: Delivery Gap in Days")
        st.caption(
            "A status count says an order was late; it does not say by how much. "
            "The gap below, in days, is derived the same way for every On Time or "
            "Late order: actual delivery date minus promised delivery date. "
            "Positive means the order arrived after it was promised."
        )

        dated = shipped[shipped["delivery_status"].isin(["On Time", "Late"])].copy()
        if not dated.empty:
            dated["actual_delivery_date"] = pd.to_datetime(dated["actual_delivery_date"])
            dated["promised_delivery_date"] = pd.to_datetime(dated["promised_delivery_date"])
            dated["delivery_gap_days"] = (
                dated["actual_delivery_date"] - dated["promised_delivery_date"]
            ).dt.days

            gap_left, gap_right = st.columns(2)
            with gap_left:
                avg_gap = dated["delivery_gap_days"].mean()
                worst_gap = dated["delivery_gap_days"].max()
                st.metric("Average Gap, On Time + Late Orders", f"{avg_gap:+.1f} days")
                st.metric("Worst Single Gap", f"{int(worst_gap):+d} days")

            with gap_right:
                by_region_gap = (
                    dated.groupby("region", as_index=False)["delivery_gap_days"]
                    .mean()
                    .sort_values("delivery_gap_days")
                )
                gap_colors = [
                    COLOR_RED if v > 0 else COLOR_TEAL
                    for v in by_region_gap["delivery_gap_days"]
                ]
                fig3 = go.Figure(
                    go.Bar(
                        x=by_region_gap["delivery_gap_days"],
                        y=by_region_gap["region"],
                        orientation="h",
                        marker_color=gap_colors,
                        text=[f"{v:+.1f}" for v in by_region_gap["delivery_gap_days"]],
                        textposition="auto",
                    )
                )
                fig3.add_vline(x=0, line_color=COLOR_CHART_GRIDLINE, line_width=1)
                fig3.update_layout(
                    height=280,
                    xaxis_title="Average Gap (days, zero-anchored)",
                    yaxis_title="Region",
                    showlegend=False,
                )
                fig3 = _style_plot(fig3)
                fig3.update_layout(margin=dict(t=60, l=90, r=40, b=60))
                st.plotly_chart(fig3, use_container_width=True)
            st.caption(
                "Red bars run late on average, teal bars run on time or early on "
                "average; the axis is the same shortfall-versus-plan convention "
                "used for revenue, applied here to a promise instead of a plan."
            )

    section_rule()
    st.subheader("The Rows a Status Count Alone Would Hide")
    n_cancelled = int((orders["delivery_status"] == "Cancelled").sum())
    n_unknown = int((orders["delivery_status"] == "Unknown").sum())
    with panel("callouts-delivery"):
        st.markdown('<div class="callout-row">', unsafe_allow_html=True)
        st.markdown(
            f'<div class="callout">'
            f'<span class="num">{n_cancelled}</span> orders are Cancelled. Every '
            f'one of them carries a delivery date in the source system, which '
            f'contradicts a cancelled order; order status wins and the date is '
            f'ignored, so these rows are excluded from on-time scoring rather '
            f'than counted as a delivery outcome.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="callout">'
            f'<span class="num">{n_unknown}</span> order is Unknown: its actual '
            f'delivery date falls before its order date, which is not physically '
            f'possible. The dates are untrustworthy, so the delivery status '
            f'degrades to Unknown rather than being guessed at; the revenue is '
            f'not in question and stays recognised.</div>',
            unsafe_allow_html=True,
        )
        st.markdown('</div>', unsafe_allow_html=True)

    section_rule()
    with panel("table-late-orders-detail"):
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
            ]].rename(columns={
                "order_id": "Order",
                "customer_id": "Customer",
                "product_id": "Product",
                "region": "Region",
                "order_date": "Order Date",
                "promised_delivery_date": "Promised",
                "actual_delivery_date": "Actual",
                "days_late": "Days Late",
                "gross_revenue": "Gross Revenue",
            }).sort_values("Days Late", ascending=False)

            st.dataframe(
                late_display.style.format({"Gross Revenue": "${:,.2f}"}, na_rep=NULL_MARKER),
                use_container_width=True,
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
    uncovered_orders = orders[~orders["has_standard_cost"]]

    with panel("fig-margin-summary"):
        m1, m2, m3 = st.columns(3)
        m1.metric("Orders", f"{int(total_orders):,}")
        m2.metric("Gross Margin", money(total_margin))
        m3.metric(
            "Margin Coverage",
            PCT_FORMAT.format(margin_coverage) if margin_coverage is not None else NULL_MARKER,
        )

    # Margin coverage is the interesting column here, not a footnote: a
    # complete margin figure and a partial one are different claims, so the
    # gap gets a callout naming exactly which order(s) it is, the same way
    # the executive summary names the UNMAPPED row rather than hiding it in
    # a caption.
    with panel("callouts-margin"):
        st.markdown('<div class="callout-row">', unsafe_allow_html=True)
        if not uncovered_orders.empty:
            order_list = ", ".join(uncovered_orders["order_id"].astype(str))
            product_list = ", ".join(sorted(uncovered_orders["product_id"].unique()))
            st.markdown(
                f'<div class="callout">'
                f'<span class="state-violet">&#9670;</span> '
                f'<span class="num">{len(uncovered_orders)}</span> of '
                f'<span class="num">{total_orders}</span> orders '
                f'(<span class="num">{order_list}</span>, product '
                f'<span class="num">{product_list}</span>) has no standard cost. '
                f'Its margin is unknown, not zero, so the gross margin total '
                f'above is a complete figure for '
                f'<span class="num">{PCT_FORMAT.format(margin_coverage)}</span> '
                f'of orders and silent on the rest.</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="callout">Every order has a standard cost on file; '
                'the margin total is complete.</div>',
                unsafe_allow_html=True,
            )
        st.markdown('</div>', unsafe_allow_html=True)

    section_rule()
    with panel("chart-revenue-by-product-family"):
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
                marker=dict(
                    color=by_family_sorted["actual_revenue"],
                    colorscale=REVENUE_SEQUENTIAL_COLORSCALE,
                    showscale=False,
                ),
                text=[money(v) for v in by_family_sorted["actual_revenue"]],
                textposition="auto",
            )
        )
        fig.update_layout(
            height=min(90 + 50 * len(by_family_sorted), 400),
            xaxis_title="Actual Revenue (USD)",
            yaxis_title="Product Family",
            showlegend=False,
        )
        fig = _style_plot(fig)
        fig.update_layout(margin=dict(t=60, l=140, r=60, b=60))
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Revenue concentrates in the top one or two families; the rest "
            "trail well behind. Fill deepens with revenue, on the same one-hue "
            "teal ramp as bar length, so magnitude reads twice."
        )

    section_rule()

    # Full width, stacked. Side by side, the chart had to rotate twelve product
    # names to 45 degrees to fit and the table lost its last column off the
    # right edge. Neither reads at half width, and nothing here needs to be
    # compared across the two.
    with panel("chart-margin-by-product"):
        st.subheader("Margin by Product")
        st.caption(
            "Teal marks a product whose margin is built from a fully known "
            "cost; violet marks one where at least one order had no "
            "standard cost, so part of that bar's margin is unknown rather "
            "than zero. The bar states the margin; hover it for the exact figure "
            "and how much of it rests on a known cost."
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

        bar_colors = [
            COLOR_TEAL if pd.notna(c) and c >= 1 else COLOR_VIOLET
            for c in by_product["margin_coverage"]
        ]

        fig2 = go.Figure(
            go.Bar(
                x=by_product["product_name"],
                y=by_product["actual_margin"],
                marker_color=bar_colors,
                text=by_product["actual_margin"].apply(money_compact),
                customdata=by_product.apply(
                    lambda r: (
                        f"{money(r['actual_margin'])} "
                        f"({r['margin_coverage']:.0%} of orders have a known cost)"
                        if pd.notna(r["margin_coverage"])
                        else f"{money(r['actual_margin'])} (coverage {NULL_MARKER})"
                    ),
                    axis=1,
                ),
                hovertemplate="%{x}<br>%{customdata}<extra></extra>",
                textposition="inside",
                insidetextanchor="middle",
            )
        )
        fig2.update_layout(
            yaxis_title="Gross Margin (USD)",
            xaxis_title="Product", xaxis_tickangle=-45, height=480,
            showlegend=False,
        )
        fig2 = _style_plot(fig2)
        fig2.update_layout(margin=dict(t=60, l=70, r=30, b=150))
        st.plotly_chart(fig2, use_container_width=True)
        st.caption(
            "Coverage below 100 percent means a slice of that bar's margin "
            "is unknown, not zero; hover a bar to see how much of it to trust."
        )

    section_rule()

    with panel("table-product-lifecycle"):
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
            use_container_width=True,
            hide_index=True,
        )


def tab_data_quality(data: dict[str, pd.DataFrame]):
    dq = data["dq"]
    rejects = data["rejects_detail"]
    mart = data["mart"]

    st.subheader("Reconciliation Proof")
    st.caption(
        "Every source dollar lands in exactly one bucket. If this stops balancing, dbt build fails. "
        "This reconciliation always covers the full dataset; it ignores the filter row, since "
        "a partial reconciliation would not prove anything."
    )

    totals = _reconciliation_totals(data)
    recognized = totals["recognized"]
    open_rev = totals["open"]
    cancelled_rev = totals["cancelled"]
    rejected_rev = totals["quarantined"]
    source_total = totals["source_total"]
    rejected_share = rejected_rev / source_total if source_total else None

    with panel("fig-reconciliation-summary"):
        rc1, rc2, rc3, rc4 = st.columns(4)
        rc1.metric("Recognized (Shipped)", money(recognized))
        rc2.metric("Open Pipeline", money(open_rev))
        rc3.metric("Cancelled", money(cancelled_rev))
        rc4.metric("Rejected (DQ)", money(rejected_rev))
        st.markdown(
            f'<p class="section-caption"><span class="state-violet">&#9670;</span> '
            f'<span class="state-violet num">'
            f'{PCT_FORMAT.format(rejected_share) if rejected_share is not None else NULL_MARKER}'
            f'</span> share of source revenue, a data-quality cost, not a shortfall '
            f'against plan, which is why it is violet rather than the variance colours.</p>',
            unsafe_allow_html=True,
        )

        st.metric("Source Total (sum of above)", money_precise(source_total))

    # One bar per bucket, coloured by what the bucket means rather than by
    # whether the waterfall step reads as an increase: recognised revenue is
    # the good outcome (teal), open pipeline is genuinely in flight and not
    # yet judged (amber), cancelled orders are structurally excluded from
    # revenue by design (grey, no judgement), and rejected is the
    # data-quality bucket (violet). The source total is stated as the
    # metric above rather than repeated as a fifth bar, since a total is not
    # itself a bucket.
    with panel("chart-reconciliation-buckets"):
        recon_labels = ["Recognized", "Open Pipeline", "Cancelled", "Rejected (DQ)"]
        recon_values = [recognized, open_rev, cancelled_rev, rejected_rev]
        recon_colors = [COLOR_TEAL, COLOR_AMBER, COLOR_GREY_MID, COLOR_VIOLET]
        fig = go.Figure(
            go.Bar(
                x=recon_labels,
                y=recon_values,
                marker_color=recon_colors,
                text=[money(v) for v in recon_values],
                textposition="outside",
            )
        )
        fig.update_layout(
            xaxis_title="Bucket",
            yaxis_title="Revenue (USD)",
            showlegend=False,
            height=420,
        )
        st.plotly_chart(_style_plot(fig), use_container_width=True)
        st.caption(
            f"The four buckets sum to the source total of "
            f"{money_precise(source_total)} exactly; nothing "
            "is dropped or double-counted between raw orders and the mart."
        )

    section_rule()
    left, right = st.columns(2)

    with left:
        with panel("table-reject-summary"):
            st.subheader("Reject Summary")
            st.caption(
                "Six rows across four failure reasons, quarantined before the "
                "mart is built rather than silently dropped or coerced."
            )
            dq_display = dq.rename(columns={
                "dq_failure_reason": "Failure Reason",
                "rejected_row_count": "Rows Rejected",
                "rejected_revenue": "Rejected Revenue",
                "pct_of_source_rows": "% of Source Rows",
            })
            st.dataframe(
                dq_display.style.format(
                    {"Rejected Revenue": "${:,.2f}", "% of Source Rows": "{:.2%}"},
                    na_rep=NULL_MARKER,
                ),
                use_container_width=True,
                hide_index=True,
            )

    with right:
        with panel("chart-rejected-revenue-by-reason"):
            st.subheader("Rejected Revenue by Reason")
            st.caption(
                "Violet throughout: this chart is entirely data-quality scope. "
                "One reason has no revenue figure because the row is missing "
                "the unit price needed to compute one."
            )
            dq_valid = dq[dq["rejected_revenue"].notna()]
            if not dq_valid.empty:
                fig2 = px.bar(
                    dq_valid,
                    x="dq_failure_reason", y="rejected_revenue",
                    labels={
                        "dq_failure_reason": "DQ Failure Reason",
                        "rejected_revenue": "Rejected Revenue (USD)",
                    },
                    text=dq_valid["rejected_revenue"].apply(lambda x: f"${x:,.0f}"),
                )
                fig2.update_traces(marker_color=COLOR_VIOLET, textposition="outside")
                fig2.update_layout(
                    showlegend=False,
                    xaxis_tickangle=-20, height=440,
                    margin=dict(t=60, l=70, r=30, b=110),
                )
                st.plotly_chart(_style_plot(fig2), use_container_width=True)
                st.caption(
                    "One failure reason accounts for most of the quarantined "
                    "revenue; the rest are minor by comparison."
                )

    section_rule()
    with panel("table-rejected-rows-detail"):
        st.subheader("Rejected Rows Detail")
        st.caption(
            f"Full payload of every one of the {len(rejects)} rejected rows, "
            "with the reason each was excluded."
        )
        rejects_display = rejects.rename(columns={
            "order_id": "Order",
            "customer_id": "Customer",
            "product_id": "Product",
            "quantity": "Quantity",
            "unit_price": "Unit Price",
            "gross_revenue": "Gross Revenue",
            "order_status": "Order Status",
            "dq_failure_reason": "Failure Reason",
        })
        st.dataframe(
            rejects_display.style.format(
                {"Gross Revenue": "${:,.2f}", "Unit Price": "${:,.2f}"},
                na_rep=NULL_MARKER,
            ),
            use_container_width=True,
            hide_index=True,
        )

    section_rule()
    st.subheader("Pipeline Observability")
    st.caption(
        "When the data was last loaded, when the transformation pipeline "
        "last ran, and whether its tests passed, read from the same "
        "artifacts the pipeline already writes, not a separate monitoring "
        "system."
    )

    obs_left, obs_right = st.columns(2)

    with obs_left:
        with panel("fig-data-freshness"):
            st.markdown("**Data Freshness (raw layer)**")
            try:
                freshness = query(
                    "select max(_loaded_at) as last_loaded_at, "
                    "arg_max(_batch_id, _loaded_at) as latest_batch_id, "
                    "count(distinct _batch_id) as batch_count from ("
                    "select _loaded_at, _batch_id from raw.raw_oracle_orders "
                    "union all "
                    "select _loaded_at, _batch_id from raw.raw_erp_products "
                    "union all "
                    "select _loaded_at, _batch_id from raw.raw_adaptive_forecast "
                    "union all "
                    "select _loaded_at, _batch_id from raw.raw_salesforce_accounts"
                    ")"
                )
            except DataSourceError as error:
                st.info(f"Freshness unavailable: {error}")
            else:
                if freshness.empty or pd.isna(freshness.loc[0, "last_loaded_at"]):
                    st.info("No load timestamps found in the raw layer yet.")
                else:
                    row = freshness.iloc[0]
                    st.metric(
                        "Last Loaded",
                        pd.Timestamp(row["last_loaded_at"]).strftime("%Y-%m-%d %H:%M %Z").strip(),
                    )
                    st.caption(
                        f"Batch {row['latest_batch_id']} &middot; "
                        f"{int(row['batch_count'])} distinct batch(es) on file."
                    )

    with obs_right:
        with panel("fig-last-dbt-build"):
            st.markdown("**Last dbt Build**")
            run_results = _load_dbt_run_results()
            if run_results is None:
                st.info(
                    "No transform/target/run_results.json found. Run "
                    "`make build` to produce one."
                )
            else:
                results = run_results.get("results", [])
                model_results = [r for r in results if r["unique_id"].startswith("model.")]
                test_results = [r for r in results if r["unique_id"].startswith("test.")]
                models_ok = sum(1 for r in model_results if r["status"] == "success")
                tests_passed = sum(1 for r in test_results if r["status"] == "pass")
                tests_failed = len(test_results) - tests_passed
                generated_at = run_results.get("metadata", {}).get("generated_at", NULL_MARKER)
                elapsed = run_results.get("elapsed_time")

                st.metric("Pipeline Last Ran", str(generated_at))
                bm1, bm2, bm3 = st.columns(3)
                bm1.metric("Models Built", f"{models_ok}/{len(model_results)}")
                bm2.metric("Tests Passed", f"{tests_passed}/{len(test_results)}")
                bm3.metric(
                    "Total Run Time",
                    f"{elapsed:.1f}s" if elapsed is not None else NULL_MARKER,
                )
                if tests_failed:
                    st.warning(f"{tests_failed} test(s) failed on the last run.")

                slowest = sorted(model_results, key=lambda r: -r["execution_time"])[:5]
                if slowest:
                    slowest_df = pd.DataFrame([
                        {
                            "Model": r["unique_id"].split(".")[-1],
                            "Execution Time (s)": r["execution_time"],
                        }
                        for r in slowest
                    ])
                    st.markdown("Slowest models:")
                    st.dataframe(
                        slowest_df.style.format({"Execution Time (s)": "{:.2f}"}),
                        use_container_width=True,
                        hide_index=True,

                    )


def main():
    try:
        inject_theme()
        data = load_data()
        render_instrument_header(data)
        st.caption(
            "Actual vs plan at product x region x month, with order-level delivery "
            "detail and full DQ reconciliation."
        )

        regions, families, months = render_filter_row(data)
        filtered = apply_filters(data, regions, families, months)

        tabs = st.tabs([
            "Executive Summary",
            "Revenue Variance",
            "Delivery Performance",
            "Product & Margin",
            "Data Quality & Observability",
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
            # cover the whole source dataset regardless of the filter row's
            # region/family/month selections, or the totals stop reconciling.
            tab_data_quality(data)

    except DataSourceError as error:
        st.error(str(error))


main()
