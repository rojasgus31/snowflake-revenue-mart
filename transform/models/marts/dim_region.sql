-- UNMAPPED is a member of this dimension, not an absence. is_forecastable
-- records why it can never match a forecast row (D2).

select
    region_name,
    cast(is_forecastable as boolean) as is_forecastable

from {{ ref('seed_region') }}
