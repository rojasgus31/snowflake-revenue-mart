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
