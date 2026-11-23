# ADR-003: Deterministic Power BI Codegen Standard (DAX, TMDL, Power Query M, WCAG AA Theme)

- **Status:** Accepted
- **Date:** 2026-11-23
- **Governance & Standards:** `Power BI PBIP / TMDL`, `DAX Best Practices`, `Power Query M`, `WCAG 2.1 AA`, `C.O.R.E. Delivery Phase 6`

## Context

During enterprise analytics delivery (`biz-consulting-orchestrator` Phase 6), translating dbt semantic models into Power BI reports manually introduces repetitive boilerplate, inconsistent Time Intelligence formulas, fragile hardcoded connection strings, inaccessible color palettes, and undocumented KPIs. Generating those artifacts directly from the compiled semantic catalog eliminates manual effort while keeping BI definitions synchronized with dbt.

## Decision

We standardize a deterministic BI code generation pipeline (`sbg compile-bi`) driven solely by the compiled semantic model (`Phase 02`):

1. **DAX & PBIP-Compatible TMDL Measures (`.dax` / `.tmdl`):**
   - Every certified metric emits canonical DAX expressions wrapped in defensive division (`DIVIDE(<num>, <den>, BLANK())`) to prevent divide-by-zero errors.
   - Metrics flagged with `time_intelligence: true` deterministically generate companion measures bound to the model's `dim_date` table:
     - Base measure (`[Total Revenue]`)
     - Year-to-Date (`[Total Revenue YTD]` via `TOTALYTD`)
     - Year-over-Year % (`[Total Revenue YoY %]` via `SAMEPERIODLASTYEAR` + `DIVIDE`)
     - Month-over-Month % (`[Total Revenue MoM %]` via `DATEADD(..., -1, MONTH)` + `DIVIDE`)
     - Rolling 12 Months (`[Total Revenue R12M]` via `DATESINPERIOD(..., -12, MONTH)`)
   - Metrics declaring `target_expression` additionally emit target and variance measures (`[... Target]`, `[... Variance vs Target]`, `[... Variance vs Target %]`).
   - Every measure includes explicit `formatString`, `displayFolder`, and `description` metadata for PBIP/TMDL portability.
2. **Parameterized Power Query M Connectors (`.pq`):**
   - Emit separate parameterized `.pq` scripts supporting three target warehouse dialects:
     - `DuckDB` (local/CI analytical files via ODBC/Flight parameters `pDatabasePath`, `pSchemaName`)
     - `Databricks` (`Databricks.Catalogs` with `pServerHostname`, `pHttpPath`, `pCatalog`, `pSchema`)
     - `Snowflake` (`Snowflake.Databases` with `pAccountServer`, `pWarehouse`, `pDatabase`, `pSchema`)
   - All generated queries declare explicit `Table.TransformColumnTypes` contracts matching `catalog/metrics.yaml` column types and never embed credentials or secrets.
3. **Accessible Corporate Theme (`corporate_theme.json` — WCAG 2.1 AA):**
   - Emit a Power BI JSON theme with a colorblind-safe corporate palette where every foreground/background pair is programmatically verified to meet **WCAG AA contrast ratios** (`>= 4.5:1` for normal body/label text and `>= 3.0:1` for large titles/UI components).
4. **Markdown KPI Dictionary (`kpi_dictionary.md`):**
   - Emit a deterministic Markdown reference linking each certified KPI to its dbt source model, grain, SQL formula, DAX measure name, owner, format, and allowed analytical dimensions.

## Consequences

- **Positive:**
  - Guarantees 100% parity between dbt SQL metrics, Power BI DAX measures, and NL2SQL answers.
  - Byte-for-byte deterministic outputs enable golden-file snapshot testing in CI.
  - Eliminates manual DAX/Power Query boilerplate and enforces accessible visual defaults out of the box.
- **Trade-offs:**
  - Custom visual page layouts (`.pbir` report pages) are outside the semantic codegen scope; the generator focuses on the semantic model layer (`TMDL`/`DAX`), ingestion connectors (`Power Query M`), theme, and documentation.

## Verification Criteria

- Phase 03 golden-file tests confirm byte-identical deterministic regeneration across repeated `compile-bi` executions.
- Structural syntax validators verify balanced parentheses/brackets in `.dax`/`.tmdl`, valid `let ... in` blocks with declared parameters in `.pq`, and programmatic WCAG AA contrast ratios (`>= 4.5:1`) in `corporate_theme.json`.
