select
    region_id,
    region_name,
    uf_code,
    macro_region,
    country_code,
    currency_code
from {{ ref('stg_regions') }}
