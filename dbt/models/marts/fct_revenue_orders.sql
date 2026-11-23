select
    order_id,
    order_date,
    region_id,
    segment_id,
    service_tier_id,
    gross_revenue_brl,
    discount_brl,
    net_revenue_brl,
    direct_cost_brl,
    gross_margin_brl,
    target_revenue_brl,
    order_status
from {{ ref('stg_revenue_orders') }}
