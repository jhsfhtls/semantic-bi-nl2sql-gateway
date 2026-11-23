select
    cast(order_id as varchar) as order_id,
    cast(order_date as date) as order_date,
    cast(region_id as varchar) as region_id,
    cast(segment_id as varchar) as segment_id,
    cast(service_tier_id as varchar) as service_tier_id,
    cast(gross_revenue_brl as decimal(18, 4)) as gross_revenue_brl,
    cast(discount_brl as decimal(18, 4)) as discount_brl,
    cast(net_revenue_brl as decimal(18, 4)) as net_revenue_brl,
    cast(direct_cost_brl as decimal(18, 4)) as direct_cost_brl,
    cast(gross_margin_brl as decimal(18, 4)) as gross_margin_brl,
    cast(target_revenue_brl as decimal(18, 4)) as target_revenue_brl,
    cast(order_status as varchar) as order_status
from {{ ref('raw_revenue_orders') }}
