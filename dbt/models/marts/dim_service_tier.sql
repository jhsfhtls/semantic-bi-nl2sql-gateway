select
    service_tier_id,
    service_tier_name,
    priority_level,
    sla_target_hours,
    support_window
from {{ ref('stg_service_tiers') }}
