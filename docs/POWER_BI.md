# Power BI Connection Guide

Power BI Desktop does not run on macOS, so this project ships a Streamlit
dashboard instead. The mart is nonetheless shaped as a conformed star
specifically so connecting Power BI is a configuration exercise rather than a
rewrite. This document is what that configuration looks like.

## Model layout

Import these five tables and set the relationships as follows:

| From | To | Cardinality | Direction |
|---|---|---|---|
| `mart_revenue_performance[product_id]` | `dim_product[product_id]` | many-to-one | single |
| `mart_revenue_performance[region]` | `dim_region[region_name]` | many-to-one | single |
| `mart_revenue_performance[revenue_month]` | `dim_date[date_month]` | many-to-one | single |
| `fct_orders[customer_id]` | `dim_customer[customer_id]` | many-to-one | single |

Mark `dim_date` as the date table on `date_month`. Leave every relationship
single-direction — bidirectional filtering across two fact tables at different
grains produces ambiguous paths and silently wrong totals.

`fct_orders` and `mart_revenue_performance` sit at different grains by design.
They share `dim_product`, `dim_region`, and `dim_date`, so a slicer on any of
those filters both correctly. Never join them to each other directly.

## Measures

```dax
Actual Revenue   = SUM ( mart_revenue_performance[actual_revenue] )
Forecast Revenue = SUM ( mart_revenue_performance[forecast_revenue] )
Revenue Variance = [Actual Revenue] - [Forecast Revenue]

Variance %  =
DIVIDE ( [Revenue Variance], [Forecast Revenue] )   -- DIVIDE, not "/", returns
                                                     -- BLANK on a zero forecast

Gross Margin = SUM ( mart_revenue_performance[actual_margin] )

-- Margin is unknown for products absent from the product master (defect D3).
-- Surface the coverage next to the total so a partial figure is never read as
-- complete.
Margin Coverage % =
DIVIDE (
    SUM ( mart_revenue_performance[orders_with_known_cost] ),
    SUM ( mart_revenue_performance[order_count] )
)

On Time Delivery % =
DIVIDE (
    SUM ( mart_revenue_performance[on_time_count] ),
    SUM ( mart_revenue_performance[on_time_count] )
        + SUM ( mart_revenue_performance[late_count] )
)
```

## Import against DirectQuery

Use **Import**. The mart is a few thousand rows at monthly grain; Import gives
better performance, full DAX support, and no load on the warehouse. DirectQuery
would leave an XSMALL warehouse resuming on every visual interaction, which
costs credits and adds latency for no benefit at this size.

Reconsider DirectQuery only above roughly 100 million rows, or when
sub-minute freshness is a stated requirement. At that point, configure
incremental refresh partitioned on `revenue_month` with a rolling window,
rather than switching the whole model.

## Row-level security

If regional managers should see only their own region, add an RLS role on
`dim_region`:

```dax
[region_name] = LOOKUPVALUE (
    user_region_mapping[region],
    user_region_mapping[email],
    USERPRINCIPALNAME ()
)
```

This requires a user-to-region mapping table, which the assessment dataset does
not include. In production it would be sourced from the same Salesforce extract
that supplies `account_owner`.
