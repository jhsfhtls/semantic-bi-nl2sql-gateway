"""Read-only multi-lakehouse bridge with automatic fallback to embedded synthetic dataset."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import duckdb

from semantic_bi_gateway.lakehouse.shield import ClientPathShield, SourceMode
from semantic_bi_gateway.lakehouse.synthetic_bootstrap import materialize_synthetic_duckdb
from semantic_bi_gateway.nl2sql.executor import QueryExecutionResult, ReadOnlyDuckDbExecutor
from semantic_bi_gateway.semantic.models import CompiledSemanticModel


@dataclass(frozen=True)
class LakehouseSpec:
    """Registered local lakehouse source specification."""

    lakehouse_id: str
    relative_db_path: str
    env_var: str
    description: str
    is_synthetic: bool = False


@dataclass(frozen=True)
class LakehouseStatus:
    """Discovery status for a registered lakehouse target."""

    lakehouse_id: str
    available: bool
    db_path: Path | None
    source_mode: SourceMode
    tables: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class ResolvedLakehouse:
    """Resolved read-only DuckDB database target with fallback audit metadata."""

    lakehouse_id: str
    db_path: Path
    source_mode: SourceMode
    fallback_used: bool
    reason: str


@dataclass(frozen=True)
class BridgedQueryResult:
    """Combined multi-lakehouse resolution metadata and read-only query result."""

    resolved: ResolvedLakehouse
    execution: QueryExecutionResult


DEFAULT_LAKEHOUSE_SPECS: tuple[LakehouseSpec, ...] = (
    LakehouseSpec(
        lakehouse_id="public-data-gateway",
        relative_db_path="data/brasil.duckdb",
        env_var="SBG_PDG_DUCKDB_PATH",
        description="Official Brazilian public datasets & cross-domain Gold marts.",
    ),
    LakehouseSpec(
        lakehouse_id="finops-capex-revenue-lakehouse",
        relative_db_path="data/gold/finops_lakehouse.duckdb",
        env_var="SBG_FINOPS_DUCKDB_PATH",
        description="Enterprise CAPEX/OPEX, revenue recovery & B2B economics lakehouse.",
    ),
    LakehouseSpec(
        lakehouse_id="ops-sla-telemetry-lakehouse",
        relative_db_path="data/gold/ops_sla_telemetry.duckdb",
        env_var="SBG_OPS_SLA_DUCKDB_PATH",
        description="Operational field-service SLA telemetry, MTTR & burn-rate lakehouse.",
    ),
    LakehouseSpec(
        lakehouse_id="synthetic_embedded",
        relative_db_path="dbt/target/sbg_semantic_demo.duckdb",
        env_var="SBG_DUCKDB_PATH",
        description="Embedded deterministic synthetic DuckDB lakehouse (seed=42).",
        is_synthetic=True,
    ),
)


def _default_workspace_root(repo_root: Path) -> Path:
    resolved = repo_root.resolve()
    if resolved.parent.name.endswith("-worktrees"):
        return resolved.parent.parent
    return resolved.parent


class MultiLakehouseBridge:
    """Discovers local sibling DuckDB lakehouses read-only and falls back to synthetic data."""

    def __init__(
        self,
        repo_root: Path,
        workspace_root: Path | None = None,
        allow_sibling_discovery: bool = True,
    ) -> None:
        self._repo_root = repo_root.resolve()
        self._workspace_root = (
            workspace_root.resolve()
            if workspace_root is not None
            else _default_workspace_root(self._repo_root)
        )
        self._allow_sibling_discovery = allow_sibling_discovery
        self._shield = ClientPathShield(self._repo_root)
        self._specs = {spec.lakehouse_id: spec for spec in DEFAULT_LAKEHOUSE_SPECS}

    @property
    def shield(self) -> ClientPathShield:
        """Return ClientPathShield protecting this repository."""
        return self._shield

    def inspect_tables_readonly(self, db_path: Path) -> tuple[str, ...]:
        """Connect strictly read-only with locked config and list tables in main schema."""
        resolved = db_path.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"DuckDB file not found: {resolved}")
        conn = duckdb.connect(str(resolved), read_only=True)
        try:
            conn.execute("SET enable_external_access = false;")
            conn.execute("SET lock_configuration = true;")
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
            return tuple(str(row[0]) for row in rows)
        finally:
            conn.close()

    def _candidate_path(self, spec: LakehouseSpec) -> Path | None:
        env_override = os.environ.get(spec.env_var, "").strip()
        if env_override:
            return Path(env_override).expanduser().resolve()
        if spec.is_synthetic:
            return (self._repo_root / spec.relative_db_path).resolve()
        if not self._allow_sibling_discovery:
            return None
        return (self._workspace_root / spec.lakehouse_id / spec.relative_db_path).resolve()

    def discover(self) -> dict[str, LakehouseStatus]:
        """Inspect all registered lakehouses without mutating or failing on missing siblings."""
        statuses: dict[str, LakehouseStatus] = {}
        for spec in DEFAULT_LAKEHOUSE_SPECS:
            source_mode: SourceMode = (
                "synthetic_embedded" if spec.is_synthetic else "lakehouse_readonly"
            )
            candidate = self._candidate_path(spec)
            if candidate is None:
                statuses[spec.lakehouse_id] = LakehouseStatus(
                    lakehouse_id=spec.lakehouse_id,
                    available=False,
                    db_path=None,
                    source_mode=source_mode,
                    tables=(),
                    reason="Sibling lakehouse discovery disabled in isolated mode.",
                )
                continue
            if not candidate.is_file():
                statuses[spec.lakehouse_id] = LakehouseStatus(
                    lakehouse_id=spec.lakehouse_id,
                    available=False,
                    db_path=candidate,
                    source_mode=source_mode,
                    tables=(),
                    reason=f"DuckDB file not present at {candidate}.",
                )
                continue

            try:
                tables = self.inspect_tables_readonly(candidate)
            except duckdb.Error as exc:
                statuses[spec.lakehouse_id] = LakehouseStatus(
                    lakehouse_id=spec.lakehouse_id,
                    available=False,
                    db_path=candidate,
                    source_mode=source_mode,
                    tables=(),
                    reason=f"Read-only inspection failed: {exc}",
                )
                continue

            statuses[spec.lakehouse_id] = LakehouseStatus(
                lakehouse_id=spec.lakehouse_id,
                available=True,
                db_path=candidate,
                source_mode=source_mode,
                tables=tables,
                reason="Available (read-only verified).",
            )
        return statuses

    def resolve_database(
        self,
        preferred_lakehouse: str | None = None,
        allow_fallback: bool = True,
        auto_bootstrap_synthetic: bool = True,
    ) -> ResolvedLakehouse:
        """Resolve preferred read-only lakehouse or fall back cleanly to synthetic_embedded."""
        if preferred_lakehouse is not None and preferred_lakehouse not in self._specs:
            raise ValueError(
                f"Unknown lakehouse '{preferred_lakehouse}'. "
                f"Registered targets: {', '.join(sorted(self._specs))}."
            )

        statuses = self.discover()

        if preferred_lakehouse and preferred_lakehouse != "synthetic_embedded":
            status = statuses[preferred_lakehouse]
            if status.available and status.db_path is not None:
                return ResolvedLakehouse(
                    lakehouse_id=preferred_lakehouse,
                    db_path=status.db_path,
                    source_mode="lakehouse_readonly",
                    fallback_used=False,
                    reason=f"Connected read-only to '{preferred_lakehouse}'.",
                )
            if not allow_fallback:
                raise FileNotFoundError(
                    f"Requested lakehouse '{preferred_lakehouse}' unavailable ({status.reason}) "
                    "and fallback is disabled."
                )
            fallback_reason = (
                f"Requested lakehouse '{preferred_lakehouse}' unavailable ({status.reason}); "
                "falling back to embedded synthetic dataset."
            )
            return self._resolve_synthetic(
                statuses["synthetic_embedded"],
                fallback_used=True,
                reason=fallback_reason,
                auto_bootstrap=auto_bootstrap_synthetic,
            )

        return self._resolve_synthetic(
            statuses["synthetic_embedded"],
            fallback_used=False,
            reason="Using embedded deterministic synthetic dataset (seed=42).",
            auto_bootstrap=auto_bootstrap_synthetic,
        )

    def _resolve_synthetic(
        self,
        synthetic_status: LakehouseStatus,
        fallback_used: bool,
        reason: str,
        auto_bootstrap: bool,
    ) -> ResolvedLakehouse:
        syn_spec = self._specs["synthetic_embedded"]
        target_path = synthetic_status.db_path or (self._repo_root / syn_spec.relative_db_path).resolve()
        if not target_path.is_file():
            if not auto_bootstrap:
                raise FileNotFoundError(
                    f"Synthetic DuckDB missing at {target_path} and auto_bootstrap is disabled."
                )
            materialize_synthetic_duckdb(self._repo_root, target_path)
        return ResolvedLakehouse(
            lakehouse_id="synthetic_embedded",
            db_path=target_path,
            source_mode="synthetic_embedded",
            fallback_used=fallback_used,
            reason=reason,
        )

    def execute_query(
        self,
        sql: str,
        compiled: CompiledSemanticModel,
        preferred_lakehouse: str | None = None,
        allow_fallback: bool = True,
        auto_bootstrap_synthetic: bool = True,
    ) -> BridgedQueryResult:
        """Resolve lakehouse target and execute AST-validated SQL strictly read-only."""
        resolved = self.resolve_database(
            preferred_lakehouse=preferred_lakehouse,
            allow_fallback=allow_fallback,
            auto_bootstrap_synthetic=auto_bootstrap_synthetic,
        )
        executor = ReadOnlyDuckDbExecutor(compiled, db_path=resolved.db_path)
        execution = executor.execute(sql)
        return BridgedQueryResult(resolved=resolved, execution=execution)
