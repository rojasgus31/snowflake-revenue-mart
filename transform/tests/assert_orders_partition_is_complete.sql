-- Clean and rejected rows must partition the source exactly: no row invented,
-- no row silently dropped. This is the guarantee that makes the rejects table
-- meaningful rather than decorative.

with counts as (
    select
        (select count(*) from {{ source('oracle', 'raw_oracle_orders') }}) as source_rows,
        (select count(*) from {{ ref('stg_oracle__orders') }}) as clean_rows,
        (select count(*) from {{ ref('stg_oracle__orders_rejects') }}) as rejected_rows
)

select *
from counts
where source_rows <> clean_rows + rejected_rows
