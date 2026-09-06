-- Run once as ACCOUNTADMIN on a fresh Snowflake trial.
--
-- Three roles matching the three things that touch the warehouse: the loader
-- writes RAW and nothing else, the transformer reads RAW and owns everything
-- downstream, the reporter reads ANALYTICS only. Separate warehouses so a
-- heavy transform cannot starve a dashboard query, and so the two workloads
-- bill separately.

use role accountadmin;

create database if not exists revenue_analytics;

create schema if not exists revenue_analytics.raw;
create schema if not exists revenue_analytics.staging;
create schema if not exists revenue_analytics.analytics;

-- Deliberately no `create table` statements for the RAW schema here. The four
-- raw tables (raw_oracle_orders, raw_salesforce_accounts, raw_adaptive_forecast,
-- raw_erp_products) are created by the loader itself --
-- `uv run python ingest/load_raw.py --target snowflake` -- which uses
-- write_pandas(auto_create_table=True) so the table shape always matches what
-- was actually loaded. Bootstrap only needs to exist so the LOADER role has
-- somewhere to write; run the loader before the first dbt build, or every
-- source() in transform/models/staging/_sources.yml will fail to resolve.

-- Auto-suspend at the 60-second floor: on a trial, an idle warehouse is the
-- single largest source of wasted credits.
create warehouse if not exists wh_load_xs
    warehouse_size = xsmall
    auto_suspend = 60
    auto_resume = true
    initially_suspended = true;

create warehouse if not exists wh_transform_xs
    warehouse_size = xsmall
    auto_suspend = 60
    auto_resume = true
    initially_suspended = true;

create role if not exists loader;
create role if not exists transformer;
create role if not exists reporter;

grant usage on warehouse wh_load_xs to role loader;
grant usage on warehouse wh_transform_xs to role transformer;
grant usage on warehouse wh_transform_xs to role reporter;

grant usage on database revenue_analytics to role loader;
grant usage on database revenue_analytics to role transformer;
grant usage on database revenue_analytics to role reporter;

-- LOADER: writes RAW, and can read nothing else.
grant usage, create table on schema revenue_analytics.raw to role loader;

-- TRANSFORMER: reads RAW, owns STAGING and ANALYTICS.
grant usage on schema revenue_analytics.raw to role transformer;
grant select on all tables in schema revenue_analytics.raw to role transformer;
grant select on future tables in schema revenue_analytics.raw to role transformer;
grant all on schema revenue_analytics.staging to role transformer;
grant all on schema revenue_analytics.analytics to role transformer;

-- REPORTER: reads the published marts, and cannot see staging or raw.
grant usage on schema revenue_analytics.analytics to role reporter;
grant select on all tables in schema revenue_analytics.analytics to role reporter;
grant select on future tables in schema revenue_analytics.analytics to role reporter;

-- Replace <YOUR_USER> with the trial account's username.
grant role loader to user <YOUR_USER>;
grant role transformer to user <YOUR_USER>;
grant role reporter to user <YOUR_USER>;
