-- Order-line grain. This is the half of the grain conflict that the monthly
-- mart cannot represent: per-order delivery status, promise dates, and plant.
-- Analysts drill from the mart to here.

select
    order_id,
    customer_id,
    product_id,
    region,
    revenue_month,
    order_date,
    promised_delivery_date,
    actual_delivery_date,
    quantity,
    unit_price,
    gross_revenue,
    gross_margin,
    has_standard_cost,
    order_status,
    delivery_status,
    is_recognised_revenue,
    plant_city,
    plant_country,
    source_batch

from {{ ref('int_orders_enriched') }}
