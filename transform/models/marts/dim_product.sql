-- Same late-arriving-member treatment as dim_customer. standard_cost stays
-- NULL for the synthetic row so margin remains honestly unknown (D3) rather
-- than silently zero.

with products as (

    select * from {{ ref('stg_erp__products') }}

),

orphaned_products as (

    select distinct product_id
    from {{ ref('int_orders_enriched') }}
    where has_standard_cost = false

)

select
    product_id,
    product_name,
    product_family,
    lifecycle_status,
    standard_cost,
    is_active_product,
    false as is_synthetic
from products

union all

select
    product_id,
    'Unknown Product (' || product_id || ')' as product_name,
    'Unknown'  as product_family,
    'Unknown'  as lifecycle_status,
    null       as standard_cost,
    false      as is_active_product,
    true       as is_synthetic
from orphaned_products
