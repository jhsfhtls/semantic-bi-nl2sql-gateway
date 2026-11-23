# ADR-001: Deterministic Semantic Compilation Before LLM Invocation

- **Status:** Accepted
- **Date:** 2026-11-23
- **Governance & Standards:** `dbt Semantic Layer`, `POP_T_158` (AI FinOps & Model Routing), `Zero-Hallucination NL2SQL`

## Context

Conventional text-to-SQL systems feed raw database DDL and ad-hoc prompts directly to Large Language Models (LLMs). In analytical engineering and executive BI workloads, that pattern fails along four dimensions:

1. **Metric Drift & Definition Divergence:** Different SQL queries or BI dashboards independently re-implement KPIs (such as `gross_margin_pct` or `sla_compliance_rate`), producing inconsistent numbers across Finance, Operations, and Power BI reports.
2. **Silent Fan-Out & Chasm Traps:** Unrestricted joins across fact tables or `many_to_many` relationships inflate row counts silently, corrupting `SUM` and `COUNT` aggregations without raising SQL syntax errors.
3. **LLM Token Waste & Latency (`POP_T_158`):** Invoking an LLM for repetitive, structured business questions over a fixed set of certified metrics increases tail latency and API cost unnecessarily, and makes CI regression tests slow or non-deterministic.
4. **Unbounded Hallucinations:** Allowing a model to invent table names, join keys, or column aliases bypasses semantic governance.

## Decision

We adopt a **Deterministic-First Semantic Compilation Architecture** executed prior to any LLM interaction:

1. **Single Semantic Source of Truth (`catalog/metrics.yaml` + dbt Metadata):**
   - Every certified KPI, grain, time dimension, allowed join path, and blocked sensitive column is declared in `catalog/metrics.yaml`, strictly validated against `catalog/metrics_schema.json` (JSON Schema Draft 2020-12).
   - The semantic compiler (`Phase 02`) ingests dbt `manifest.json`, `schema.yml`, and `metrics.yaml` into an in-memory typed semantic graph before any Power BI artifact generation (`compile-bi`) or NL2SQL resolution (`ask`) occurs.
2. **Directed Typed Join Graph & Fan-Out / Chasm-Trap Prevention:**
   - Every edge in `allowed_joins` must declare an explicit `cardinality` restricted to `many_to_one` or `one_to_one` (`safe_for_aggregation: true`).
   - Direct joins between two `fact` models are **prohibited** at schema validation and compilation time (`chasm-trap invariant`).
   - Queries combining metrics from multiple fact models across conformed dimensions (`dim_date`, `dim_region`, `dim_customer_segment`, `dim_service_tier`) must compile into independent pre-aggregated Common Table Expressions (CTEs) per fact table, joined strictly on the conformed dimension keys at the output grain.
3. **Offline Deterministic Mode as Default (`POP_T_158`):**
   - The default NL2SQL engine runs in `mode="deterministic"`, resolving bilingual (`pt`/`en`) business intents through a typed grammar and lexical/semantic matcher bound strictly to certified metrics, dimensions, and time grains with **zero network calls and zero token cost**.
   - An optional LLM adapter (`Phase 04`) may be enabled explicitly via isolated environment configuration, but it can only propose structured intent or SQL referencing compiled catalog identifiers and must pass the identical `AstSqlFirewall` (`ADR-002`).

## Consequences

- **Positive:**
  - Zero-hallucination metric definitions: both Power BI (`DAX`/`TMDL`) and NL2SQL consume the exact same compiled formulas and join graph.
  - Sub-second offline execution and 100% deterministic CI benchmark evaluation (`scripts/eval_nl2sql.py`).
  - Structural elimination of `N:M` fan-out and multi-fact chasm traps prior to SQL generation.
- **Trade-offs:**
  - Business terms and metrics outside the certified catalog cannot be queried ad hoc until added to dbt models and `catalog/metrics.yaml`.
  - Bilingual deterministic grammar rules require curated synonym mappings for non-standard phrasing unless the optional LLM adapter is enabled.

## Verification Criteria

- `scripts/validate_metrics_schema.py` verifies JSON Schema compliance and proves zero direct `fact`-to-`fact` edges in `allowed_joins`.
- Phase 02 semantic compiler unit tests confirm rejection of cyclic, `many_to_many`, and uncertified joins, and verify pre-aggregated CTE compilation for multi-fact queries.
- Phase 06 deterministic CI benchmarks execute offline with zero LLM API keys or network dependencies.
