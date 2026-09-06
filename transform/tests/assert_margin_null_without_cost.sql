-- D3: PROD999 is absent from the product master, so its standard cost is
-- unknown. Revenue is knowable, margin is not. Defaulting the cost to zero
-- would report a 100% margin -- a plausible-looking lie. Margin must be NULL,
-- and revenue must survive.

select 'margin was fabricated for a product with no known cost' as failure
from {{ ref('int_orders_enriched') }}
where has_standard_cost = false and gross_margin is not null

union all

select 'revenue was dropped for a product with no known cost' as failure
from {{ ref('int_orders_enriched') }}
where product_id = 'PROD999' and gross_revenue is null
