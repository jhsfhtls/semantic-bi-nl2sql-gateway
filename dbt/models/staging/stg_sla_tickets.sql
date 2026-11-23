select
    cast(ticket_id as varchar) as ticket_id,
    cast(created_date as date) as created_date,
    cast(region_id as varchar) as region_id,
    cast(segment_id as varchar) as segment_id,
    cast(service_tier_id as varchar) as service_tier_id,
    cast(resolution_hours as decimal(12, 4)) as resolution_hours,
    cast(is_sla_met as integer) as is_sla_met,
    cast(is_breached as integer) as is_breached,
    cast(ticket_status as varchar) as ticket_status
from {{ ref('raw_sla_tickets') }}
