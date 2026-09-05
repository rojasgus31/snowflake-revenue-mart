# Revenue Performance Mart — Design

**Date:** 2026-09-04
**Status:** Approved, ready for implementation planning
**Context:** Sr. Data Engineer technical assessment. Build a Snowflake-based analytical
solution over four fictitious sources (Oracle orders, Salesforce accounts, Adaptive
forecast, product master), targeting `analytics.mart_revenue_performance`.

---

## 1. Purpose

The assessment brief asks for a model containing actual revenue, forecast revenue,
revenue variance, delivery status, and estimated gross margin. It also states the data
contains deliberate quality defects that the candidate must find, document, and handle.

The brief explicitly does not require a production-ready build. This project builds one
anyway, because the differentiating signal is a repository a reviewer can clone and run,
not a slide deck describing one.

### Success criteria

1. `dbt build` succeeds end to end against both DuckDB and Snowflake from a clean clone.
2. Every deliberate data defect is detected by a named test, and its handling decision is
   documented with a rationale.
3. `mart_revenue_performance` reconciles to source: every source revenue dollar is either
   in the mart or in a rejects table with a stated reason. No dollar disappears silently.
4. A public URL renders the mart without warehouse credentials.
5. One transform exists in both dbt SQL and PySpark, with a test asserting the two produce
   identical output.

### Non-goals

- Orchestration (Airflow, Dagster). `dbt build` plus CI is the scheduler for a dataset of
  450 rows. Documented as the production next step.
- SCD2 history on dimensions. Designed for, not built — see §9.
- Real Fivetran or Coalesce accounts. Their *shape* is reproduced; their bill is not.

---

## 2. Source data profile

| Source | Rows | Grain | Notes |
|---|---|---|---|
| `oracle_orders.csv` | 128 | order line | Jan–Jun 2025. No region column. |
| `salesforce_accounts.csv` | 20 | customer | Sole source of `region`. |
| `adaptive_forecast.csv` | 287 | product × region × month | Jan–Jun 2025, 4 regions. |
| `product_master.csv` | 12 | product | Sole source of `standard_cost`. |

### The central modeling problem

The brief requests one table holding both forecast revenue (grain: product × region ×
month) and delivery status (grain: order). **These grains are incompatible.** A single
table cannot hold both without either fabricating forecast precision at order level or
discarding order-level delivery detail.

Compounding this: orders carry no region. Region is reachable only through
`orders.customer_id → salesforce_accounts.customer_id → region`. The join to forecast
therefore depends on a dimension lookup that one order row cannot satisfy (see defect
D2). Resolving this grain conflict honestly is the core of the exercise.

---

## 3. Data quality defects and handling decisions

Twelve defects were confirmed by profiling. Each row below is a decision, not an
observation.

| ID | Defect | Evidence | Decision | Rationale |
|---|---|---|---|---|
| D1 | Duplicate primary key | `ORD00005` appears twice; `quantity` 31 vs 99, all other columns identical | **Reject both rows.** Flag as `AMBIGUOUS_DUPLICATE` | No `updated_at` or sequence column exists to identify the later record. Choosing either value fabricates a fact. Correct fix is upstream: source-system CDC timestamps, which a Fivetran-style connector would supply. |
| D2 | Orphan customer FK | `CUST999` in orders, absent from Salesforce | **Keep the row.** `region = 'UNMAPPED'` | The revenue is real. Dropping it breaks source reconciliation. `UNMAPPED` cannot join to any forecast row, so it surfaces as unexplained variance — visible rather than lost. |
| D3 | Orphan product FK | `PROD999` in orders, absent from product master | **Keep the row.** `gross_margin = NULL`, `has_standard_cost = false` | Revenue is knowable from the order; margin is not. Defaulting cost to zero would report 100% margin — a plausible-looking lie. NULL is honest. |
| D4 | NULL `unit_price` | `ORD90106` | **Reject.** `MISSING_UNIT_PRICE` | Revenue is the mart's primary measure and is undefined without price. Imputing from product average invents revenue. |
| D5 | NULL `order_date` | `ORD90107` | **Reject.** `MISSING_ORDER_DATE` | Revenue is recognized on `order_date`; without it the row cannot be assigned to a month, which is the mart's grain. |
| D6 | Non-positive quantity | 1 row | **Reject.** `INVALID_QUANTITY` | The source has no return or credit-memo type, so a negative quantity is unexplained rather than meaningful. |
| D7 | Status contradiction | All 13 `Cancelled` orders carry an `actual_delivery_date` | **Keep. Status wins; ignore the date.** | A cancelled order was not delivered. Log the contradiction to the DQ summary as a source-system bug worth reporting back. |
| D8 | Delivery precedes order | 1 row, `actual_delivery_date < order_date` | **Keep.** `delivery_status = 'Unknown'` | The dates are untrustworthy; the revenue is not. Degrade the one attribute, retain the row. |
| D9 | ID format anomaly | `ORD9xxxx` batch alongside `ORD000xx` | **Keep.** Tag `source_batch` | Two extracts, not corruption. Making the batch explicit lets analysts spot extract-level skew. |
| D10 | Soft-deleted dimensions | 2 accounts `active_flag = FALSE`; 1 product `Discontinued`, 1 `Pending` | **Keep all.** Expose the flags | Historical orders against a now-inactive account are still real historical revenue. Filtering belongs in the BI layer, not the warehouse. |
| D11 | Unnormalized location | `plant_location = "San Jose, Costa Rica"` | **Split** into `plant_city`, `plant_country` | Compound string blocks grouping by country. |
| D12 | Grain mismatch | Forecast is monthly/regional; orders are line-level and regionless | **Two fact tables plus a conformed mart** — see §4 | Detailed in the architecture section. |

### Rejects are written, not dropped

Rejected rows are routed to `stg_<source>__rejects` with `dq_failure_reason` and the full
original payload. `dbt build` succeeds with rejects present — a pipeline that halts on one
bad row is not production-ready. A `dq_reject_summary` model reports counts and dollar
value by reason, so the cost of every exclusion is visible.

---

## 4. Architecture

### Layers

```
RAW          ingest/load_raw.py
             All columns VARCHAR. Adds _loaded_at, _source_file, _batch_id.
             Append-only, zero transformation — Fivetran-shaped landing.

STAGING      stg_oracle__orders          stg_oracle__orders_rejects
             stg_salesforce__accounts    stg_salesforce__accounts_rejects
             stg_adaptive__forecast      stg_adaptive__forecast_rejects
             stg_erp__products           stg_erp__products_rejects
             Typecast, rename to business names, dedup, apply DQ rules.

INTERMEDIATE int_orders_enriched          (+ region from customer, + cost from product)
             int_order_revenue_monthly    (aggregate to product × region × month)
                                          ← the PySpark twin, see §6

MARTS        dim_customer  dim_product  dim_date  dim_region
             fct_orders       (order-line grain)
             fct_forecast     (product × region × month)
             mart_revenue_performance
             dq_reject_summary
```

Nothing downstream of staging references RAW. Staging is the only layer that knows source
column names — a rename in Oracle changes exactly one file.

### The two-fact resolution

`fct_orders` holds order-line grain with per-order `delivery_status`, line revenue, and
line margin. `fct_forecast` holds product × region × month.

`mart_revenue_performance` sits at product × region × month. It aggregates `fct_orders`
up to that grain and **full-outer-joins** `fct_forecast`.

The full outer join is load-bearing. An inner join would drop forecast rows that received
no orders — which is exactly the variance a revenue performance report exists to show —
and would drop `UNMAPPED` actuals, breaking reconciliation. Both sides survive:

- Forecast with no actuals → actual revenue 0, variance = full negative forecast.
- Actuals with no forecast (`UNMAPPED`, or an unforecast product) → forecast NULL,
  variance flagged `NO_FORECAST`.

### Rejected alternative

Allocating monthly forecast down to individual orders pro-rata was considered and
rejected. It would produce a single order-grain table satisfying the brief's literal
wording, but the allocation weights are invented — the source contains no basis for
distributing a monthly regional forecast across orders. It manufactures precision.

---

## 5. Business rules

These are decisions, not derivations, and each is stated in the project README.

**R1 — Revenue recognition status.** Actual revenue counts `Shipped` orders only. `Open`
is pipeline, not revenue. `Cancelled` is excluded. Because the forecast was produced
before cancellations were known, cancelled demand still sits in forecast revenue. The
resulting gap is genuine business variance and must not be engineered away.

**R2 — Recognition date.** Revenue is recognized on `order_date`, not
`actual_delivery_date`, aligning actuals to the forecast's monthly grain. Delivery-date
recognition is a one-line change, noted in the README as a configurable alternative.

**R3 — Measures.**

```
gross_revenue      = quantity * unit_price
gross_margin       = (unit_price - standard_cost) * quantity   -- NULL when cost unknown
revenue_variance   = actual_revenue - forecast_revenue
variance_pct       = revenue_variance / NULLIF(forecast_revenue, 0)
```

`NULLIF` guards division by zero. Margin is NULL rather than zero when `standard_cost` is
unavailable (D3), and aggregate margin sums are accompanied by a
`margin_coverage_pct` column so a partially-NULL total is never read as complete.

**R4 — Delivery status**, evaluated in order:

| Condition | Status |
|---|---|
| `order_status = 'Cancelled'` | `Cancelled` |
| `actual_delivery_date IS NULL` | `Not Delivered` |
| `actual_delivery_date < order_date` | `Unknown` |
| `actual_delivery_date <= promised_delivery_date` | `On Time` |
| otherwise | `Late` |

At mart grain this becomes `on_time_delivery_rate`, plus counts per status.

**R5 — Region.** Sourced from Salesforce via customer. Unresolvable customers get
`UNMAPPED` (D2). The four valid regions — LATAM, North America, EMEA, APAC — are seeded as
`dim_region` and tested for referential integrity against both facts.

---

## 6. The PySpark twin

`int_order_revenue_monthly` — the aggregation at the center of the mart — is implemented
twice:

- `models/intermediate/int_order_revenue_monthly.sql` (dbt; runs on Snowflake and DuckDB)
- `spark/int_order_revenue_monthly_spark.py` (PySpark DataFrame API; Databricks-shaped)

A test reads both outputs and asserts row-for-row equality. This demonstrates the
Databricks capability the role calls for and simultaneously proves the transform is
engine-portable — a stronger claim than an unverified standalone notebook.

---

## 7. Testing

**Schema tests** (dbt built-ins): `unique` and `not_null` on every primary key,
`relationships` on every foreign key, `accepted_values` on `order_status`,
`delivery_status`, `region`, `segment`, `lifecycle_status`.

**Defect regression tests.** Each of D1–D12 gets a singular test asserting the handling
decision held — e.g. `assert_ord00005_quarantined`, `assert_unmapped_region_present`,
`assert_null_margin_for_unknown_cost`. These fail loudly if a future change silently
starts dropping rows.

**Reconciliation test.** The strongest guarantee in the project:

```
source_gross_revenue = mart_actual_revenue
                     + rejected_revenue
                     + cancelled_revenue
                     + open_order_revenue
```

Every source dollar is accounted for in exactly one bucket. This is a singular dbt test
that fails the build on any leak.

**Engine parity test.** dbt SQL output equals PySpark output for
`int_order_revenue_monthly`.

---

## 8. Deployment and access

**Snowflake trial** is the primary target: real DDL, warehouse sizing, RBAC roles
(`LOADER` / `TRANSFORMER` / `REPORTER`), and layered schemas (`RAW`, `STAGING`,
`ANALYTICS`).

**DuckDB** is a second dbt target running the identical model code. The compiled `.duckdb`
file is committed (450 rows, well under a megabyte), so a reviewer clones and runs
`dbt build` in roughly twenty seconds with no account, and the artifact outlives the
30-day trial.

**Streamlit** renders the mart — variance by region and product family, delivery
performance, margin coverage, and a DQ panel showing rejected rows with reasons. Deployed
to Streamlit Community Cloud, reading the committed DuckDB file, yielding a public URL
requiring no credentials.

**Power BI readiness** is documented in `docs/POWER_BI.md` rather than built, since Power
BI Desktop does not run on macOS. The document covers the star schema layout, DAX
definitions for each measure in R3, an import-versus-DirectQuery recommendation for this
data volume, and the incremental-refresh partitioning strategy. The mart is shaped as a
conformed star specifically so this document describes a connection rather than a
rewrite.

---

## 9. Deferred, with reasons

Named here so their absence reads as a decision rather than an oversight.

| Deferred | Why | Trigger to build |
|---|---|---|
| SCD2 snapshots on `dim_customer` | Sources carry no change history; a snapshot over static CSVs would capture nothing | First incremental load with a changed account |
| Orchestration (Airflow / Dagster) | 450 rows, one daily batch. `dbt build` in CI is sufficient | Multiple schedules or cross-system dependencies |
| Incremental materialization | Full refresh completes in seconds | Roughly 10M+ order rows |
| Streams and Tasks | Batch is adequate for a daily-grain report | Intraday freshness requirement |
| Real Fivetran connector | Paid, and the assessment provides files rather than live systems | Actual Oracle Fusion / Salesforce credentials |
