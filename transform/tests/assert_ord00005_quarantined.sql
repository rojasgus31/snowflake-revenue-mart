-- D1: ORD00005 appears twice with conflicting quantity (31 vs 99) and no
-- column that could identify the later record. Both copies must be rejected;
-- keeping either one would fabricate a fact. Fails if any copy reached the
-- clean model, or if the rejects are labelled with the wrong reason.

select 'leaked into clean model' as failure, count(*) as row_count
from {{ ref('stg_oracle__orders') }}
where order_id = 'ORD00005'
having count(*) > 0

union all

select 'not quarantined as an ambiguous duplicate' as failure, count(*) as row_count
from {{ ref('stg_oracle__orders_rejects') }}
where order_id = 'ORD00005'
  and dq_failure_reason = 'AMBIGUOUS_DUPLICATE'
having count(*) <> 2
