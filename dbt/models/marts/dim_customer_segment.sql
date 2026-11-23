select
    segment_id,
    segment_name,
    tier_group,
    account_band,
    is_enterprise
from {{ ref('stg_customer_segments') }}
