-- Type 1: current state only. The sources carry no change history, so a
-- snapshot over them would capture nothing. SCD2 is designed for in the spec
-- and becomes worth building at the first load with a changed account.
--
-- The synthetic row is the standard treatment for a late-arriving dimension
-- member: it preserves referential integrity from the fact table without
-- discarding the orphaned order's revenue (D2).

with accounts as (

    select * from {{ ref('stg_salesforce__accounts') }}

),

orphaned_customers as (

    select distinct customer_id
    from {{ ref('int_orders_enriched') }}
    where region = 'UNMAPPED'

)

select
    customer_id,
    account_id,
    account_name,
    region,
    segment,
    account_owner,
    is_active,
    false as is_synthetic
from accounts

union all

select
    customer_id,
    null            as account_id,
    'Unknown Customer (' || customer_id || ')' as account_name,
    'UNMAPPED'      as region,
    'Unknown'       as segment,
    null            as account_owner,
    false           as is_active,
    true            as is_synthetic
from orphaned_customers
