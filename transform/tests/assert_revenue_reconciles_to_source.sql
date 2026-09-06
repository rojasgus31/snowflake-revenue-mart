-- Every computable source dollar lands in exactly one bucket:
--
--   source total = recognised (in the mart)
--                + open       (pipeline, R1)
--                + cancelled  (excluded, R1)
--                + rejected   (quarantined, D1/D4/D5/D6)
--
-- Both sides apply the identical filter -- quantity and unit_price both
-- non-null -- so a row whose revenue cannot be computed is absent from both
-- and cannot mask a leak. If this test fails, revenue is disappearing
-- somewhere in the pipeline and no number in the mart can be trusted.

with source_total as (

    select coalesce(sum(
        try_cast(nullif(trim(quantity), '') as decimal(18, 2))
        * try_cast(nullif(trim(unit_price), '') as decimal(18, 2))
    ), 0) as amount
    from {{ source('oracle', 'raw_oracle_orders') }}
    where try_cast(nullif(trim(quantity), '') as decimal(18, 2)) is not null
      and try_cast(nullif(trim(unit_price), '') as decimal(18, 2)) is not null

),

recognised as (

    select coalesce(sum(actual_revenue), 0) as amount
    from {{ ref('mart_revenue_performance') }}

),

open_and_cancelled as (

    select coalesce(sum(gross_revenue), 0) as amount
    from {{ ref('fct_orders') }}
    where not is_recognised_revenue

),

rejected as (

    select coalesce(sum(gross_revenue), 0) as amount
    from {{ ref('stg_oracle__orders_rejects') }}
    where quantity is not null and unit_price is not null

),

reconciliation as (

    select
        (select amount from source_total)  as source_amount,
        (select amount from recognised)
            + (select amount from open_and_cancelled)
            + (select amount from rejected) as accounted_amount

)

select
    source_amount,
    accounted_amount,
    source_amount - accounted_amount as unexplained_difference
from reconciliation
-- One cent of tolerance for decimal rounding across the aggregation steps.
where abs(source_amount - accounted_amount) > 0.01
