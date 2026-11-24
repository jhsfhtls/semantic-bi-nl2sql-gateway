"""Unified Python SDK facade (`SemanticBiGateway`) enforcing semantic compilation and AST firewall."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from semantic_bi_gateway._version import __version__
from semantic_bi_gateway.bi import PowerBiCodeGenerator, render_kpi_dictionary
from semantic_bi_gateway.evals import Nl2SqlEvalHarness, report_to_dict
from semantic_bi_gateway.lakehouse import MultiLakehouseBridge, SourceMode
from semantic_bi_gateway.nl2sql import (
    AstSqlFirewall,
    Nl2SqlGateway,
    ReadOnlyDuckDbExecutor,
    SqlFirewallViolation,
)
from semantic_bi_gateway.nl2sql.llm_adapter import LlmSqlProposer
from semantic_bi_gateway.semantic import DbtMetadataIngestor, SemanticCompiler
from semantic_bi_gateway.semantic.models import CompiledSemanticModel


def resolve_gateway_repo_root(explicit_root: Path | None = None) -> Path:
    """Resolve repository root safely across editable installs, CWD, and git worktrees."""
    if explicit_root is not None:
        return explicit_root.resolve()
    cwd_root = Path.cwd().resolve()
    if (cwd_root / "catalog" / "metrics.yaml").is_file():
        return cwd_root
    return Path(__file__).resolve().parents[2]


class SemanticBiGateway:
    """Single source-of-truth gateway shared identically by SDK, CLI, FastAPI, and MCP."""

    def __init__(
        self,
        repo_root: Path | None = None,
        allow_sibling_discovery: bool = False,
        mode: Literal["deterministic", "llm"] = "deterministic",
        llm_adapter: LlmSqlProposer | None = None,
    ) -> None:
        self._repo_root = resolve_gateway_repo_root(repo_root)
        self._allow_sibling_discovery = allow_sibling_discovery
        self._default_mode: Literal["deterministic", "llm"] = mode
        self._llm_adapter = llm_adapter
        self._compiled: CompiledSemanticModel | None = None
        self._bridge = MultiLakehouseBridge(
            self._repo_root,
            allow_sibling_discovery=allow_sibling_discovery,
        )

    @property
    def repo_root(self) -> Path:
        """Return resolved repository root."""
        return self._repo_root

    @property
    def compiled(self) -> CompiledSemanticModel:
        """Lazily ingest and compile catalog/metrics.yaml + dbt schema metadata."""
        if self._compiled is None:
            metrics_path = self._repo_root / "catalog" / "metrics.yaml"
            schema_path = self._repo_root / "dbt" / "models" / "marts" / "schema.yml"
            manifest_path = self._repo_root / "dbt" / "target" / "manifest.json"
            schema_paths = [schema_path] if schema_path.is_file() else None
            ingested = DbtMetadataIngestor().ingest(
                metrics_path=metrics_path,
                manifest_path=manifest_path if manifest_path.is_file() else None,
                schema_paths=schema_paths,
            )
            self._compiled = SemanticCompiler().compile(ingested)
        return self._compiled

    @property
    def bridge(self) -> MultiLakehouseBridge:
        """Return MultiLakehouseBridge instance."""
        return self._bridge

    def status(self) -> dict[str, Any]:
        """Return gateway metadata, compiled catalog counts, and lakehouse availability."""
        compiled = self.compiled
        discovered = self._bridge.discover()
        return {
            "service": "semantic-bi-nl2sql-gateway",
            "version": __version__,
            "default_mode": self._default_mode,
            "default_dialect": compiled.default_dialect,
            "schema_version": compiled.schema_version,
            "semantic_models_count": len(compiled.models),
            "allowed_joins_count": len(compiled.allowed_joins),
            "certified_metrics_count": sum(1 for m in compiled.metrics.values() if m.certified),
            "blocked_sensitive_columns_count": len(compiled.security.blocked_sensitive_columns),
            "catalog_path": str(self._repo_root / "catalog" / "metrics.yaml"),
            "lakehouses": {
                lh_id: {
                    "available": st.available,
                    "source_mode": st.source_mode,
                    "table_count": len(st.tables),
                }
                for lh_id, st in discovered.items()
            },
        }

    def compile_bi(
        self,
        output_dir: Path | None = None,
        source_mode: SourceMode = "synthetic_embedded",
    ) -> dict[str, Any]:
        """Generate deterministic Power BI artifacts (DAX, TMDL, Power Query M, theme, KPI dict)."""
        validated_dir: Path | None = None
        if output_dir is not None:
            validated_dir = self._bridge.shield.validate_output_path(
                Path(output_dir),
                source_mode=source_mode,
            )

        bundle = PowerBiCodeGenerator().generate(self.compiled, output_dir=validated_dir)
        written_files = (
            [str(validated_dir / name) for name in sorted(bundle.artifacts)]
            if validated_dir is not None
            else []
        )
        return {
            "project_name": self.compiled.project_name,
            "measure_count": bundle.measure_count,
            "model_count": bundle.model_count,
            "metric_count": bundle.metric_count,
            "artifacts": bundle.artifacts,
            "written_files": written_files,
        }

    def validate_sql(self, sql: str) -> dict[str, Any]:
        """Validate untrusted DuckDB SQL through AstSqlFirewall without executing it."""
        firewall = AstSqlFirewall(self.compiled)
        try:
            decision = firewall.validate(sql)
            return {
                "allowed": True,
                "code": "OK",
                "reason": decision.reason,
                "sanitized_sql": decision.sanitized_sql,
                "applied_limit": decision.applied_limit,
                "referenced_tables": list(decision.referenced_tables),
                "referenced_columns": list(decision.referenced_columns),
            }
        except SqlFirewallViolation as exc:
            return {
                "allowed": False,
                "code": exc.code,
                "reason": exc.reason,
                "sanitized_sql": None,
                "applied_limit": None,
                "referenced_tables": [],
                "referenced_columns": [],
            }

    def ask(
        self,
        question: str,
        lakehouse: str | None = None,
        mode: Literal["deterministic", "llm"] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Translate a business question and execute read-only on DuckDB via AstSqlFirewall."""
        compiled = self.compiled
        active_mode = mode or self._default_mode
        firewall = AstSqlFirewall(compiled)
        nl2sql = Nl2SqlGateway(
            compiled,
            mode=active_mode,
            llm_adapter=self._llm_adapter,
        )
        translation = nl2sql.translate(question, mode=active_mode)

        if dry_run:
            return {
                "question": question,
                "mode": translation.mode,
                "dry_run": True,
                "sql": translation.sql,
                "strategy": translation.strategy,
                "metrics": list(translation.metrics),
                "dimensions": list(translation.dimensions),
                "filters": list(translation.filters),
                "applied_limit": translation.firewall_decision.applied_limit,
                "lakehouse_id": None,
                "source_mode": None,
                "fallback_used": False,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "execution_ms": 0.0,
                "result_hash_sha256": None,
            }

        resolved = self._bridge.resolve_database(
            preferred_lakehouse=lakehouse,
            allow_fallback=True,
            auto_bootstrap_synthetic=True,
        )
        executor = ReadOnlyDuckDbExecutor(compiled, firewall=firewall, db_path=resolved.db_path)
        execution = executor.execute(translation.sql)

        return {
            "question": question,
            "mode": translation.mode,
            "dry_run": False,
            "sql": execution.sql,
            "strategy": translation.strategy,
            "metrics": list(translation.metrics),
            "dimensions": list(translation.dimensions),
            "filters": list(translation.filters),
            "applied_limit": execution.firewall_decision.applied_limit,
            "lakehouse_id": resolved.lakehouse_id,
            "source_mode": resolved.source_mode,
            "fallback_used": resolved.fallback_used,
            "columns": list(execution.columns),
            "rows": list(execution.rows),
            "row_count": execution.row_count,
            "execution_ms": execution.execution_ms,
            "result_hash_sha256": execution.result_hash_sha256,
        }

    def run_evals(self) -> dict[str, Any]:
        """Run the deterministic 50-question + 20-adversarial benchmark harness."""
        report = Nl2SqlEvalHarness(self._repo_root).run()
        return report_to_dict(report)

    def kpi_dictionary(self) -> dict[str, Any]:
        """Generate the certified Markdown KPI dictionary from compiled semantic metadata."""
        compiled = self.compiled
        markdown = render_kpi_dictionary(compiled)
        certified = [m for m in compiled.metrics.values() if m.certified]
        return {
            "project_name": compiled.project_name,
            "certified_metrics_count": len(certified),
            "markdown": markdown,
        }
