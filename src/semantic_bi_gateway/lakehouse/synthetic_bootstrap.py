"""Standalone deterministic synthetic DuckDB bootstrap from embedded dbt seed CSVs."""

from __future__ import annotations

from pathlib import Path

import duckdb


def materialize_synthetic_duckdb(repo_root: Path, target_db_path: Path) -> Path:
    """Build deterministic embedded DuckDB tables from dbt/seeds/*.csv without requiring dbt CLI."""
    resolved_root = repo_root.resolve()
    seeds_dir = resolved_root / "dbt" / "seeds"
    required_seeds = (
        "raw_regions.csv",
        "raw_customer_segments.csv",
        "raw_service_tiers.csv",
        "raw_revenue_orders.csv",
        "raw_sla_tickets.csv",
    )
    for seed_name in required_seeds:
        seed_path = seeds_dir / seed_name
        if not seed_path.is_file():
            raise FileNotFoundError(f"Embedded synthetic seed file missing: {seed_path}")

    resolved_target = target_db_path.resolve()
    resolved_target.parent.mkdir(parents=True, exist_ok=True)
    tmp_db_path = resolved_target.with_name(f"{resolved_target.stem}.tmp{resolved_target.suffix}")
    if tmp_db_path.exists():
        tmp_db_path.unlink()

    conn = duckdb.connect(str(tmp_db_path))
    try:
        conn.execute(
            """
            CREATE TABLE dim_date AS
            WITH date_spine AS (
                SELECT CAST(gs AS DATE) AS date_day
                FROM generate_series(DATE '2025-01-01', DATE '2026-12-31', INTERVAL 1 DAY) AS t(gs)
            )
            SELECT
                date_day,
                CAST(EXTRACT(YEAR FROM date_day) AS INTEGER) AS year_num,
                CAST(EXTRACT(QUARTER FROM date_day) AS INTEGER) AS quarter_num,
                CONCAT(
                    CAST(EXTRACT(YEAR FROM date_day) AS VARCHAR),
                    '-Q',
                    CAST(EXTRACT(QUARTER FROM date_day) AS VARCHAR)
                ) AS quarter_label,
                CAST(EXTRACT(MONTH FROM date_day) AS INTEGER) AS month_num,
                STRFTIME(date_day, '%B') AS month_name,
                STRFTIME(date_day, '%Y-%m') AS year_month,
                EXTRACT(ISODOW FROM date_day) IN (6, 7) AS is_weekend,
                CAST(EXTRACT(YEAR FROM date_day) AS INTEGER) AS fiscal_year
            FROM date_spine;
            """
        )

        regions_csv = (seeds_dir / "raw_regions.csv").as_posix()
        conn.execute(
            f"""
            CREATE TABLE dim_region AS
            SELECT
                CAST(region_id AS VARCHAR) AS region_id,
                CAST(region_name AS VARCHAR) AS region_name,
                CAST(uf_code AS VARCHAR) AS uf_code,
                CAST(macro_region AS VARCHAR) AS macro_region,
                CAST(country_code AS VARCHAR) AS country_code,
                CAST(currency_code AS VARCHAR) AS currency_code
            FROM read_csv('{regions_csv}', header=true, auto_detect=true);
            """
        )

        segments_csv = (seeds_dir / "raw_customer_segments.csv").as_posix()
        conn.execute(
            f"""
            CREATE TABLE dim_customer_segment AS
            SELECT
                CAST(segment_id AS VARCHAR) AS segment_id,
                CAST(segment_name AS VARCHAR) AS segment_name,
                CAST(tier_group AS VARCHAR) AS tier_group,
                CAST(account_band AS VARCHAR) AS account_band,
                CAST(is_enterprise AS BOOLEAN) AS is_enterprise
            FROM read_csv('{segments_csv}', header=true, auto_detect=true);
            """
        )

        tiers_csv = (seeds_dir / "raw_service_tiers.csv").as_posix()
        conn.execute(
            f"""
            CREATE TABLE dim_service_tier AS
            SELECT
                CAST(service_tier_id AS VARCHAR) AS service_tier_id,
                CAST(service_tier_name AS VARCHAR) AS service_tier_name,
                CAST(priority_level AS VARCHAR) AS priority_level,
                CAST(sla_target_hours AS DECIMAL(10, 2)) AS sla_target_hours,
                CAST(support_window AS VARCHAR) AS support_window
            FROM read_csv('{tiers_csv}', header=true, auto_detect=true);
            """
        )

        orders_csv = (seeds_dir / "raw_revenue_orders.csv").as_posix()
        conn.execute(
            f"""
            CREATE TABLE fct_revenue_orders AS
            SELECT
                CAST(order_id AS VARCHAR) AS order_id,
                CAST(order_date AS DATE) AS order_date,
                CAST(region_id AS VARCHAR) AS region_id,
                CAST(segment_id AS VARCHAR) AS segment_id,
                CAST(service_tier_id AS VARCHAR) AS service_tier_id,
                CAST(gross_revenue_brl AS DECIMAL(18, 4)) AS gross_revenue_brl,
                CAST(discount_brl AS DECIMAL(18, 4)) AS discount_brl,
                CAST(net_revenue_brl AS DECIMAL(18, 4)) AS net_revenue_brl,
                CAST(direct_cost_brl AS DECIMAL(18, 4)) AS direct_cost_brl,
                CAST(gross_margin_brl AS DECIMAL(18, 4)) AS gross_margin_brl,
                CAST(target_revenue_brl AS DECIMAL(18, 4)) AS target_revenue_brl,
                CAST(order_status AS VARCHAR) AS order_status
            FROM read_csv('{orders_csv}', header=true, auto_detect=true);
            """
        )

        tickets_csv = (seeds_dir / "raw_sla_tickets.csv").as_posix()
        conn.execute(
            f"""
            CREATE TABLE fct_sla_tickets AS
            SELECT
                CAST(ticket_id AS VARCHAR) AS ticket_id,
                CAST(created_date AS DATE) AS created_date,
                CAST(region_id AS VARCHAR) AS region_id,
                CAST(segment_id AS VARCHAR) AS segment_id,
                CAST(service_tier_id AS VARCHAR) AS service_tier_id,
                CAST(resolution_hours AS DECIMAL(12, 4)) AS resolution_hours,
                CAST(is_sla_met AS INTEGER) AS is_sla_met,
                CAST(is_breached AS INTEGER) AS is_breached,
                CAST(ticket_status AS VARCHAR) AS ticket_status
            FROM read_csv('{tickets_csv}', header=true, auto_detect=true);
            """
        )

        conn.execute("CHECKPOINT;")
    finally:
        conn.close()

    tmp_db_path.replace(resolved_target)
    return resolved_target
