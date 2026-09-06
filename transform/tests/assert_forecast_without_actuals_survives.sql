-- The full outer join is load-bearing. An inner join would drop forecast rows
-- that received no orders -- which is precisely the variance a revenue
-- performance report exists to surface -- and would drop UNMAPPED actuals,
-- breaking the reconciliation guarantee. This test fails if either side of the
-- join is ever silently lost.

select 'forecast rows without actuals were dropped' as failure
from (select 1 as probe) as p
where (
    select count(*) from {{ ref('fct_forecast') }}
) > (
    select count(*) from {{ ref('mart_revenue_performance') }} where forecast_revenue is not null
)

union all

select 'UNMAPPED actuals were dropped by the join' as failure
from (select 1 as probe) as p
where not exists (
    select 1 from {{ ref('mart_revenue_performance') }} where region = 'UNMAPPED'
)
