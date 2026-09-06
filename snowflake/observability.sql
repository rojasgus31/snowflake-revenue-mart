-- Observability query pack over Snowflake's own telemetry.
--
-- No new objects, no scheduled jobs. Every query here reads data Snowflake
-- already collects (RAW's own load metadata, or an ACCOUNT_USAGE /
-- INFORMATION_SCHEMA view) and answers one operational question a reader of
-- this pipeline would actually ask.
--
-- ---------------------------------------------------------------------
-- ACCOUNT_USAGE lags; INFORMATION_SCHEMA is real-time. Read this first.
-- ---------------------------------------------------------------------
-- The SNOWFLAKE.ACCOUNT_USAGE views used below (WAREHOUSE_METERING_HISTORY,
-- QUERY_HISTORY, TABLE_STORAGE_METRICS) are populated on a delay -- commonly
-- up to about 45 minutes, and longer for some views (TABLE_STORAGE_METRICS
-- can lag several hours). They will not show a build that just finished.
-- Their matching INFORMATION_SCHEMA table functions (QUERY_HISTORY,
-- WAREHOUSE_METERING_HISTORY, etc.) are real-time but only retain a much
-- shorter window (7 days, vs. up to a year in ACCOUNT_USAGE) and are scoped
-- to the current session's warehouse/database context.
-- Rule of thumb: reaching for "what just happened" (did the build I ran a
-- minute ago succeed?) -> INFORMATION_SCHEMA. Reaching for "what happened
-- over the last N days/weeks" (cost trend, storage growth) -> ACCOUNT_USAGE.
-- Getting this backwards is the single most common mistake with these views
-- -- e.g. concluding "the query never ran" when it simply hasn't landed in
-- ACCOUNT_USAGE yet.
--
-- ---------------------------------------------------------------------
-- Role required
-- ---------------------------------------------------------------------
-- Every SNOWFLAKE.ACCOUNT_USAGE query below requires the IMPORTED PRIVILEGES
-- grant on the SNOWFLAKE database, which ACCOUNTADMIN holds by default. None
-- of the three project roles (snowflake/01_bootstrap.sql: LOADER,
-- TRANSFORMER, REPORTER) has it, and REPORTER in particular cannot run any
-- of these -- it only has SELECT on the ANALYTICS schema. Run this file as
-- ACCOUNTADMIN, or as a role that has been separately granted IMPORTED
-- PRIVILEGES on the SNOWFLAKE database. This is expected: these are
-- operator/owner queries, not something a BI consumer of REPORTER should
-- ever need.

use role accountadmin;

-- =======================================================================
-- 1. Freshness and load batch, straight from RAW's own metadata
-- =======================================================================
-- What it answers: when did each RAW table last load, and how many rows
-- came in with that batch? This is the same _loaded_at column dbt's source
-- freshness check (transform/models/staging/_sources.yml) reads, queried
-- directly instead of through a dbt run.
-- If the answer looks wrong: a stale max(_loaded_at) means the loader
-- (ingest/load_raw.py --target snowflake) did not run today -- check that
-- the daily job actually fired before assuming a data problem downstream.
select
    'raw_oracle_orders' as table_name,
    max(_loaded_at) as last_loaded_at,
    datediff('hour', max(_loaded_at), current_timestamp()) as hours_since_load,
    count(distinct _batch_id) as batches_seen,
    count(*) as row_count
from revenue_analytics.raw.raw_oracle_orders
union all
select
    'raw_salesforce_accounts',
    max(_loaded_at),
    datediff('hour', max(_loaded_at), current_timestamp()),
    count(distinct _batch_id),
    count(*)
from revenue_analytics.raw.raw_salesforce_accounts
union all
select
    'raw_adaptive_forecast',
    max(_loaded_at),
    datediff('hour', max(_loaded_at), current_timestamp()),
    count(distinct _batch_id),
    count(*)
from revenue_analytics.raw.raw_adaptive_forecast
union all
select
    'raw_erp_products',
    max(_loaded_at),
    datediff('hour', max(_loaded_at), current_timestamp()),
    count(distinct _batch_id),
    count(*)
from revenue_analytics.raw.raw_erp_products
order by table_name;

-- =======================================================================
-- 2. Row counts per layer -- the pipeline's shape at a glance
-- =======================================================================
-- What it answers: how many rows sit in RAW vs. STAGING vs. ANALYTICS right
-- now, so a reader can see the pipeline narrow (rejects held back in
-- staging) and then fan back out (facts/marts) without opening every table.
-- If the answer looks wrong: RAW much bigger than expected -> check the
-- loader ran once, not repeatedly, without a truncate. ANALYTICS smaller
-- than STAGING's fact table -> rerun `dbt build` and read the
-- dq_reject_summary model for where rows were quarantined.
select 'raw' as layer, table_name, row_count
from revenue_analytics.information_schema.tables
where table_schema = 'RAW'
union all
select 'staging', table_name, row_count
from revenue_analytics.information_schema.tables
where table_schema = 'STAGING'
union all
select 'analytics', table_name, row_count
from revenue_analytics.information_schema.tables
where table_schema = 'ANALYTICS'
order by layer, table_name;

-- =======================================================================
-- 3. Pipeline cost -- what a build actually costs
-- =======================================================================
-- What it answers: credits consumed by this project's two warehouses
-- (wh_load_xs, wh_transform_xs) per day, so the reader can state a real
-- number instead of a guess. ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY is
-- account-wide, so this is filtered to just the two warehouses this project
-- owns.
-- If the answer looks wrong: a spike on a day with no load/build -> check
-- for a warehouse left running (auto_suspend disabled or raised) rather than
-- an actual heavier workload; both warehouses are provisioned with a
-- 60-second auto_suspend specifically to prevent this (snowflake/01_bootstrap.sql).
select
    warehouse_name,
    date_trunc('day', start_time) as usage_day,
    sum(credits_used) as credits_used,
    sum(credits_used_compute) as credits_used_compute,
    sum(credits_used_cloud_services) as credits_used_cloud_services
from snowflake.account_usage.warehouse_metering_history
where warehouse_name in ('WH_LOAD_XS', 'WH_TRANSFORM_XS')
  and start_time >= dateadd('day', -30, current_timestamp())
group by warehouse_name, usage_day
order by usage_day desc, warehouse_name;

-- =======================================================================
-- 4. Recent query performance and failures, scoped to this database
-- =======================================================================
-- What it answers: what ran against REVENUE_ANALYTICS recently, how long it
-- took, and whether anything errored -- the closest thing to "did last
-- night's build actually work" without opening the dbt log.
-- If the answer looks wrong: an unexpected error_message on a dbt-run query
-- -> check transform/target/run_results.json for the same failure recorded
-- from the dbt side, then cross-reference query_id here for the exact
-- Snowflake-side error text dbt's own artifact does not capture.
select
    query_id,
    query_type,
    user_name,
    role_name,
    warehouse_name,
    execution_status,
    error_code,
    error_message,
    start_time,
    total_elapsed_time / 1000.0 as elapsed_seconds
from snowflake.account_usage.query_history
where database_name = 'REVENUE_ANALYTICS'
  and start_time >= dateadd('day', -7, current_timestamp())
order by start_time desc
limit 200;

-- Quick failure-only view of the same window.
select
    query_id,
    query_type,
    error_code,
    error_message,
    start_time
from snowflake.account_usage.query_history
where database_name = 'REVENUE_ANALYTICS'
  and execution_status = 'FAIL'
  and start_time >= dateadd('day', -7, current_timestamp())
order by start_time desc;

-- =======================================================================
-- 5. Table growth over time
-- =======================================================================
-- What it answers: is any table growing faster than the daily batch (a few
-- hundred rows) would predict -- a sign of a duplicate load, a missing
-- truncate/full-refresh, or a runaway rebuild.
-- If the answer looks wrong: active_bytes growing daily well beyond what a
-- full-refresh table should hold -> check that models materialize as
-- `table` (full replace, per transform/dbt_project.yml), not accidentally
-- `incremental`, and that the loader isn't being invoked more than once.
select
    table_schema,
    table_name,
    active_bytes,
    time_travel_bytes,
    failsafe_bytes,
    row_count,
    last_altered
from snowflake.account_usage.table_storage_metrics
where table_catalog = 'REVENUE_ANALYTICS'
  and deleted is null
order by table_schema, table_name;
