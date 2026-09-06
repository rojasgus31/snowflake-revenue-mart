-- D2: CUST999 has no Salesforce account, so it has no region. Its revenue is
-- still real and must survive into the model tagged UNMAPPED, where it shows
-- up as variance no forecast explains. Dropping it would break the
-- reconciliation guarantee in Task 11.

select 'CUST999 revenue was dropped instead of tagged UNMAPPED' as failure
from (select 1 as probe) as p
where not exists (
    select 1
    from {{ ref('int_orders_enriched') }}
    where customer_id = 'CUST999' and region = 'UNMAPPED'
)

union all

select 'a mapped customer was wrongly tagged UNMAPPED' as failure
from {{ ref('int_orders_enriched') }}
where region = 'UNMAPPED' and customer_id <> 'CUST999'
