select
    cast(segment_id as varchar) as segment_id,
    cast(segment_name as varchar) as segment_name,
    cast(tier_group as varchar) as tier_group,
    cast(account_band as varchar) as account_band,
    cast(is_enterprise as boolean) as is_enterprise
from {{ ref('raw_customer_segments') }}
