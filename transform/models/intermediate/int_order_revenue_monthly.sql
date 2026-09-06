-- Roll recognised revenue up to the forecast's grain so the two can be
-- compared. Cancelled and open orders are excluded from the revenue measures
-- per R1 but still counted, because "the forecast assumed these would ship and
-- they did not" is exactly the story the mart exists to tell.

with enriched as (

    select * from {{ ref('int_orders_enriched') }}

),

aggregated as (

    select
        product_id,
        region,
        revenue_month,

        cast(sum(case when is_recognised_revenue then gross_revenue else 0 end)
             as decimal(18, 2)) as actual_revenue,

        sum(case when is_recognised_revenue then quantity else 0 end) as actual_quantity,

        -- NULL-safe by construction: a NULL margin (D3) contributes nothing to
        -- the sum, and orders_with_known_cost below reports the coverage so a
        -- partial total is never mistaken for a complete one.
        cast(sum(case when is_recognised_revenue then coalesce(gross_margin, 0) else 0 end)
             as decimal(18, 2)) as actual_margin,

        count(*) as order_count,
        sum(case when delivery_status = 'On Time' then 1 else 0 end)  as on_time_count,
        sum(case when delivery_status = 'Late' then 1 else 0 end)     as late_count,
        sum(case when order_status = 'Cancelled' then 1 else 0 end)   as cancelled_count,
        sum(case when has_standard_cost then 1 else 0 end)            as orders_with_known_cost

    from enriched
    group by product_id, region, revenue_month

)

select * from aggregated
