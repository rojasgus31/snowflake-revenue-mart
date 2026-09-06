-- Argument contract (positional, unvalidated):
--   1. order_status_column: the order's status column (e.g. Cancelled/Delivered/etc.)
--   2. actual_column:       the actual delivery date column
--   3. promised_column:     the promised/expected delivery date column
--   4. order_date_column:   the date the order was placed
-- These four arguments are passed positionally and are not validated by this
-- macro. Jinja has no way to check at compile time that the caller passed the
-- right column in the right slot. If a future call site transposes
-- actual_column and promised_column, or transposes either of them against
-- order_date_column, the macro will not raise an error: it will silently
-- compute the wrong delivery status for every row. If you add a new call
-- site, verify the resulting delivery_status distribution (counts per status)
-- looks sane before trusting it.
{% macro delivery_status(order_status_column, actual_column, promised_column, order_date_column) %}
-- Business rule R4, evaluated in order of precedence.
--   D7: every Cancelled order in this extract carries a delivery date, which
--       is a source-system contradiction. Status wins; the date is ignored.
--   D8: one row is delivered before it was ordered. The dates are untrustworthy
--       but the revenue is not, so the status degrades to Unknown and the row
--       is retained.
case
    when {{ order_status_column }} = 'Cancelled'              then 'Cancelled'
    when {{ actual_column }} is null                          then 'Not Delivered'
    when {{ actual_column }} < {{ order_date_column }}        then 'Unknown'
    when {{ actual_column }} <= {{ promised_column }}         then 'On Time'
    else 'Late'
end
{% endmacro %}
