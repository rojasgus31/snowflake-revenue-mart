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

-- Granting these three roles to a human user does not, by itself, enforce
-- the separation they describe. Snowflake defaults every user to
-- DEFAULT_SECONDARY_ROLES = ('ALL'), so a user who holds several roles keeps
-- all of them active as secondary roles even after `use role reporter`
-- narrows the primary one. A privilege check that should fail on the
-- reporter's own grants can instead pass through a broader role the same
-- user happens to also hold. The only way to test the boundary is
-- `use secondary roles none`, which drops every role but the primary for the
-- rest of the session. See docs/evidence/reporter_role_verification.md for a
-- live demonstration: RAW is readable as REPORTER with secondary roles on,
-- and refused the moment they are turned off, even though REPORTER's grants
-- never changed.

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

-- ---------------------------------------------------------------------
-- Service account for BI tools (Power BI, a scheduled job, anything that
-- is not a person exploring the account interactively)
-- ---------------------------------------------------------------------
--
-- A human user who also holds LOADER or TRANSFORMER (or, worse,
-- ACCOUNTADMIN) cannot be trusted to always remember `use secondary roles
-- none` before connecting a BI tool -- and the demonstration in
-- docs/evidence/reporter_role_verification.md shows exactly what happens
-- when that step is skipped: REPORTER's own grants are correct, but every
-- other role the user holds rides along as a secondary role and quietly
-- widens what the connection can read.
--
-- A service account sidesteps the problem instead of relying on the caller:
-- it holds REPORTER and nothing else, and DEFAULT_SECONDARY_ROLES = () means
-- there is no other role available to ride along in the first place. The
-- boundary holds unconditionally, without depending on anyone remembering to
-- narrow the session by hand. This is the load-bearing setting -- a service
-- account created without it would inherit DEFAULT_SECONDARY_ROLES = ('ALL')
-- like any other user, and grant nothing over the human-user pattern above.
--
-- Uncomment and replace <BI_SERVICE_ACCOUNT> with a real name to use. Set the
-- account's password or key pair separately, through the credential's own
-- channel -- it must never be written into this file or committed anywhere.
--
-- create user <BI_SERVICE_ACCOUNT>
--     default_role = reporter
--     default_secondary_roles = ();
--
-- grant role reporter to user <BI_SERVICE_ACCOUNT>;
