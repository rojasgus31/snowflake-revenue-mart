# Snowflake run evidence

This directory holds durable proof that the pipeline was actually run against
a real Snowflake account, not just that the code is written to support one.
That distinction matters because a Snowflake trial expires after 30 days and
the account behind it will eventually be gone; the two files committed here
do not expire with it. Once a trial account is gone, these files remain the
permanent, date-independent record that the run happened — independent of
whether the account itself still exists.

## What is here

- **`snowflake_run_results.json`** -- dbt's own run artifact
  (`transform/target/run_results.json`) from the `dbt build --target
  snowflake` that was actually executed. This is dbt's machine-readable
  record of every model and test that ran, its status, and its timing: 94
  results, 0 errors.
- **`snowflake_build.log`** -- the full terminal output of that same build:
  the loader run plus `dbt build`, captured end to end. The log records
  `target='snowflake'`, and it shows the loader writing all four RAW tables
  (128 / 20 / 287 / 12 rows) before the build ran on top of them.

Together these two files prove the run happened on a real account and that
it produced results matching the DuckDB build exactly: `dbt build` at 94
results / 0 errors, `mart_revenue_performance` at 289 rows, `fct_orders` at
122, 6 rejects, 1 `UNMAPPED` region, and the same reconciliation to the cent
(`6,168,239.01` recognised + `3,006,773.46` open/cancelled + `273,711.64`
rejected = `9,448,724.11`).

## Still outstanding

Screenshots, to be taken by hand in Snowsight — there is no CLI equivalent
for these, and they have not been captured yet:

- The mart returning its row count -- `select count(*) from
  revenue_analytics.analytics.mart_revenue_performance;` -- proving the
  table exists and is populated in a real account.
- The three roles from `snowflake/01_bootstrap.sql` (`LOADER`,
  `TRANSFORMER`, `REPORTER`) as they appear in Snowsight's role list or
  `show roles`, proving the role/warehouse split was actually provisioned.
- Warehouse credit usage (Snowsight's Admin > Cost Management, or `show
  warehouses`), proving the build ran on real compute and roughly what it
  cost.

## How to reproduce this

`snowflake_run_results.json` and `snowflake_build.log` are produced together
by running `make snowflake-proof` (see the Makefile) once Snowflake
credentials are exported per `snowflake/README.md`. That target tees the
combined loader-and-build output to `snowflake_build.log` and copies
`transform/target/run_results.json` here afterward. It prints a reminder of
the screenshots above.
