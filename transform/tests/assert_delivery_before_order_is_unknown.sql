-- D8: one row in this extract (ORD90105) shows an actual delivery date
-- earlier than its own order_date -- a physical impossibility, since nothing
-- can be delivered before it was ordered. transform/macros/delivery_status.sql
-- treats this as untrustworthy dates rather than untrustworthy revenue: the
-- row is retained (the sale still happened) but its delivery_status degrades
-- to 'Unknown' rather than being computed as if the dates were sound.
--
-- Nothing else in the test suite pins this down, and the same reordering
-- risk as D7 applies here: the WHEN actual_column < order_date_column arm
-- must be checked before the On Time / Late comparisons, or a row like this
-- would silently report a fabricated On Time or Late status built from
-- nonsensical dates.
--
-- This asserts both directions:
--   1. no row with actual_delivery_date < order_date is classified as
--      anything other than 'Unknown' (the rule is never silently bypassed);
--   2. exactly one such row exists in the current source, so this test does
--      not pass vacuously if the underlying data changes shape -- if that
--      count ever moves, this test should be revisited alongside it rather
--      than passing by accident.

with backdated as (

    select
        order_id,
        order_date,
        actual_delivery_date,
        delivery_status
    from {{ ref('int_orders_enriched') }}
    where actual_delivery_date < order_date

),

wrongly_classified as (

    select * from backdated
    where delivery_status <> 'Unknown'

),

unexpected_row_count as (

    select * from backdated
    where (select count(*) from backdated) <> 1

)

select order_id, order_date, actual_delivery_date, delivery_status, 'wrong status' as failure_reason
from wrongly_classified

union all

select order_id, order_date, actual_delivery_date, delivery_status, 'unexpected row count' as failure_reason
from unexpected_row_count
