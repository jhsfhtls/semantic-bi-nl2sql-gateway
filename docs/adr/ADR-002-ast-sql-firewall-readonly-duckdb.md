# ADR-002: AST SQL Firewall & Read-Only DuckDB Execution Boundary

- **Status:** Accepted
- **Date:** 2026-11-23
- **Governance & Standards:** `sqlglot` AST Validation, `DuckDB Read-Only`, `OWASP LLM01/LLM02`, `POP_T_154` (LGPD & Multi-Repo Shield), `security_env_isolation.md`

## Context

Any natural-language-to-SQL gateway introduces high-severity execution and data-exfiltration risks if generated queries are forwarded directly to an analytical engine. In particular:

1. **Prompt Injection & SQL Injection:** User prompts or maliciously crafted text can attempt statement stacking (`; DROP TABLE ...`), comment-based obfuscation (`/* ... */`), or Unicode confusables to smuggle forbidden statements.
2. **Embedded Engine IO & Extension Primitives:** DuckDB exposes powerful table functions and pragmas (`read_csv`, `read_parquet`, `read_json`, `read_text`, `glob`, `http_get`, `ATTACH`, `COPY`, `INSTALL`, `LOAD`, `PRAGMA`) that can read local host files (including `.env` or SSH keys), probe local network endpoints, or write artifacts to disk if left unguarded.
3. **PII / LGPD Exposure (`POP_T_154`):** Local analytical warehouses may contain sensitive or regulated columns (`cpf`, `cnpj_pf`, `email`, `phone`, `endereco_completo`, `rg`) that must never be exposed by public-facing or agent-driven NL2SQL queries.
4. **Indirect Prompt Injection to Downstream Agents:** Query results returned to MCP clients or agentic workflows can contain untrusted strings from data sources (`OWASP LLM01`).

## Decision

All SQL statements—whether produced by the deterministic compiler, the optional LLM adapter, or submitted to `validate-sql`—are treated as **untrusted input** and enforced through a multi-layered security boundary:

1. **Pre-Parse Normalization & Resource Guards:**
   - Reject queries containing null bytes (`\x00`), empty input, or exceeding the maximum query length budget.
   - Apply Unicode `NFKC` normalization prior to parsing to neutralize full-width and homoglyph obfuscation attempts.
2. **Mandatory `AstSqlFirewall` (`sqlglot`, DuckDB Dialect):**
   - **Single Statement `SELECT`-Only:** Reject multi-statement payloads (`len(parsed) != 1`) and any root AST node other than `exp.Select` (including `With` only when wrapping a single `Select`, while blocking `Insert`, `Update`, `Delete`, `Drop`, `Create`, `Alter`, `Attach`, `Detach`, `Copy`, `Export`, `Import`, `Install`, `Load`, `Pragma`, `Set`, `Call`, `Transaction`, `Command`).
   - **Closed Identifier Allowlist:** Every `exp.Table` and `exp.Column` node in the AST (outside CTE aliases defined inside the query) must resolve strictly to certified models and non-sensitive columns registered in `catalog/metrics.yaml`.
   - **Sensitive Column & PII Pattern Block:** Any reference to `security.blocked_sensitive_columns` or column names matching `security.blocked_column_patterns` (e.g., `cpf`, `cnpj_pf`, `email`, `phone`, `telefone`, `celular`, `rg`) triggers immediate rejection, regardless of table alias or quoting.
   - **Closed Function Allowlist & IO/Network Ban:** Only explicitly allowlisted analytical functions (`SUM`, `COUNT`, `AVG`, `MIN`, `MAX`, `COALESCE`, `NULLIF`, `CASE`, `CAST`, `ROUND`, `DATE_TRUNC`, `EXTRACT`, `STRFTIME`, `ABS`, `LOWER`, `UPPER`, `TRIM`) are permitted. All file/network/extension functions (`read_csv*`, `read_parquet*`, `read_json*`, `read_text`, `read_blob`, `glob`, `http_*`, `sqlite_*`, `postgres_*`, `mysql_*`, `iceberg_*`, `delta_*`, `duckdb_*`) are unconditionally blocked.
   - **Mandatory `LIMIT` & Cost Ceiling:** Queries without a `LIMIT` clause receive the catalog `default_limit` (e.g., `100`); explicit limits exceeding `max_limit` (e.g., `1000`) are rejected or capped per policy, and execution enforces `query_timeout_seconds`.
3. **DuckDB Defense-in-Depth Execution Sandbox:**
   - Connections execute with `read_only=True` whenever backed by persisted DuckDB files, and immediately run:
     - `SET enable_external_access = false;`
     - `SET lock_configuration = true;`
   - No local secret files (`.env`) or sibling repo files are ever read by the firewall or query executor; outputs written locally obey the **Client Path Shield** (`POP_T_154`), rejecting writes into sensitive or external restricted directories from public endpoints.
4. **Agent Output Framing (`OWASP LLM01/LLM02`):**
   - MCP server (`sbg-mcp`) responses wrap all query metadata and result rows inside `<untrusted_data>` XML delimiters with explicit non-instructional framing.

## Consequences

- **Positive:**
  - 100% deterministic rejection of destructive DDL/DML, file-read primitives, multi-statement injection, Unicode bypasses, and sensitive column exfiltration across all four interfaces (`SDK`, `CLI`, `FastAPI`, `MCP`).
  - Execution guarantees hold even if an optional LLM adapter is compromised by prompt injection.
- **Trade-offs:**
  - Arbitrary ad-hoc DuckDB table functions and uncertified analytical functions cannot run until explicitly reviewed and added to `security.allowed_functions`.

## Verification Criteria

- Phase 04 adversarial security suite achieves **100% block rate** across `DROP`, `ATTACH`, `COPY`, `INSTALL`, `LOAD`, `PRAGMA`, `read_csv`/`read_parquet`/`.env` file reads, multi-statement payloads, SQL comments, Unicode NFKC confusables, and sensitive column queries.
- Phase 05 and Phase 07 integration tests verify read-only DuckDB enforcement (write attempts raise read-only errors) and verify firewall parity across `SDK`, `CLI`, `FastAPI`, and `MCP` (including `<untrusted_data>` framing on MCP).
