-- D7: every Cancelled order in this extract carries a delivery date, which is
-- a source-system contradiction -- Oracle should never populate an actual
-- delivery date on an order it also marks Cancelled. transform/macros/
-- delivery_status.sql resolves that contradiction by making order_status win:
-- a Cancelled order is always reported as delivery_status = 'Cancelled',
-- regardless of what actual_delivery_date says.
--
-- Nothing else in the test suite pins this down. The CASE statement in
-- delivery_status.sql checks order_status = 'Cancelled' first specifically so
-- it short-circuits before the date comparisons -- reorder those WHEN arms and
-- a cancelled order with a delivery date would report 'On Time' or 'Late'
-- instead, and every other test in this project would still pass. This test
-- fails (returns rows) if any Cancelled order is reported as anything other
-- than delivery_status = 'Cancelled'.

select
    order_id,
    order_status,
    delivery_status,
    actual_delivery_date
from {{ ref('int_orders_enriched') }}
where order_status = 'Cancelled'
  and delivery_status <> 'Cancelled'
