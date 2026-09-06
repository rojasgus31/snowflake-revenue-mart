-- A conformed monthly date dimension for the BI layer, spanning the full
-- 2025 calendar year rather than just the months that happen to have data.
-- mart_revenue_performance does not join this table -- its `revenue_month`
-- values come straight from the fact grain, so a month with genuinely zero
-- activity is simply absent from the mart rather than present as a zero row.
-- dim_date exists so a BI tool (see docs/POWER_BI.md) can mark it as the
-- model's date table and get correctly blank, rather than missing, periods
-- on any visual that filters or groups by month.

with months as (

    {{ dbt_utils.date_spine(
        datepart="month",
        start_date="cast('2025-01-01' as date)",
        end_date="cast('2026-01-01' as date)"
    ) }}

)

select
    cast(date_month as date)             as date_month,
    extract(year from date_month)        as year_number,
    extract(month from date_month)       as month_number,
    extract(quarter from date_month)     as quarter_number,
    cast(extract(year from date_month) as varchar)
        || '-' || lpad(cast(extract(month from date_month) as varchar), 2, '0') as year_month_label

from months
