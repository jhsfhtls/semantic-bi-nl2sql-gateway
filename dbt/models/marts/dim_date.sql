with date_spine as (
    select cast(gs as date) as date_day
    from generate_series(date '2025-01-01', date '2026-12-31', interval 1 day) as t(gs)
)
select
    date_day,
    cast(extract(year from date_day) as integer) as year_num,
    cast(extract(quarter from date_day) as integer) as quarter_num,
    concat(
        cast(extract(year from date_day) as varchar),
        '-Q',
        cast(extract(quarter from date_day) as varchar)
    ) as quarter_label,
    cast(extract(month from date_day) as integer) as month_num,
    strftime(date_day, '%B') as month_name,
    strftime(date_day, '%Y-%m') as year_month,
    extract(isodow from date_day) in (6, 7) as is_weekend,
    cast(extract(year from date_day) as integer) as fiscal_year
from date_spine
