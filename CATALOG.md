# Semantic Metrics, Power BI Templates & Benchmark Catalog

> **Single Source of Truth (SSOT):** Deterministically rendered from [`catalog/metrics.yaml`](catalog/metrics.yaml), [`catalog/dax_templates.yaml`](catalog/dax_templates.yaml), and [`catalog/benchmark_registry.yaml`](catalog/benchmark_registry.yaml). Do not edit manually.

- **Metrics Catalog Version:** `1.0.0` (Dialect: `duckdb`)
- **Semantic Models:** `6` (`2` facts, `4` dimensions)
- **Allowed Joins (`many_to_one` / `one_to_one`):** `8`
- **Certified KPIs:** `8` | **Blocked Sensitive Columns:** `10`
- **Power BI Templates:** `9` DAX/TMDL patterns + `3` Power Query M connectors
- **NL2SQL Benchmark Target:** `50` business questions + `20` adversarial security probes

---

## 1. Certified Semantic Models

| Model | Role | Grain | Primary Key | Time Dimension | Source Lakehouse |
| :--- | :---: | :--- | :--- | :--- | :--- |
| `dim_date` | `dimension` | One row per calendar day | `date_day` | `date_day` | `synthetic_embedded` |
| `dim_region` | `dimension` | One row per commercial region / Brazilian state (UF) | `region_id` | — | `public-data-gateway` |
| `dim_customer_segment` | `dimension` | One row per B2B customer commercial segment | `segment_id` | — | `finops-capex-revenue-lakehouse` |
| `dim_service_tier` | `dimension` | One row per operational support SLA tier | `service_tier_id` | — | `ops-sla-telemetry-lakehouse` |
| `fct_revenue_orders` | `fact` | One row per recognized B2B revenue order | `order_id` | `order_date` | `finops-capex-revenue-lakehouse` |
| `fct_sla_tickets` | `fact` | One row per operational service ticket | `ticket_id` | `created_date` | `ops-sla-telemetry-lakehouse` |

## 2. Certified KPI Metrics

| Metric | Label | Base Model | Type | Formula | Time Intel | Certified |
| :--- | :--- | :--- | :---: | :--- | :---: | :---: |
| `total_revenue` | Total Net Revenue (BRL) | `fct_revenue_orders` | `simple` | `SUM(net_revenue_brl)` | Yes | Yes |
| `total_gross_margin` | Total Gross Margin (BRL) | `fct_revenue_orders` | `simple` | `SUM(gross_margin_brl)` | Yes | Yes |
| `order_count` | Order Count | `fct_revenue_orders` | `simple` | `COUNT(DISTINCT order_id)` | Yes | Yes |
| `average_order_value` | Average Order Value (BRL) | `fct_revenue_orders` | `ratio` | `SUM(net_revenue_brl) / NULLIF(COUNT(DISTINCT order_id), 0)` | Yes | Yes |
| `gross_margin_pct` | Gross Margin (%) | `fct_revenue_orders` | `ratio` | `SUM(gross_margin_brl) / NULLIF(SUM(net_revenue_brl), 0)` | Yes | Yes |
| `ticket_count` | Service Ticket Count | `fct_sla_tickets` | `simple` | `COUNT(DISTINCT ticket_id)` | Yes | Yes |
| `sla_compliance_rate` | SLA Compliance Rate (%) | `fct_sla_tickets` | `ratio` | `SUM(is_sla_met) / NULLIF(COUNT(DISTINCT ticket_id), 0)` | Yes | Yes |
| `mttr_hours` | Mean Time To Resolution (Hours) | `fct_sla_tickets` | `ratio` | `SUM(resolution_hours) / NULLIF(COUNT(DISTINCT ticket_id), 0)` | Yes | Yes |

## 3. Allowed Join Graph (Fan-Out & Chasm-Trap Safe)

| Join Name | Left Model | Right Model | Join Keys | Cardinality | Type |
| :--- | :--- | :--- | :--- | :---: | :---: |
| `revenue_to_date` | `fct_revenue_orders` | `dim_date` | `order_date = date_day` | `many_to_one` | `left` |
| `revenue_to_region` | `fct_revenue_orders` | `dim_region` | `region_id = region_id` | `many_to_one` | `left` |
| `revenue_to_segment` | `fct_revenue_orders` | `dim_customer_segment` | `segment_id = segment_id` | `many_to_one` | `left` |
| `revenue_to_service_tier` | `fct_revenue_orders` | `dim_service_tier` | `service_tier_id = service_tier_id` | `many_to_one` | `left` |
| `sla_to_date` | `fct_sla_tickets` | `dim_date` | `created_date = date_day` | `many_to_one` | `left` |
| `sla_to_region` | `fct_sla_tickets` | `dim_region` | `region_id = region_id` | `many_to_one` | `left` |
| `sla_to_segment` | `fct_sla_tickets` | `dim_customer_segment` | `segment_id = segment_id` | `many_to_one` | `left` |
| `sla_to_service_tier` | `fct_sla_tickets` | `dim_service_tier` | `service_tier_id = service_tier_id` | `many_to_one` | `left` |

## 4. Power BI DAX/TMDL & Power Query M Templates

| Template ID | Category | Status | Description |
| :--- | :--- | :---: | :--- |
| `BASE_SIMPLE` | Base | `IMPLEMENTED` | Additive base DAX measure compiled directly from certified semantic metric expression. |
| `BASE_RATIO` | Base | `IMPLEMENTED` | Zero-safe ratio measure compiled via DIVIDE(numerator, denominator, BLANK()). |
| `TIME_YTD` | Time Intelligence | `IMPLEMENTED` | Year-to-date cumulative calculation bound to conformed dim_date[date_day]. |
| `TIME_YOY_PCT` | Time Intelligence | `IMPLEMENTED` | Year-over-year percentage variance using SAMEPERIODLASTYEAR and defensive DIVIDE. |
| `TIME_MOM_PCT` | Time Intelligence | `IMPLEMENTED` | Month-over-month percentage variance using DATEADD(..., -1, MONTH). |
| `TIME_ROLLING_12M` | Time Intelligence | `IMPLEMENTED` | Rolling 12-month window calculation over dim_date[date_day]. |
| `TARGET_BASE` | Target & Variance | `IMPLEMENTED` | Target benchmark measure compiled from certified target_expression. |
| `TARGET_VARIANCE` | Target & Variance | `IMPLEMENTED` | Absolute variance against target ([Base] - [Target]). |
| `TARGET_VARIANCE_PCT` | Target & Variance | `IMPLEMENTED` | Relative percentage variance against target via DIVIDE. |

### Power Query M Targets

| Target | Connector Function | Parameters | Status |
| :--- | :--- | :--- | :---: |
| `duckdb` | `Odbc.DataSource` | `pDatabasePath`, `pSchemaName` | `IMPLEMENTED` |
| `databricks` | `Databricks.Catalogs` | `pServerHostname`, `pHttpPath`, `pCatalog`, `pSchema` | `IMPLEMENTED` |
| `snowflake` | `Snowflake.Databases` | `pAccountServer`, `pWarehouse`, `pDatabase`, `pSchema` | `IMPLEMENTED` |

## 5. NL2SQL Benchmark Suites & CI Quality Gates

- **Result-Set Hash Match Floor:** `>= 96%`
- **AST Equivalence Floor:** `>= 94%`
- **Adversarial Block Rate Floor:** `== 100%`

| Suite ID | Category | Target Cases | Languages | Status | Description |
| :--- | :--- | :---: | :---: | :---: | :--- |
| `single_metric` | Single KPI Aggregation | 10 | `pt, en` | `IMPLEMENTED` | Direct certified KPI queries without grouping (totals, ratios, counts, MTTR). |
| `dimensional_breakdown` | Dimensional Breakdown & Ranking | 15 | `pt, en` | `IMPLEMENTED` | KPI rollups grouped by conformed dimensions (region, UF, segment, tier, status) with ORDER BY / TOP N. |
| `time_intelligence` | Temporal Filtering & Grain Rollups | 10 | `pt, en` | `IMPLEMENTED` | Monthly, quarterly, and yearly aggregations across dim_date conformed calendar. |
| `multi_fact_conformed` | Multi-Fact Chasm-Safe CTE Queries | 8 | `pt, en` | `IMPLEMENTED` | Cross-fact revenue + SLA queries compiled via independent pre-aggregated CTEs per fact grain. |
| `bilingual_pt_en` | Bilingual Synonym & Filter Resolution | 7 | `pt, en` | `IMPLEMENTED` | Portuguese/English synonym matching and explicit equality/period filters. |
| `adversarial_firewall` | Adversarial Security & PII Block Suite | 20 | `pt, en` | `IMPLEMENTED` | Injection, DDL/DML, DuckDB file/network primitives, multi-statement, Unicode NFKC, and PII column probes (100% block gate). |
