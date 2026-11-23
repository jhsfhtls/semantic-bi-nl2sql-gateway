select
    ticket_id,
    created_date,
    region_id,
    segment_id,
    service_tier_id,
    resolution_hours,
    is_sla_met,
    is_breached,
    ticket_status
from {{ ref('stg_sla_tickets') }}
