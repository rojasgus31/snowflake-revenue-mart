# Snowflake run evidence

This directory holds durable proof that the pipeline was actually run against
a real Snowflake account, not just that the code is written to support one.
That distinction matters because a Snowflake trial expires after 30 days and
the account behind it will eventually be gone; the files captured here do not
expire. Once populated, this directory is the permanent record of that run,
independent of whether the trial account still exists.

**Until this directory is populated with the files below, the project has
been validated on DuckDB only.** The Snowflake path (`snowflake/README.md`,
`transform/profiles.yml`'s `snowflake` target, `ingest/load_raw.py --target
snowflake`) is code-complete, but code-complete is not the same claim as
proven-against-a-live-account.

## What belongs here

- **`snowflake_run_results.json`** -- dbt's own run artifact
  (`transform/target/run_results.json`) from a `dbt build` executed with
  `--target snowflake`. This is dbt's machine-readable record of every model
  and test that ran, its status, and its timing.
- **`snowflake_build.log`** -- the full terminal output of that same build:
  the loader run plus `dbt build`, captured end to end.
- **Screenshots**, taken in Snowsight after the build:
  - The mart returning its row count -- `select count(*) from
    revenue_analytics.analytics.mart_revenue_performance;` -- proving the
    table exists and is populated in a real account.
  - The three roles from `snowflake/01_bootstrap.sql` (`LOADER`,
    `TRANSFORMER`, `REPORTER`) as they appear in Snowsight's role list or
    `show roles`, proving the role/warehouse split was actually provisioned.
  - Warehouse credit usage (Snowsight's Admin > Cost Management, or `show
    warehouses`), proving the build ran on real compute and roughly what it
    cost.

## How to produce it

`snowflake_run_results.json` and `snowflake_build.log` are produced together
by running `make snowflake-proof` (see the Makefile) once Snowflake
credentials are exported per `snowflake/README.md`. That target tees the
combined loader-and-build output to `snowflake_build.log` and copies
`transform/target/run_results.json` here afterward. It prints a reminder of
the screenshots above, which still have to be taken by hand in Snowsight --
there is no CLI equivalent for those.
