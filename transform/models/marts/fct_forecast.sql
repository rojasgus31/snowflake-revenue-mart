-- Plan grain: product x region x month. Deliberately not disaggregated to
-- order level -- the source carries no basis for allocating a monthly regional
-- number across individual orders, and inventing one would manufacture
-- precision the business does not have.

select
    forecast_id,
    product_id,
    region,
    forecast_month,
    forecast_revenue,
    forecast_quantity

from {{ ref('stg_adaptive__forecast') }}
