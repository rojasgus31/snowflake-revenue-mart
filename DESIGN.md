# Design system

Governs `app/streamlit_app.py`, `app/theme.css` and `.streamlit/config.toml`.
Written as rules a second person can follow to add a new tab without it
looking foreign. Not an essay: if it is not a rule, it is not here.

## Colour tokens

| Token | Hex | Role | When it may be used |
|---|---|---|---|
| `--surface` | `#FAF8F6` | Page background | Body background only. |
| `--surface-raised` | `#F3EFED` | Raised band background | The instrument header, the filter strip. Never a card, never a rounded box. |
| `--border` | `#DAD7D3` | Hairline rule | Every ruled band separator, chart gridlines, axis lines. Never a heavy border. |
| `--text` | `#24211E` | Body / heading text | All primary text. |
| `--muted` | `#6C6864` | Secondary text | Captions, labels, axis ticks. |
| `--red` | `#C2181D` | **Data weight.** Below-plan variance, Late delivery. | Only on data that is genuinely below plan or a judged-bad outcome. Never on chrome, never decorative. |
| `--teal` | `#007475` | **Data weight.** At/above-plan variance, On Time, recognised revenue. | Only on data that is at or above plan, or a judged-good outcome. |
| `--violet` | `#754D9E` | Data-quality axis | Quarantined revenue, `NO_FORECAST` flag, the one missing-standard-cost order. Never variance. |
| `--amber` | `#B45309` | In-flight, not-yet-judged state | `Not Delivered`, open pipeline. Never a structural exclusion (that is grey) and never a judged outcome. |
| `--accent` | `#A45951` | **Chrome weight.** Half the chroma of `--red`, a touch lighter. | Active tab label/underline, links, focus rings, the heading marker, the slider. Never on data. |
| `--chip-tint` / `--chip-text` / `--chip-border` | `#FCEAE7` / `#791A18` / `#D0938B` | Chrome weight, filter chips | A selected filter is not a semantic state: light tint background, dark text, subtle border. Never the saturated `--red`/`--accent` fill. |
| `#8A8580` (`COLOR_GREY_MID`, code-only) | Structural exclusion, no judgement | Cancelled orders. Lighter than `--text`/`--muted` so a bar never reads near-black. |

**The two-weight rule:** one hue (27°) runs through both chrome and data.
Data gets the full-strength, saturated value (`--red`); chrome gets the
same hue at roughly half the chroma and a touch lighter (`--accent`). A
reader must always be able to tell a bold red bar (a shortfall) from a
light red underline or chip (a selection or "you are here").

## Type scale

Three roles, one wide scale: `11 / 13 / 16 / 22 / 32 / 48` (px). A decisive
spread, not a uniform ramp: weight carries as much contrast as size.

| Size | Token | Used for |
|---|---|---|
| 11px | `--text-2xs` | Eyebrows, dense labels, table headers: always IBM Plex Sans Condensed, uppercase, `letter-spacing: 0.04 to 0.06em`. |
| 13px | `--text-xs` | Captions, callouts, secondary body text. |
| 16px | `--text-sm` | Baseline body text (Streamlit default). |
| 22px | `--text-md` | `h2`, secondary figures, the instrument title. |
| 32px | `--text-lg` | `h1`, `h3` (section subheads), section headers. |
| 48px | `--text-xl` | The single most important figure on a tab (e.g. Actual vs Plan). At most one per tab. |

**Font roles:**
- **IBM Plex Sans**: interface text, body copy, headings (h1 to h3).
- **IBM Plex Mono**: every numeral: figure values, table numerals (right-aligned), metric deltas.
- **IBM Plex Sans Condensed**: dense labels, table headers, eyebrows, `h4` section titles, the instrument header's meta line and the balance bar's label. Always uppercase with generous letter-spacing at 11px. Loaded via `@import` at the top of `app/theme.css` from the same Google Fonts host (`fonts.googleapis.com`) the other two faces use.

Pair a heavy weight (600 to 700) on the figure against a light/regular
weight on its label: never the same weight on both.

## Spacing

8px base scale, used **unevenly**:

| Token | Value | Rule |
|---|---|---|
| `--space-1` | 8px | Between a figure and its own sub-line; between a heading marker and its text. Things that belong to each other. |
| `--space-2` | 16px | Inside a raised band (header, filter strip) top/side padding. |
| `--space-3` | 24px | A tight break between two related blocks that still deserve a visible split (`section_rule("tight")`). |
| `--space-4` | 32px | A real section break within a tab (`section_rule()` default). |
| `--space-5` | 48px | Reserved for the largest breaks; not yet spent inside a tab body. |

Uniform padding everywhere is the defect this scale fixes. If two rules in
the same view use the same spacing value, that is only correct when they
are doing the same job (both section breaks, or both figure/sub-line
gaps): never apply the largest available value by default.

## Layout rules

- **Ruled bands, not cards.** No bordered/filled boxes, no shadows, no
  border-radius, no nested containers. A region is set off from its
  neighbour by one `1px solid var(--border)` rule (`.band-rule` /
  `.band-rule--tight`, drawn by `section_rule()`), never by a box around
  it. The instrument header and filter strip are the one exception that
  gets a raised background (`--surface-raised`): still bounded only by a
  top and bottom rule, never a full border or radius.
- **Table rows:** compact, `row_height=32` on every `st.dataframe(...)`
  call. Hairline row separators only (Streamlit's own default), no zebra
  striping, no heavy borders.
- **Numerals:** right-aligned, IBM Plex Mono, tabular figures
  (`font-variant-numeric: tabular-nums`) in every table and every figure
  value.
- **Gutters:** 12px (`--gutter`) between adjacent columns, enforced on
  every `[data-testid="stHorizontalBlock"]` regardless of the `gap=`
  argument passed to `st.columns(...)`.
- **Page padding:** the block container's top/side padding is reduced so
  content occupies the width it has, not a wide empty margin.

## Component inventory

| Component | Function / class | Notes |
|---|---|---|
| Instrument header | `render_instrument_header()`, `.st-key-instrument-header` | Persistent above the tabs on every tab: dashboard name, data source + backend, last loaded time, dbt tests passing. Raised surface, top/bottom rule, never a card. |
| Balance bar | `render_balance_bar()`, `.balance-bar` | The signature element. One 10px-tall, full-width, four-segment proportional bar: recognised (teal) + open (muted grey) + cancelled (dimmer grey) + quarantined (violet) = source total. Exact values on hover via the native `title` attribute. Always visible in the header, always sums to the whole. |
| Figure block | `figure_card()`, `.figure-block` | A label (11px condensed, uppercase), a value (mono, 48px primary / 22px secondary), and a sub-line (13px). No border, no fill. |
| Chart block | `_style_plot()` | One shared Plotly chrome style: `plotly_white` template, surface-coloured background, muted axis text in Plex Mono, no gridline colour beyond `--border`. |
| Table | `st.dataframe(..., row_height=32, width="stretch")` | See Layout rules above. |
| Filter strip | `render_filter_row()`, `.st-key-filter-row` | Three inline filters (region, product family, month range) in a raised band above the tabs. No hue on the chips beyond the chrome accent tint: a filter selection is not a semantic state. |
| Section rule | `section_rule(weight="section" | "tight")` | The one hairline break between regions of a tab; replaces every bare `st.markdown("---")`. |

## Bans

- No cards: no bordered or filled boxes around content.
- No shadows.
- No gradients.
- No pie or donut charts.
- No colour on a control that is not conveying data (a filter chip, a
  slider handle, a tab label get the chrome accent tint only, never a
  saturated fill).
- No glassmorphism, no side-stripe borders, no gradient text.
- No hero-metric template (a dashboard is not a marketing tile grid).
- No neon, no motion or animation, no emoji.
- No em dashes in copy, and no `--` standing in for one.
- No `use_container_width`; use `width="stretch"`.
