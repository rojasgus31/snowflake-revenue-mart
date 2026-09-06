-- Monthly date spine covering the full calendar year, not just the months with
-- data. A month where nothing shipped must still appear in the report as a
-- zero, otherwise a total collapse looks identical to a missing extract.

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
