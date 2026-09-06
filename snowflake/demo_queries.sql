-- A guided walk through the revenue performance mart, in the order that tells
-- the story. Paste into a Snowsight worksheet and run one block at a time.
--
-- Every query below is read-only.

use role transformer;
use warehouse wh_transform_xs;
use database revenue_analytics;


-- 1. The deliverable ------------------------------------------------------
-- Actual against plan at product x region x month. One row per combination
-- either side of the join contributed.

select *
from analytics.mart_revenue_performance
order by revenue_variance
limit 20;


-- 2. Where the money went, by region --------------------------------------
-- Note the UNMAPPED row: real revenue from a customer who has no Salesforce
-- account, and therefore no region and no forecast that could explain it. It
-- is retained and visible rather than quietly dropped.

select
    region,
    count(*)                    as mart_rows,
    round(sum(actual_revenue))  as actual,
    round(sum(forecast_revenue)) as forecast,
    round(sum(revenue_variance)) as variance
from analytics.mart_revenue_performance
group by region
order by variance;


-- 3. Why the variance is negative everywhere ------------------------------
-- Only shipped orders count as revenue. The forecast was made before
-- cancellations were known, so it still counts demand that never shipped.
-- That gap is real business variance, not a data defect.

select
    variance_flag,
    count(*)                     as rows_,
    round(sum(actual_revenue))   as actual,
    round(sum(forecast_revenue)) as forecast
from analytics.mart_revenue_performance
group by variance_flag
order by variance_flag;


-- 4. What was excluded, and what it cost ----------------------------------
-- Nothing is deleted. Rows that cannot be trusted are quarantined with a
-- reason and the dollar value of the exclusion is published.
--
-- MISSING_UNIT_PRICE has a NULL value because that row's revenue cannot be
-- computed at all -- which is precisely why it was rejected.
-- INVALID_QUANTITY is negative because one of its two rows has quantity -15.

select * from analytics.dq_reject_summary
order by rejected_row_count desc;


-- 5. The six quarantined rows themselves ----------------------------------
-- ORD00005 appears twice with conflicting quantities and no timestamp to say
-- which is current. Both copies are rejected: choosing either would fabricate
-- a fact. The real fix is change-data-capture metadata in the source.

select order_id, dq_failure_reason, order_status, quantity, unit_price, order_date
from staging.stg_oracle__orders_rejects
order by dq_failure_reason, order_id;


-- 6. Kept, but honestly degraded ------------------------------------------
-- Three rows where the revenue is knowable but something else is not. Each
-- keeps its revenue and loses only the attribute that is genuinely unknown.
--   UNMAPPED       customer absent from Salesforce, so no region
--   NULL margin    product absent from the master, so no standard cost
--                  (zero cost would report a 100% margin, a plausible lie)
--   Unknown        delivered before it was ordered, so the dates are suspect

select order_id, customer_id, product_id, region,
       has_standard_cost, gross_margin, delivery_status
from analytics.fct_orders
where region = 'UNMAPPED'
   or not has_standard_cost
   or delivery_status = 'Unknown'
order by order_id;


-- 7. The guarantee --------------------------------------------------------
-- Every source dollar lands in exactly one bucket. This is asserted by a dbt
-- test that fails the build if it ever stops balancing.

select
    round(recognised + open_cancelled + rejected, 2) as accounted_for,
    round(recognised, 2)                             as recognised,
    round(open_cancelled, 2)                         as open_cancelled,
    round(rejected, 2)                               as rejected
from (
    select
        (select sum(actual_revenue)
           from analytics.mart_revenue_performance)                    as recognised,
        (select sum(gross_revenue)
           from analytics.fct_orders
          where not is_recognised_revenue)                             as open_cancelled,
        (select sum(gross_revenue)
           from staging.stg_oracle__orders_rejects
          where quantity is not null and unit_price is not null)       as rejected
);


-- 8. The role split, and why it only holds with secondary roles off -------
-- LOADER writes RAW and nothing else. TRANSFORMER reads RAW and owns
-- everything downstream. REPORTER reads only the published marts -- but
-- granting that role to a human user is not enough to enforce it, because
-- Snowflake keeps every other role that user holds active as a secondary
-- role by default. This block switches to REPORTER and shows the boundary
-- failing to hold, then makes it hold. See
-- docs/evidence/reporter_role_verification.md for the full write-up.

use role reporter;

-- Whatever else this session's user holds keeps riding along here. On the
-- account this was verified against, that showed ACCOUNTADMIN, TRANSFORMER,
-- ORGADMIN and LOADER all still active -- none of them REPORTER's own grant.
select current_role(), current_secondary_roles();

-- This should be refused by REPORTER's own grants (SELECT on ANALYTICS
-- only), and yet it succeeds: the secondary roles above are doing the work,
-- not REPORTER.
select count(*) from revenue_analytics.raw.raw_oracle_orders;

-- Drop every role but the primary one for the rest of this session. This is
-- the step a human user has to remember and a BI service account should
-- never need, because it should be created with
-- default_secondary_roles = () in the first place (see the service-account
-- section at the end of snowflake/01_bootstrap.sql).
use secondary roles none;

-- Now REPORTER's own grants are the only thing left, and the mart is
-- exactly what they allow.
select count(*) from revenue_analytics.analytics.mart_revenue_performance;

-- This is expected to fail with "Schema 'REVENUE_ANALYTICS.RAW' does not
-- exist or not authorized." That error is the demonstration working, not a
-- problem to fix -- it is REPORTER's real boundary, finally being enforced.
select count(*) from revenue_analytics.raw.raw_oracle_orders;
