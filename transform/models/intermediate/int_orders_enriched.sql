-- Attach the two dimensions an order needs but does not carry: region (only
-- reachable through the customer) and standard cost (only in the product
-- master). Both joins are LEFT, because a failed lookup must degrade one
-- attribute rather than delete a row of real revenue.

with orders as (

    select * from {{ ref('stg_oracle__orders') }}

),

accounts as (

    select * from {{ ref('stg_salesforce__accounts') }}

),

products as (

    select * from {{ ref('stg_erp__products') }}

),

enriched as (

    select
        orders.order_id,
        orders.customer_id,
        orders.product_id,
        orders.order_date,
        orders.promised_delivery_date,
        orders.actual_delivery_date,
        orders.quantity,
        orders.unit_price,
        orders.gross_revenue,
        orders.plant_city,
        orders.plant_country,
        orders.order_status,
        orders.source_batch,

        -- D2: an order whose customer is absent from Salesforce has no region.
        -- UNMAPPED keeps the revenue visible and, because it matches no
        -- forecast row, surfaces it as unexplained variance.
        coalesce(accounts.region, 'UNMAPPED') as region,
        accounts.segment,
        accounts.account_name,
        accounts.is_active as is_active_account,

        products.product_family,
        products.lifecycle_status,
        products.standard_cost,
        products.product_id is not null as has_standard_cost,

        -- D3: NULL rather than zero when the cost is unknown.
        case
            when products.standard_cost is not null
                then cast((orders.unit_price - products.standard_cost) * orders.quantity as decimal(18, 2))
        end as gross_margin,

        {{ delivery_status('orders.order_status',
                           'orders.actual_delivery_date',
                           'orders.promised_delivery_date',
                           'orders.order_date') }} as delivery_status,

        -- R2: revenue is recognised on order_date, aligning actuals to the
        -- forecast's monthly grain.
        cast(date_trunc('month', orders.order_date) as date) as revenue_month,

        -- R1: only shipped orders are revenue. Open is pipeline; cancelled is
        -- excluded. The forecast still counted the cancelled demand, and that
        -- gap is genuine business variance rather than a defect to engineer away.
        orders.order_status = 'Shipped' as is_recognised_revenue

    from orders
    left join accounts on orders.customer_id = accounts.customer_id
    left join products on orders.product_id = products.product_id

)

select * from enriched
