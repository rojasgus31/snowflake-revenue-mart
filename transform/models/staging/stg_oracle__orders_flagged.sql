{{ config(materialized='ephemeral') }}

-- Typecast the raw text and classify every row against the data quality rules
-- from the design (D1, D4, D5, D6, D9, D11). This model decides nothing about
-- what to do with a bad row -- it only names the problem. The keep and reject
-- models below branch on that single verdict, so the rules never drift apart.

with source as (

    select * from {{ source('oracle', 'raw_oracle_orders') }}

),

typed as (

    select
        trim(order_id)      as order_id,
        trim(customer_id)   as customer_id,
        trim(product_id)    as product_id,

        try_cast(nullif(trim(order_date), '') as date)              as order_date,
        try_cast(nullif(trim(promised_delivery_date), '') as date)  as promised_delivery_date,
        try_cast(nullif(trim(actual_delivery_date), '') as date)    as actual_delivery_date,

        try_cast(nullif(trim(quantity), '') as integer)             as quantity,
        try_cast(nullif(trim(unit_price), '') as decimal(18, 2))    as unit_price,

        -- D11: plant_location arrives as "City, Country" in one column,
        -- which blocks any grouping by country.
        trim(split_part(plant_location, ',', 1)) as plant_city,
        trim(split_part(plant_location, ',', 2)) as plant_country,

        trim(order_status) as order_status,

        -- D9: two extracts are present. ORD9xxxx is a later batch, not
        -- corruption. Tagging it lets an analyst spot extract-level skew.
        case
            when trim(order_id) like 'ORD9%' then 'BATCH_B'
            else 'BATCH_A'
        end as source_batch

    from source

),

with_duplicate_count as (

    select
        *,
        count(*) over (partition by order_id) as order_id_occurrences
    from typed

),

classified as (

    select
        order_id,
        customer_id,
        product_id,
        order_date,
        promised_delivery_date,
        actual_delivery_date,
        quantity,
        unit_price,
        cast(quantity * unit_price as decimal(18, 2)) as gross_revenue,
        plant_city,
        plant_country,
        order_status,
        source_batch,

        -- Order matters: a duplicated key is reported as such even when the
        -- row also has a missing field, because the duplicate is the defect
        -- that has to be fixed upstream.
        case
            when order_id_occurrences > 1               then 'AMBIGUOUS_DUPLICATE'
            when order_date is null                     then 'MISSING_ORDER_DATE'
            when unit_price is null                     then 'MISSING_UNIT_PRICE'
            when quantity is null or quantity <= 0      then 'INVALID_QUANTITY'
        end as dq_failure_reason

    from with_duplicate_count

)

select * from classified
