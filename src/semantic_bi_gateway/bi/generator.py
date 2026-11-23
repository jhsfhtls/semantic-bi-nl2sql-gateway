"""Orchestrator generating all deterministic Power BI artifacts from compiled semantic models."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from semantic_bi_gateway.bi.dax_generator import build_dax_measures, render_dax, render_tmdl
from semantic_bi_gateway.bi.kpi_dictionary import render_kpi_dictionary
from semantic_bi_gateway.bi.power_query_generator import generate_power_query
from semantic_bi_gateway.bi.theme_generator import (
    render_corporate_theme_json,
    validate_wcag_aa_theme,
)
from semantic_bi_gateway.semantic.models import CompiledSemanticModel


@dataclass(frozen=True)
class BiArtifactBundle:
    """Deterministic collection of generated Power BI artifacts."""

    artifacts: dict[str, str]
    measure_count: int
    model_count: int
    metric_count: int


class PowerBiCodeGenerator:
    """Generates DAX, TMDL, Power Query M, WCAG AA theme, and KPI dictionary artifacts."""

    def generate(
        self,
        compiled: CompiledSemanticModel,
        output_dir: Path | None = None,
    ) -> BiArtifactBundle:
        """Generate all BI artifacts deterministically and optionally write to output_dir."""
        measures = build_dax_measures(compiled)
        theme_json = render_corporate_theme_json(compiled.project_name)
        theme_ok, theme_errors = validate_wcag_aa_theme(json.loads(theme_json))
        if not theme_ok:
            raise ValueError(f"Generated corporate theme failed WCAG AA validation: {theme_errors}")

        artifacts = {
            "corporate_theme.json": theme_json,
            "kpi_dictionary.md": render_kpi_dictionary(compiled),
            "measures.dax": render_dax(measures),
            "measures.tmdl": render_tmdl(measures),
            "power_query_databricks.pq": generate_power_query(compiled, "databricks"),
            "power_query_duckdb.pq": generate_power_query(compiled, "duckdb"),
            "power_query_snowflake.pq": generate_power_query(compiled, "snowflake"),
        }

        for filename, content in artifacts.items():
            if "\r" in content:
                raise ValueError(f"Generated artifact '{filename}' contains CR characters.")

        if output_dir is not None:
            resolved_dir = output_dir.resolve()
            resolved_dir.mkdir(parents=True, exist_ok=True)
            for filename, content in artifacts.items():
                (resolved_dir / filename).write_bytes(content.encode("utf-8"))

        certified_count = sum(1 for m in compiled.metrics.values() if m.certified)
        return BiArtifactBundle(
            artifacts=artifacts,
            measure_count=len(measures),
            model_count=len(compiled.models),
            metric_count=certified_count,
        )
