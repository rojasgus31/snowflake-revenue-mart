-- Rows excluded from analytics, retained with the reason. Quarantining rather
-- than deleting is what lets the reconciliation test in Task 11 prove that no
-- revenue disappeared, and gives the source-system owner an actionable list.

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
    source_batch,
    dq_failure_reason

from {{ ref('stg_oracle__orders_flagged') }}
where dq_failure_reason is not null
