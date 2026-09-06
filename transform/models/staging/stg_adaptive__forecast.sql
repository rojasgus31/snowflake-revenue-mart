-- Revenue plan at product x region x month. This is the grain the mart adopts,
-- because it is the coarsest of the two inputs and cannot be disaggregated
-- without inventing allocation weights.

select
    trim(forecast_id)  as forecast_id,
    trim(product_id)   as product_id,
    trim(region)       as region,
    cast(forecast_month as date) as forecast_month,
    cast(forecast_revenue as decimal(18, 2)) as forecast_revenue,
    cast(forecast_quantity as integer)       as forecast_quantity

from {{ source('adaptive', 'raw_adaptive_forecast') }}
