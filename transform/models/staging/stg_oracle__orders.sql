-- Orders that passed every data quality rule. This is the only orders model
-- any downstream layer reads.

select
    order_id,
    customer_id,
    product_id,
    order_date,
    promised_delivery_date,
    actual_delivery_date,
    quantity,
    unit_price,
    gross_revenue,
    plant_city,
    plant_country,
    order_status,
    source_batch

from {{ ref('stg_oracle__orders_flagged') }}
where dq_failure_reason is null
