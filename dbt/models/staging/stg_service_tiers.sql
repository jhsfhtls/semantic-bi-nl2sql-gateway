select
    cast(service_tier_id as varchar) as service_tier_id,
    cast(service_tier_name as varchar) as service_tier_name,
    cast(priority_level as varchar) as priority_level,
    cast(sla_target_hours as decimal(10, 2)) as sla_target_hours,
    cast(support_window as varchar) as support_window
from {{ ref('raw_service_tiers') }}
