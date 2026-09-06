-- The deliverable. Actual against plan at the coarsest grain both inputs share.
--
-- FULL OUTER is the whole design. Forecast rows with no orders are the report's
-- primary signal, and actuals with no forecast (UNMAPPED customers, unforecast
-- products) must stay visible rather than vanish into a join. Both sides
-- survive; the coalesced key columns below are what make that work.

with actuals as (

    select * from {{ ref('int_order_revenue_monthly') }}

),

forecast as (

    select * from {{ ref('fct_forecast') }}

),

joined as (

    select
        coalesce(actuals.product_id, forecast.product_id)       as product_id,
        coalesce(actuals.region, forecast.region)               as region,
        coalesce(actuals.revenue_month, forecast.forecast_month) as revenue_month,

        coalesce(actuals.actual_revenue, 0)  as actual_revenue,
        coalesce(actuals.actual_quantity, 0) as actual_quantity,
        coalesce(actuals.actual_margin, 0)   as actual_margin,

        forecast.forecast_revenue,
        forecast.forecast_quantity,

        coalesce(actuals.order_count, 0)             as order_count,
        coalesce(actuals.on_time_count, 0)           as on_time_count,
        coalesce(actuals.late_count, 0)              as late_count,
        coalesce(actuals.cancelled_count, 0)         as cancelled_count,
        coalesce(actuals.orders_with_known_cost, 0)  as orders_with_known_cost

    from actuals
    full outer join forecast
        on  actuals.product_id    = forecast.product_id
        and actuals.region        = forecast.region
        and actuals.revenue_month = forecast.forecast_month

),

measured as (

    select
        product_id,
        region,
        revenue_month,

        actual_revenue,
        actual_quantity,
        forecast_revenue,
        forecast_quantity,

        -- For a NO_FORECAST row the variance equals the full actual revenue --
        -- nothing was planned, so all of it is unplanned.
        cast(actual_revenue - coalesce(forecast_revenue, 0) as decimal(18, 2)) as revenue_variance,

        -- NULLIF guards the zero-forecast case, which is common: a product sold
        -- into a region nobody planned for divides by zero without it. The
        -- percentage is deliberately NULL in that case -- a percentage against
        -- a zero base is meaningless rather than infinite.
        cast(
            (actual_revenue - coalesce(forecast_revenue, 0))
            / nullif(forecast_revenue, 0)
            as decimal(18, 4)
        ) as revenue_variance_pct,

        actual_margin,

        -- A margin total built from partly-unknown costs (D3) must never be
        -- read as complete. This column is what stops that.
        case
            when order_count > 0
                then cast(orders_with_known_cost * 1.0 / order_count as decimal(18, 4))
        end as margin_coverage_pct,

        order_count,
        on_time_count,
        late_count,
        cancelled_count,

        -- Denominator is deliberately On Time + Late only -- Not Delivered,
        -- Cancelled and Unknown orders are excluded. This measures punctuality
        -- among orders that actually arrived, not fulfilment overall.
        case
            when (on_time_count + late_count) > 0
                then cast(on_time_count * 1.0 / (on_time_count + late_count) as decimal(18, 4))
        end as on_time_delivery_rate,

        -- A month whose orders were all cancelled reads BELOW_PLAN here, same
        -- as an ordinary shortfall -- actual_revenue is 0 either way. cancelled_count
        -- is the column that distinguishes that case from a genuine miss.
        case
            when forecast_revenue is null then 'NO_FORECAST'
            when order_count = 0          then 'NO_ACTUALS'
            when actual_revenue >= forecast_revenue then 'AT_OR_ABOVE_PLAN'
            else 'BELOW_PLAN'
        end as variance_flag

    from joined

)

select * from measured
