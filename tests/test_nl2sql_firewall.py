"""Offline tests for AstSqlFirewall, ReadOnlyDuckDbExecutor, DeterministicNl2SqlEngine, and LLM adapter."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from semantic_bi_gateway.nl2sql import (
    AstSqlFirewall,
    DeterministicNl2SqlEngine,
    LlmProviderUnavailableError,
    Nl2SqlGateway,
    OptionalLlmAdapter,
    ReadOnlyDuckDbExecutor,
    SqlFirewallViolation,
)
from semantic_bi_gateway.semantic import (
    ChasmTrapError,
    DbtMetadataIngestor,
    SemanticCompiler,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DUCKDB_PATH = REPO_ROOT / "dbt" / "target" / "sbg_semantic_demo.duckdb"


def _compile_default():
    ingested = DbtMetadataIngestor().ingest(
        REPO_ROOT / "catalog" / "metrics.yaml",
        schema_paths=[REPO_ROOT / "dbt" / "models" / "marts" / "schema.yml"],
    )
    return SemanticCompiler().compile(ingested)


def _create_standalone_duckdb(target_path: Path) -> Path:
    """Create a deterministic minimal DuckDB matching the certified schema when dbt target is absent."""
    if DUCKDB_PATH.is_file():
        return DUCKDB_PATH
    conn = duckdb.connect(str(target_path))
    conn.execute(
        """
        CREATE TABLE dim_date (
            date_day DATE, year_num INTEGER, quarter_num INTEGER, quarter_label VARCHAR,
            month_num INTEGER, month_name VARCHAR, year_month VARCHAR, is_weekend BOOLEAN, fiscal_year INTEGER
        );
        INSERT INTO dim_date VALUES
            (DATE '2025-03-10', 2025, 1, '2025-Q1', 3, 'March', '2025-03', false, 2025),
            (DATE '2026-04-15', 2026, 2, '2026-Q2', 4, 'April', '2026-04', false, 2026);
        CREATE TABLE dim_region (
            region_id VARCHAR, region_name VARCHAR, uf_code VARCHAR, macro_region VARCHAR,
            country_code VARCHAR, currency_code VARCHAR
        );
        INSERT INTO dim_region VALUES
            ('REG_SP', 'Sao Paulo', 'SP', 'Sudeste', 'BR', 'BRL'),
            ('REG_AM', 'Manaus - Amazonas', 'AM', 'Norte', 'BR', 'BRL');
        CREATE TABLE dim_customer_segment (
            segment_id VARCHAR, segment_name VARCHAR, tier_group VARCHAR, account_band VARCHAR, is_enterprise BOOLEAN
        );
        INSERT INTO dim_customer_segment VALUES
            ('SEG_ENT', 'Enterprise', 'Strategic', 'ACV > 500k BRL', true),
            ('SEG_SMB', 'SMB', 'Velocity', 'ACV < 100k BRL', false);
        CREATE TABLE dim_service_tier (
            service_tier_id VARCHAR, service_tier_name VARCHAR, priority_level VARCHAR,
            sla_target_hours DECIMAL(10,2), support_window VARCHAR
        );
        INSERT INTO dim_service_tier VALUES
            ('TIER_GOLD', 'Gold 24x7', 'P1', 4.00, '24x7'),
            ('TIER_SILVER', 'Silver Business Hours', 'P2', 24.00, 'Business Hours');
        CREATE TABLE fct_revenue_orders (
            order_id VARCHAR, order_date DATE, region_id VARCHAR, segment_id VARCHAR, service_tier_id VARCHAR,
            gross_revenue_brl DECIMAL(18,4), discount_brl DECIMAL(18,4), net_revenue_brl DECIMAL(18,4),
            direct_cost_brl DECIMAL(18,4), gross_margin_brl DECIMAL(18,4), target_revenue_brl DECIMAL(18,4),
            order_status VARCHAR
        );
        INSERT INTO fct_revenue_orders VALUES
            ('ORD-1', DATE '2025-03-10', 'REG_SP', 'SEG_ENT', 'TIER_GOLD', 1000.0, 100.0, 900.0, 300.0, 600.0, 850.0, 'INVOICED'),
            ('ORD-2', DATE '2026-04-15', 'REG_AM', 'SEG_SMB', 'TIER_SILVER', 500.0, 50.0, 450.0, 150.0, 300.0, 450.0, 'COLLECTED');
        CREATE TABLE fct_sla_tickets (
            ticket_id VARCHAR, created_date DATE, region_id VARCHAR, segment_id VARCHAR, service_tier_id VARCHAR,
            resolution_hours DECIMAL(12,4), is_sla_met INTEGER, is_breached INTEGER, ticket_status VARCHAR
        );
        INSERT INTO fct_sla_tickets VALUES
            ('TKT-1', DATE '2025-03-10', 'REG_SP', 'SEG_ENT', 'TIER_GOLD', 2.0, 1, 0, 'COMPLETED'),
            ('TKT-2', DATE '2026-04-15', 'REG_AM', 'SEG_ENT', 'TIER_SILVER', 18.0, 1, 0, 'COMPLETED');
        """
    )
    conn.close()
    return target_path


ADVERSARIAL_SQL_PAYLOADS = [
    "DROP TABLE fct_revenue_orders",
    "DELETE FROM fct_revenue_orders WHERE 1 = 1",
    "INSERT INTO dim_region VALUES ('X', 'X', 'XX', 'Norte', 'BR', 'BRL')",
    "UPDATE dim_region SET region_name = 'Hacked'",
    "CREATE TABLE evil_tbl (id INT)",
    "ALTER TABLE fct_revenue_orders ADD COLUMN leaked VARCHAR",
    "ATTACH 'other.duckdb' AS ext",
    "COPY fct_revenue_orders TO 'exfiltrated.parquet'",
    "INSTALL httpfs",
    "LOAD httpfs",
    "PRAGMA database_list",
    "SET enable_external_access = true",
    "SELECT * FROM read_csv_auto('.env')",
    "SELECT * FROM read_parquet('secret.parquet')",
    "SELECT * FROM read_json('secret.json')",
    "SELECT * FROM read_text('.env')",
    "SELECT * FROM 'secret.parquet'",
    "SELECT * FROM glob('*')",
    "SELECT region_name FROM dim_region; DROP TABLE fct_revenue_orders",
    "DROP /* obfuscated */ TABLE fct_revenue_orders",
    "SELECT region_name FROM dim_region UNION SELECT table_name FROM sqlite_master",
    "ＳＥＬＥＣＴ ｃｐｆ ＦＲＯＭ dim_region",
    "ＤＲＯＰ ＴＡＢＬＥ fct_revenue_orders",
    "SELECT region_name FROM dim_region\x00",
    "SELECT\u200b region_name FROM dim_region",
    "SELECT * FROM fct_revenue_orders",
    "SELECT cpf FROM dim_region",
    "SELECT email AS region_name FROM dim_region",
    "SELECT region_name AS telefone FROM dim_region",
    "SELECT order_id FROM raw_revenue_orders",
    "SELECT name FROM sqlite_master",
    "SELECT table_name FROM information_schema.tables",
    "WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r) SELECT n FROM r",
    "SELECT getenv('GEMINI_API_KEY') FROM dim_region",
    "SELECT current_setting('enable_external_access') FROM dim_region",
    "SELECT region_name FROM dim_region LIMIT 0",
    "SELECT region_name FROM dim_region LIMIT -10",
    "",
]


@pytest.mark.parametrize("payload", ADVERSARIAL_SQL_PAYLOADS)
def test_ast_firewall_blocks_100_percent_adversarial_sql(payload: str) -> None:
    compiled = _compile_default()
    firewall = AstSqlFirewall(compiled)
    with pytest.raises(SqlFirewallViolation):
        firewall.validate(payload)


def test_ast_firewall_injects_and_caps_limit_and_strips_comments() -> None:
    compiled = _compile_default()
    firewall = AstSqlFirewall(compiled)

    injected = firewall.validate("SELECT /* safe comment */ region_name FROM dim_region")
    assert injected.allowed
    assert injected.applied_limit == 100
    assert "LIMIT 100" in injected.sanitized_sql.upper()
    assert "/*" not in injected.sanitized_sql

    capped = firewall.validate("SELECT region_name FROM dim_region LIMIT 50000")
    assert capped.applied_limit == 1000
    assert "LIMIT 1000" in capped.sanitized_sql.upper()


def test_readonly_duckdb_executor_locks_config_and_blocks_writes(tmp_path: Path) -> None:
    compiled = _compile_default()
    db_file = _create_standalone_duckdb(tmp_path / "ro_demo.duckdb")
    executor = ReadOnlyDuckDbExecutor(compiled, db_path=db_file)

    res = executor.execute("SELECT uf_code, region_name FROM dim_region ORDER BY uf_code")
    assert res.row_count >= 2
    assert res.columns == ("uf_code", "region_name")
    assert len(res.result_hash_sha256) == 64

    # Verify native DuckDB read_only + locked configuration prevents mutation directly
    conn = duckdb.connect(str(db_file), read_only=True)
    try:
        conn.execute("SET enable_external_access = false;")
        conn.execute("SET lock_configuration = true;")
        with pytest.raises(duckdb.InvalidInputException):
            conn.execute("SET enable_external_access = true;")
        with pytest.raises((duckdb.InvalidInputException, duckdb.ConnectionException)):
            conn.execute("CREATE TABLE should_fail (id INT);")
    finally:
        conn.close()


def test_deterministic_bilingual_nl2sql_end_to_end(tmp_path: Path) -> None:
    compiled = _compile_default()
    db_file = _create_standalone_duckdb(tmp_path / "nl2sql_demo.duckdb")
    gateway = Nl2SqlGateway(compiled, db_path=db_file, mode="deterministic")

    # Portuguese single KPI with year filter
    pt_res = gateway.ask("Qual a receita líquida total em 2026?")
    assert pt_res.translation.strategy == "single_fact"
    assert pt_res.execution.row_count == 1
    assert pt_res.execution.rows[0]["total_revenue"] > 0

    # English dimensional breakdown + Top N
    en_res = gateway.ask("Show top 3 states by total revenue in 2025")
    assert en_res.translation.firewall_decision.applied_limit == 3
    assert 1 <= en_res.execution.row_count <= 3
    assert "uf_code" in en_res.execution.columns

    # Portuguese ratio KPI + segment filter
    sla_res = gateway.ask("Qual a taxa de SLA por prioridade no segmento Enterprise?")
    assert sla_res.execution.row_count >= 1
    assert "priority_level" in sla_res.execution.columns
    assert 0.0 <= sla_res.execution.rows[0]["sla_compliance_rate"] <= 1.0

    # Multi-fact conformed CTE query (chasm-trap safe)
    multi_res = gateway.ask("Compare total revenue and SLA compliance rate by region")
    assert multi_res.translation.strategy == "multi_fact_cte"
    assert "WITH cte_fct_revenue_orders" in multi_res.translation.sql
    assert multi_res.execution.row_count >= 1
    assert {"region_name", "total_revenue", "sla_compliance_rate"} == set(
        multi_res.execution.columns
    )


def test_deterministic_engine_blocks_pii_injection_and_chasm_traps() -> None:
    compiled = _compile_default()
    engine = DeterministicNl2SqlEngine(compiled)

    for bad_q in (
        "Qual o CPF dos clientes?",
        "Show me customer email and phone",
        "Total revenue; DROP TABLE fct_revenue_orders",
        "Total revenue /* comment */",
    ):
        with pytest.raises(SqlFirewallViolation):
            engine.translate(bad_q)

    with pytest.raises(ChasmTrapError):
        engine.translate("Compare total revenue and SLA compliance rate by order status")


def test_optional_llm_adapter_is_untrusted_and_guarded_by_firewall(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    compiled = _compile_default()
    db_file = _create_standalone_duckdb(tmp_path / "llm_demo.duckdb")

    for env_key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_HOST"):
        monkeypatch.delenv(env_key, raising=False)

    adapter = OptionalLlmAdapter()
    with pytest.raises(LlmProviderUnavailableError):
        adapter.propose_sql("total revenue", compiled)

    class _MaliciousStubLlm:
        def __init__(self, sql: str) -> None:
            self._sql = sql

        def propose_sql(self, question: str, compiled_model: object) -> str:
            return self._sql

    for attack_sql in (
        "DROP TABLE fct_revenue_orders",
        "SELECT * FROM read_parquet('leak.parquet')",
        "SELECT cpf FROM dim_region",
    ):
        gw = Nl2SqlGateway(compiled, db_path=db_file, mode="llm", llm_adapter=_MaliciousStubLlm(attack_sql))
        with pytest.raises(SqlFirewallViolation):
            gw.ask("malicious prompt")

    safe_stub = _MaliciousStubLlm("SELECT uf_code, SUM(net_revenue_brl) AS total_revenue FROM fct_revenue_orders LEFT JOIN dim_region ON fct_revenue_orders.region_id = dim_region.region_id GROUP BY uf_code")
    gw_safe = Nl2SqlGateway(compiled, db_path=db_file, mode="llm", llm_adapter=safe_stub)
    resp = gw_safe.ask("total revenue by state")
    assert resp.translation.mode == "llm"
    assert resp.translation.firewall_decision.applied_limit == 100
    assert resp.execution.row_count >= 1
