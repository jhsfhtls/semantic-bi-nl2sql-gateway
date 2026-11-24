"""Offline tests for MultiLakehouseBridge, ClientPathShield, and standalone synthetic DuckDB."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from semantic_bi_gateway.lakehouse import (
    ClientPathShield,
    ClientPathShieldError,
    MultiLakehouseBridge,
    materialize_synthetic_duckdb,
)
from semantic_bi_gateway.nl2sql import SqlFirewallViolation
from semantic_bi_gateway.semantic import DbtMetadataIngestor, SemanticCompiler

REPO_ROOT = Path(__file__).resolve().parents[1]


def _compile_default():
    ingested = DbtMetadataIngestor().ingest(
        REPO_ROOT / "catalog" / "metrics.yaml",
        schema_paths=[REPO_ROOT / "dbt" / "models" / "marts" / "schema.yml"],
    )
    return SemanticCompiler().compile(ingested)


def test_isolated_github_mode_falls_back_to_synthetic_without_private_siblings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    compiled = _compile_default()
    fake_workspace = tmp_path / "empty_workspace"
    fake_workspace.mkdir()
    isolated_syn_db = tmp_path / "isolated_syn.duckdb"
    monkeypatch.setenv("SBG_DUCKDB_PATH", str(isolated_syn_db))
    for var in ("SBG_PDG_DUCKDB_PATH", "SBG_FINOPS_DUCKDB_PATH", "SBG_OPS_SLA_DUCKDB_PATH"):
        monkeypatch.delenv(var, raising=False)

    bridge = MultiLakehouseBridge(
        repo_root=REPO_ROOT,
        workspace_root=fake_workspace,
        allow_sibling_discovery=False,
    )
    statuses = bridge.discover()
    assert not statuses["public-data-gateway"].available
    assert not statuses["finops-capex-revenue-lakehouse"].available
    assert not statuses["ops-sla-telemetry-lakehouse"].available

    result = bridge.execute_query(
        sql="SELECT dim_region.uf_code, SUM(fct_revenue_orders.net_revenue_brl) AS total_revenue "
        "FROM fct_revenue_orders LEFT JOIN dim_region "
        "ON fct_revenue_orders.region_id = dim_region.region_id GROUP BY dim_region.uf_code",
        compiled=compiled,
        preferred_lakehouse="finops-capex-revenue-lakehouse",
        allow_fallback=True,
        auto_bootstrap_synthetic=True,
    )
    assert result.resolved.lakehouse_id == "synthetic_embedded"
    assert result.resolved.source_mode == "synthetic_embedded"
    assert result.resolved.fallback_used is True
    assert isolated_syn_db.is_file()
    assert result.execution.row_count == 8


def test_sibling_lakehouse_discovery_and_readonly_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    compiled = _compile_default()
    for var in (
        "SBG_PDG_DUCKDB_PATH",
        "SBG_FINOPS_DUCKDB_PATH",
        "SBG_OPS_SLA_DUCKDB_PATH",
        "SBG_DUCKDB_PATH",
    ):
        monkeypatch.delenv(var, raising=False)

    mock_workspace = tmp_path / "gitops_mock"
    pdg_db = mock_workspace / "public-data-gateway" / "data" / "brasil.duckdb"
    finops_db = mock_workspace / "finops-capex-revenue-lakehouse" / "data" / "gold" / "finops_lakehouse.duckdb"
    ops_db = mock_workspace / "ops-sla-telemetry-lakehouse" / "data" / "gold" / "ops_sla_telemetry.duckdb"

    materialize_synthetic_duckdb(REPO_ROOT, finops_db)
    pdg_db.parent.mkdir(parents=True, exist_ok=True)
    ops_db.parent.mkdir(parents=True, exist_ok=True)

    for sibling_db, marker_table in (
        (pdg_db, "dim_region"),
        (ops_db, "fct_sla_tickets"),
    ):
        conn = duckdb.connect(str(sibling_db))
        conn.execute(f"CREATE TABLE {marker_table} (id VARCHAR);")
        conn.close()

    bridge = MultiLakehouseBridge(
        repo_root=REPO_ROOT,
        workspace_root=mock_workspace,
        allow_sibling_discovery=True,
    )
    statuses = bridge.discover()
    assert statuses["public-data-gateway"].available
    assert statuses["finops-capex-revenue-lakehouse"].available
    assert statuses["ops-sla-telemetry-lakehouse"].available
    assert "fct_revenue_orders" in statuses["finops-capex-revenue-lakehouse"].tables

    result = bridge.execute_query(
        sql="SELECT SUM(net_revenue_brl) AS total_revenue FROM fct_revenue_orders",
        compiled=compiled,
        preferred_lakehouse="finops-capex-revenue-lakehouse",
        allow_fallback=False,
    )
    assert result.resolved.lakehouse_id == "finops-capex-revenue-lakehouse"
    assert result.resolved.source_mode == "lakehouse_readonly"
    assert result.resolved.fallback_used is False
    assert result.execution.rows[0]["total_revenue"] > 0

    with pytest.raises(SqlFirewallViolation):
        bridge.execute_query(
            sql="CREATE TABLE tampered (id INT)",
            compiled=compiled,
            preferred_lakehouse="finops-capex-revenue-lakehouse",
        )


def test_strict_no_fallback_raises_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for var in ("SBG_PDG_DUCKDB_PATH", "SBG_FINOPS_DUCKDB_PATH", "SBG_OPS_SLA_DUCKDB_PATH"):
        monkeypatch.delenv(var, raising=False)

    bridge = MultiLakehouseBridge(
        repo_root=REPO_ROOT,
        workspace_root=tmp_path,
        allow_sibling_discovery=False,
    )
    with pytest.raises(FileNotFoundError):
        bridge.resolve_database(preferred_lakehouse="ops-sla-telemetry-lakehouse", allow_fallback=False)


def test_client_path_shield_enforces_public_repo_boundary(tmp_path: Path) -> None:
    shield = ClientPathShield(REPO_ROOT)

    # Local sibling lakehouse results must never be written inside public repo
    with pytest.raises(ClientPathShieldError):
        shield.validate_output_path(REPO_ROOT / "bi" / "output" / "client_leak.csv", source_mode="lakehouse_readonly")

    # Protected source/catalog dirs are always blocked even in synthetic mode
    for blocked in (
        REPO_ROOT / "src" / "evil.py",
        REPO_ROOT / "catalog" / "metrics.yaml",
        REPO_ROOT / ".git" / "config",
    ):
        with pytest.raises(ClientPathShieldError):
            shield.validate_output_path(blocked, source_mode="synthetic_embedded")

    external_target = tmp_path / "client_exports" / "summary.json"
    assert shield.validate_output_path(external_target, source_mode="lakehouse_readonly") == external_target.resolve()

    synthetic_bi_out = REPO_ROOT / "bi" / "output" / "measures.dax"
    assert shield.validate_output_path(synthetic_bi_out, source_mode="synthetic_embedded") == synthetic_bi_out.resolve()


def test_materialize_synthetic_duckdb_row_counts(tmp_path: Path) -> None:
    db_path = materialize_synthetic_duckdb(REPO_ROOT, tmp_path / "synthetic_check.duckdb")
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM dim_date").fetchone()[0] == 730
        assert conn.execute("SELECT COUNT(*) FROM dim_region").fetchone()[0] == 8
        assert conn.execute("SELECT COUNT(*) FROM dim_customer_segment").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM dim_service_tier").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM fct_revenue_orders").fetchone()[0] == 120
        assert conn.execute("SELECT COUNT(*) FROM fct_sla_tickets").fetchone()[0] == 150
    finally:
        conn.close()
