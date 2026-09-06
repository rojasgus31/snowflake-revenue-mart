-- Deploy the revenue dashboard natively inside Snowflake as a Streamlit in
-- Snowflake app, reading the live `analytics` mart with no data copy.
--
-- Run the CREATE STAGE / CREATE STREAMLIT / GRANT steps below in a worksheet.
-- The PUT step in between does NOT run in a worksheet -- worksheets cannot
-- read local files -- it runs from the SnowSQL or Snowflake CLI on the
-- machine that has app/streamlit_app.py and app/data_source.py checked out.
--
-- Placeholders throughout (<...>) stand in for real values; none of this has
-- been run against a live account (see snowflake/STREAMLIT.md and
-- snowflake/README.md for why).

-- 1. Run as REPORTER. This dashboard only ever reads the published mart, and
--    REPORTER is the role 01_bootstrap.sql grants select on the ANALYTICS
--    schema only -- no RAW, no STAGING. Reuse its own warehouse so the
--    dashboard's query cost is billed and capacity-planned separately from
--    ingestion and transformation.
use role reporter;
use warehouse wh_transform_xs;
use database revenue_analytics;

-- 2. A stage to hold the two application files. Server-side encryption is
--    the default for an internal stage and is sufficient here -- nothing in
--    app/streamlit_app.py or app/data_source.py is sensitive, all it holds is
--    code.
create stage if not exists revenue_analytics.analytics.streamlit_app_stage
    directory = (enable = true);

-- 3. Upload the app files from the CLI (NOT from this worksheet):
--
--      snow sql -q "
--        put file://app/streamlit_app.py
--            @revenue_analytics.analytics.streamlit_app_stage
--            auto_compress=false overwrite=true;
--        put file://app/data_source.py
--            @revenue_analytics.analytics.streamlit_app_stage
--            auto_compress=false overwrite=true;
--      " --role REPORTER --warehouse WH_TRANSFORM_XS
--
--    (or the equivalent PUT commands in SnowSQL). Re-run this after every
--    change to either file, then re-run the CREATE STREAMLIT below so the app
--    picks up the new stage contents.

-- 4. Register the app. MAIN_FILE is the entry point Streamlit in Snowflake
--    launches; QUERY_WAREHOUSE is the warehouse every query the app issues
--    runs on -- separate from any warehouse a viewer's own session might use,
--    so the dashboard's cost is attributable and capped by that warehouse's
--    own auto-suspend.
create streamlit if not exists revenue_analytics.analytics.revenue_performance_app
    root_location = '@revenue_analytics.analytics.streamlit_app_stage'
    main_file = 'streamlit_app.py'
    query_warehouse = 'wh_transform_xs';

-- 5. Grant viewing to whichever role should see the dashboard -- REPORTER
--    itself, or a narrower role scoped to just this app if the account has
--    more report consumers than warehouse users. Replace <VIEWER_ROLE> with
--    that role.
grant usage on streamlit revenue_analytics.analytics.revenue_performance_app
    to role <VIEWER_ROLE>;

-- 6. Find the app in Snowsight under Projects > Streamlit, or share this URL
--    pattern with viewers who hold <VIEWER_ROLE>:
--      https://<account_locator>.snowflakecomputing.com/streamlit-apps/REVENUE_ANALYTICS.ANALYTICS.REVENUE_PERFORMANCE_APP
