-- What was excluded, why, and what it cost. Publishing the price of every
-- exclusion is what keeps quarantining honest: a silent rejects table is
-- indistinguishable from deleting the rows.

with rejects as (

    select * from {{ ref('stg_oracle__orders_rejects') }}

),

source_row_count as (

    select count(*) as total_rows from {{ source('oracle', 'raw_oracle_orders') }}

)

select
    rejects.dq_failure_reason,
    count(*) as rejected_row_count,
    cast(sum(rejects.gross_revenue) as decimal(18, 2)) as rejected_revenue,
    cast(count(*) * 1.0 / max(source_row_count.total_rows) as decimal(18, 4)) as pct_of_source_rows

from rejects
cross join source_row_count
group by rejects.dq_failure_reason
