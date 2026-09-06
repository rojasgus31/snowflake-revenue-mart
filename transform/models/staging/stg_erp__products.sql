-- Product master. Sole source of standard_cost, and therefore the only input
-- to gross margin. D10: discontinued and pending products are kept, because
-- orders were placed against them.

select
    trim(product_id)        as product_id,
    trim(product_name)      as product_name,
    trim(product_family)    as product_family,
    trim(lifecycle_status)  as lifecycle_status,
    cast(standard_cost as decimal(18, 2)) as standard_cost,
    trim(lifecycle_status) = 'Active' as is_active_product

from {{ source('erp', 'raw_erp_products') }}
