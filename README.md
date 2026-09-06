# Revenue Performance Mart

A Snowflake data pipeline over four source systems — Oracle Fusion orders,
Salesforce accounts, Adaptive Planning forecast, and an ERP product master —
producing `analytics.mart_revenue_performance`.

Built for the Sr. Data Engineer technical assessment. The brief said a
production-ready build was not required. This one runs.

## Quick start

```bash
make install
make all
make app
```

About 35 seconds from a clean checkout to a built warehouse (measured via
`make clean && make install && make all`). No Snowflake account needed —
DuckDB is the default target and the model code is identical on both engines.
See `snowflake/README.md` to run it against a real Snowflake trial.

CI (`.github/workflows/ci.yml`) does not invoke `make` at all — it runs the
same underlying commands directly, one step per line: `uv sync --all-groups`,
`uv run dbt deps`, an explicit `python -c "from dbt.adapters.snowflake import
SnowflakeAdapter"` import check (dbt's plugin loader swallows adapter import
errors, so `dbt --version` looking healthy is not proof the Snowflake adapter
actually works), `uv run python ingest/load_raw.py`, `uv run dbt build`, and
`uv run pytest -v`. It is the same work `make all` does locally, just spelled
out rather than routed through the Makefile.

## The central design decision

The brief asks for one table containing forecast revenue and delivery status.
**Those live at different grains.** Forecast is product × region × month.
Delivery status is per order. No single table holds both without either
fabricating forecast precision at order level or discarding delivery detail.

The resolution is two facts and one conformed mart:

- `fct_orders` — order grain, with per-order delivery status
- `fct_forecast` — product × region × month
- `mart_revenue_performance` — monthly, aggregating the first and
  **full-outer-joining** the second

The full outer join is load-bearing. An inner join would drop forecast rows
that received no orders, which is exactly the variance the report exists to
show.

A third option — allocating monthly forecast down to individual orders
pro-rata — was considered and rejected. The source carries no basis for those
allocation weights; inventing them manufactures precision the business does not
have.

## Data quality

Twelve defects were found by profiling. Each has a documented handling decision
with a rationale in
[the design spec](docs/superpowers/specs/2026-09-04-snowflake-revenue-mart-design.md#3-data-quality-defects-and-handling-decisions).

The governing principle: **no row is ever deleted.** Rows that cannot be trusted
are routed to a rejects table with a `dq_failure_reason` and reported in
`dq_reject_summary` with their dollar value. `stg_oracle__orders` holds 122 of
the 128 source rows; the other 6 are quarantined — `INVALID_QUANTITY` alone
catches two of them, `ORD90103` (quantity `-15`) and `ORD90104` (quantity `0`).

A dbt test then proves the whole thing balances, and on this build it balances
exactly, to the cent:

```
source revenue     = recognised     + open/cancelled   + rejected
9,448,724.11       = 6,168,239.01   + 3,006,773.46      + 273,711.64
```

That is the concrete evidence behind the "no dollar disappears" claim — not a
tolerance band, an exact match. If a single dollar goes missing anywhere in the
pipeline, `dbt build` fails.

The full suite this build was verified against: **94 dbt results (15 materialized
models + 1 seed + 78 tests) and 7 pytest tests, all passing.** The project defines 16
models; `stg_oracle__orders_flagged` is ephemeral, so it compiles into its consumers
rather than producing a result row of its own.

Two decisions worth calling out:

- **`ORD00005` is duplicated with conflicting quantities (31 and 99)** and the
  source has no timestamp to identify the later record. Both copies are
  rejected. Choosing either one would fabricate a fact; the real fix is
  upstream CDC metadata.
- **`PROD999` has no standard cost**, so its margin is NULL rather than zero.
  Zero would report a 100% margin — a plausible-looking lie. Revenue is
  knowable; margin is not.

## Stack

| Layer | Technology |
|---|---|
| Ingestion | Python, landing all-VARCHAR with load metadata (Fivetran-shaped) |
| Warehouse | Snowflake (primary, dbt-snowflake 1.10.8), DuckDB 1.5.x (local mirror via dbt-duckdb 1.10.1, identical model code) |
| Transformation | dbt-core 1.10.23, layered staging → intermediate → marts |
| Distributed compute | PySpark 3.5.9 on Temurin JDK 17, with a test asserting parity against the SQL |
| BI | Streamlit 1.63.0 (ready to deploy — see below), Power BI ([connection guide](docs/POWER_BI.md)) |
| Runtime | Python 3.12 |
| CI | GitHub Actions running the full build and test suite |

### Deploying the dashboard

The Streamlit app is not currently deployed anywhere — that requires a
Streamlit Community Cloud account, which is the reader's, not this
project's, to create. It is ready to deploy: point Streamlit Community Cloud
at this repository with main file path `app/streamlit_app.py`. It reads the
committed `warehouse.duckdb` directly and needs no credentials or secrets to
be set.

## Layout

```
ingest/        CSV → RAW, no transformation
transform/     dbt project (staging, intermediate, marts)
spark/         PySpark twin of the monthly aggregation
app/           Streamlit dashboard
snowflake/     bootstrap DDL, roles, warehouses
tests/         pytest — ingestion and Spark parity
docs/          design spec, implementation plan, Power BI guide
```

## About the committed warehouse.duckdb

`warehouse.duckdb` is checked into this repository on purpose, even though it
is a ~6 MB binary. Three things follow from that:

- It is committed so the Streamlit dashboard can run on Streamlit Community
  Cloud with no warehouse credentials at all, and so a reviewer can open the
  app immediately without building anything first.
- Every `make build` rewrites this file from scratch. That means it will show
  up as modified in `git status` after any local rebuild — that is expected
  behavior, not a sign something went wrong.
- It only needs to be re-committed when the underlying data actually changes,
  not after every local rebuild a developer happens to run.

## Deliberately not built

Named so their absence reads as a decision:

| Deferred | Why | Build it when |
|---|---|---|
| SCD2 snapshots | Sources carry no change history | The first load with a changed account |
| Airflow / Dagster | 450 rows, one daily batch; `dbt build` in CI suffices | Multiple schedules or cross-system dependencies |
| Incremental models | Full refresh takes seconds | Roughly 10M+ order rows |
| Streams and Tasks | Batch is adequate for a daily-grain report | Intraday freshness is required |
