# Observability

This is the smallest set of things that let someone answer "is the pipeline
healthy, and what does it cost" without opening a monitoring platform. It
leans entirely on capability Snowflake and dbt already provide.

## What is implemented now

1. **dbt source freshness**
   (`transform/models/staging/_sources.yml`). Every RAW source declares
   `loaded_at_field: _loaded_at` (the timestamp `ingest/load_raw.py` writes on
   every load) plus a `warn_after` / `error_after` threshold sized for a daily
   batch: 30 hours to warn, 48 hours to error, so a run that's a few hours
   late doesn't page anyone but two missed days in a row does. This is a
   Snowflake-target capability -- the DuckDB adapter does not implement
   freshness checks, so the config is present but inert on the default
   DuckDB target, and `dbt build` there stays at 94/94. Run it for real
   against Snowflake with:
   ```bash
   uv run dbt source freshness --project-dir transform --profiles-dir transform --target snowflake
   ```
   (not executed in this pass -- see snowflake/README.md on trial session
   tokens; this is documented rather than run live for the same reason the
   rest of this repo is candid about what has and hasn't been exercised
   against a live account.)

2. **The query pack** (`snowflake/observability.sql`) -- five small, commented,
   read-only queries over data Snowflake already collects: RAW's own
   `_loaded_at`/`_batch_id`, `INFORMATION_SCHEMA.TABLES` row counts per layer,
   and three `SNOWFLAKE.ACCOUNT_USAGE` views (`WAREHOUSE_METERING_HISTORY` for
   cost, `QUERY_HISTORY` for recent performance and failures,
   `TABLE_STORAGE_METRICS` for growth). No new object is created and nothing
   is scheduled -- it's a file you run by hand when you want an answer.

3. **The dashboard's existing pipeline panel**, built from
   `transform/target/run_results.json` (see `app/streamlit_app.py`,
   `_load_dbt_run_results`). This already surfaces the latest build's model
   and test results in the Streamlit app without any new plumbing -- it's
   listed here because it *is* part of this project's observability, not
   because anything changed about it in this pass.

Together these three answer: is the data fresh (1), what does the pipeline's
shape, cost and query health look like right now (2), and what happened on
the last run (3) -- without adding a single moving part.

## What is deliberately not built, and the trigger for building it

- **Persisting dbt artifacts into a queryable table**
  (`dbt build --target snowflake` already writes `run_results.json` and
  `manifest.json` per run; the missing piece is loading those into a table --
  dbt's own `--record-timing-info`/artifact-upload pattern, or a small `COPY
  INTO` from the JSON -- so test-pass/fail history survives past the current
  run and becomes queryable trend data). **Build this once more than one
  person depends on the pipeline** -- a single owner can just read the
  current `run_results.json` or the query pack above; history only earns its
  keep once someone other than the person who ran the build needs to answer
  "was this passing last Tuesday" without asking them.

- **Snowflake alerts with a notification integration** (a scheduled `ALERT`
  object wired to email/Slack/PagerDuty on a freshness or test-failure
  condition). **Build this once someone needs to be woken up, rather than
  merely informed the next time they open a dashboard.** A daily batch with
  one operator does not need a page; the query pack and the dashboard panel
  above are enough to notice a problem the next time either is opened.

## Why not build them anyway

A portfolio project moving a few hundred rows once a day does not need
persisted test history or paging alerts, and building either would be the
wrong call here: they add objects, a notification channel to configure and
secure, and ongoing maintenance surface, in exchange for solving a problem
this project doesn't have (multiple stakeholders, an on-call rotation).
The two things above are sized to be added quickly and specifically once
their stated trigger actually happens -- not built speculatively now.
