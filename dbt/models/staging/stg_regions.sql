select
    cast(region_id as varchar) as region_id,
    cast(region_name as varchar) as region_name,
    cast(uf_code as varchar) as uf_code,
    cast(macro_region as varchar) as macro_region,
    cast(country_code as varchar) as country_code,
    cast(currency_code as varchar) as currency_code
from {{ ref('raw_regions') }}
