-- Customer accounts. Sole source of region, which the forecast join depends on.
-- D10: inactive accounts are kept. An order placed while an account was active
-- is still real historical revenue; filtering belongs in the BI layer.

select
    trim(customer_id)   as customer_id,
    trim(account_id)    as account_id,
    trim(account_name)  as account_name,
    trim(region)        as region,
    trim(account_owner) as account_owner,
    trim(segment)       as segment,
    upper(trim(active_flag)) = 'TRUE' as is_active

from {{ source('salesforce', 'raw_salesforce_accounts') }}
