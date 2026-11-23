"""Ingestor for dbt manifest.json, schema.yml files, and catalog/metrics.yaml."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from semantic_bi_gateway.semantic.models import (
    ColumnSpec,
    DbtDiscoveredModel,
    DbtMetadataSnapshot,
    ForeignKeySpec,
    IngestedMetadata,
    JoinEdge,
    MetricSpec,
    ModelSpec,
    SecurityPolicy,
    SemanticCompilationError,
    UncertifiedEntityError,
)

_REF_RE = re.compile(r"ref\(\s*['\"]([a-zA-Z0-9_]+)['\"]\s*\)")


def _extract_ref_model(raw_expr: Any) -> str | None:
    if not isinstance(raw_expr, str):
        return None
    match = _REF_RE.search(raw_expr)
    if match:
        return match.group(1)
    cleaned = raw_expr.strip()
    return cleaned or None


def _parse_relationships_from_tests(
    model_name: str,
    col_name: str,
    tests: Any,
    source_label: str,
) -> list[ForeignKeySpec]:
    if not isinstance(tests, list):
        return []
    fks: list[ForeignKeySpec] = []
    for test_item in tests:
        if not isinstance(test_item, dict) or "relationships" not in test_item:
            continue
        rel_cfg = test_item["relationships"]
        if not isinstance(rel_cfg, dict):
            continue
        # Support both dbt <=1.9 (top-level to/field) and dbt >=1.10 (arguments.{to,field}).
        args = rel_cfg.get("arguments", rel_cfg)
        if not isinstance(args, dict):
            continue
        to_model = _extract_ref_model(args.get("to"))
        to_col = args.get("field")
        if to_model and isinstance(to_col, str) and to_col.strip():
            fks.append(
                ForeignKeySpec(
                    from_model=model_name,
                    from_column=col_name,
                    to_model=to_model,
                    to_column=to_col.strip(),
                    source=source_label,
                )
            )
    return fks


class DbtMetadataIngestor:
    """Reads dbt manifest.json / schema.yml and merges metadata with catalog/metrics.yaml."""

    def extract_from_manifest(self, manifest_path: Path) -> DbtMetadataSnapshot:
        """Extract models, column descriptions, and FK relationships from dbt manifest.json."""
        if not manifest_path.is_file():
            raise FileNotFoundError(f"dbt manifest.json not found: {manifest_path}")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        nodes = manifest.get("nodes", {})
        if not isinstance(nodes, dict):
            raise SemanticCompilationError("Invalid dbt manifest.json: 'nodes' must be a mapping.")

        models: dict[str, DbtDiscoveredModel] = {}
        foreign_keys: list[ForeignKeySpec] = []

        for unique_id, node in sorted(nodes.items()):
            if not isinstance(node, dict):
                continue
            resource_type = node.get("resource_type")
            if resource_type == "model":
                name = str(node.get("name", "")).strip()
                if not name:
                    continue
                cols_raw = node.get("columns", {})
                cols: dict[str, str] = {}
                if isinstance(cols_raw, dict):
                    for col_name, col_meta in cols_raw.items():
                        desc = str(col_meta.get("description", "")).strip() if isinstance(col_meta, dict) else ""
                        cols[str(col_name)] = desc
                models[name] = DbtDiscoveredModel(
                    name=name,
                    description=str(node.get("description", "")).strip(),
                    columns=cols,
                    unique_id=unique_id,
                )
            elif resource_type == "test":
                fk = self._extract_fk_from_manifest_test(node)
                if fk is not None:
                    foreign_keys.append(fk)

        deduped_fks = tuple(sorted(set(foreign_keys), key=lambda k: (k.from_model, k.from_column, k.to_model)))
        return DbtMetadataSnapshot(models=models, foreign_keys=deduped_fks)

    def _extract_fk_from_manifest_test(self, node: dict[str, Any]) -> ForeignKeySpec | None:
        meta = node.get("test_metadata")
        if not isinstance(meta, dict) or meta.get("name") != "relationships":
            return None

        kwargs = meta.get("kwargs", {})
        if not isinstance(kwargs, dict):
            return None

        from_model = _extract_ref_model(kwargs.get("model"))
        if not from_model:
            file_key = str(node.get("file_key_name", ""))
            if "." in file_key:
                from_model = file_key.split(".", 1)[1]

        to_model = _extract_ref_model(kwargs.get("to"))
        from_col = kwargs.get("column_name") or node.get("column_name")
        to_col = kwargs.get("field")

        if not (from_model and to_model and isinstance(from_col, str) and isinstance(to_col, str)):
            return None

        return ForeignKeySpec(
            from_model=from_model.strip(),
            from_column=from_col.strip(),
            to_model=to_model.strip(),
            to_column=to_col.strip(),
            source="dbt_manifest",
        )

    def extract_from_schema_yml(self, schema_paths: Sequence[Path]) -> DbtMetadataSnapshot:
        """Extract models, column descriptions, and FK tests directly from dbt schema YAML files."""
        models: dict[str, DbtDiscoveredModel] = {}
        foreign_keys: list[ForeignKeySpec] = []

        for schema_path in schema_paths:
            if not schema_path.is_file():
                raise FileNotFoundError(f"dbt schema YAML not found: {schema_path}")
            payload = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            for model_entry in payload.get("models", []) or []:
                if not isinstance(model_entry, dict):
                    continue
                model_name = str(model_entry.get("name", "")).strip()
                if not model_name:
                    continue
                cols: dict[str, str] = {}
                for col_entry in model_entry.get("columns", []) or []:
                    if not isinstance(col_entry, dict):
                        continue
                    col_name = str(col_entry.get("name", "")).strip()
                    if not col_name:
                        continue
                    cols[col_name] = str(col_entry.get("description", "")).strip()
                    for test_key in ("data_tests", "tests"):
                        foreign_keys.extend(
                            _parse_relationships_from_tests(
                                model_name,
                                col_name,
                                col_entry.get(test_key),
                                source_label=f"dbt_schema:{schema_path.name}",
                            )
                        )
                models[model_name] = DbtDiscoveredModel(
                    name=model_name,
                    description=str(model_entry.get("description", "")).strip(),
                    columns=cols,
                )

        deduped_fks = tuple(sorted(set(foreign_keys), key=lambda k: (k.from_model, k.from_column, k.to_model)))
        return DbtMetadataSnapshot(models=models, foreign_keys=deduped_fks)

    def load_catalog(self, metrics_path: Path) -> dict[str, Any]:
        """Load and return parsed YAML mapping from catalog/metrics.yaml."""
        if not metrics_path.is_file():
            raise FileNotFoundError(f"Semantic metrics catalog not found: {metrics_path}")
        payload = yaml.safe_load(metrics_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise SemanticCompilationError(f"Expected YAML mapping root in {metrics_path}")
        return payload

    def ingest(
        self,
        metrics_path: Path,
        manifest_path: Path | None = None,
        schema_paths: Sequence[Path] | None = None,
    ) -> IngestedMetadata:
        """Load metrics.yaml and optionally merge/verify dbt manifest.json and schema.yml metadata."""
        catalog = self.load_catalog(metrics_path)
        snapshot = self._build_snapshot(manifest_path=manifest_path, schema_paths=schema_paths)

        sec_raw = catalog.get("security", {})
        patterns: list[re.Pattern[str]] = []
        for raw_pat in sec_raw.get("blocked_column_patterns", []):
            try:
                patterns.append(re.compile(raw_pat))
            except re.error as exc:
                raise SemanticCompilationError(
                    f"Invalid blocked_column_patterns regex '{raw_pat}': {exc}"
                ) from exc

        security = SecurityPolicy(
            default_limit=int(sec_raw.get("default_limit", 100)),
            max_limit=int(sec_raw.get("max_limit", 1000)),
            query_timeout_seconds=float(sec_raw.get("query_timeout_seconds", 10)),
            blocked_sensitive_columns=frozenset(
                str(col).lower() for col in sec_raw.get("blocked_sensitive_columns", [])
            ),
            blocked_column_patterns=tuple(patterns),
            allowed_functions=frozenset(
                str(fn).upper() for fn in sec_raw.get("allowed_functions", [])
            ),
        )

        models: dict[str, ModelSpec] = {}
        for raw_model in catalog.get("semantic_models", []):
            model_name = str(raw_model["name"])
            dbt_model = snapshot.models.get(model_name) if snapshot is not None else None
            if snapshot is not None and dbt_model is None:
                raise UncertifiedEntityError(
                    f"Catalog model '{model_name}' was not found in ingested dbt metadata."
                )

            columns: list[ColumnSpec] = []
            for raw_col in raw_model.get("columns", []):
                col_name = str(raw_col["name"])
                desc = str(raw_col.get("description", "")).strip()
                if not desc and dbt_model is not None:
                    desc = dbt_model.columns.get(col_name, "")
                columns.append(
                    ColumnSpec(
                        name=col_name,
                        data_type=str(raw_col["type"]),
                        role=str(raw_col["role"]),
                        description=desc,
                        sensitive=bool(raw_col.get("sensitive", False)),
                        synonyms=tuple(str(s) for s in raw_col.get("synonyms", [])),
                    )
                )

            model_desc = str(raw_model.get("description", "")).strip()
            if not model_desc and dbt_model is not None:
                model_desc = dbt_model.description

            models[model_name] = ModelSpec(
                name=model_name,
                role=raw_model["role"],
                table=str(raw_model["table"]),
                grain=str(raw_model["grain"]),
                primary_key=str(raw_model["primary_key"]),
                time_dimension=raw_model.get("time_dimension"),
                description=model_desc,
                columns=tuple(columns),
                source_lakehouse=raw_model.get("source_lakehouse"),
            )

        joins = tuple(
            JoinEdge(
                name=str(j["name"]),
                left_model=str(j["left_model"]),
                right_model=str(j["right_model"]),
                left_key=str(j["left_key"]),
                right_key=str(j["right_key"]),
                join_type=j["join_type"],
                cardinality=str(j["cardinality"]),
                safe_for_aggregation=bool(j["safe_for_aggregation"]),
            )
            for j in catalog.get("allowed_joins", [])
        )

        metrics = {
            str(m["name"]): MetricSpec(
                name=str(m["name"]),
                label=str(m["label"]),
                description=str(m["description"]),
                model=str(m["model"]),
                metric_type=m["type"],
                format=str(m["format"]),
                time_intelligence=bool(m["time_intelligence"]),
                dimensions=tuple(str(d) for d in m.get("dimensions", [])),
                owner=str(m["owner"]),
                certified=bool(m["certified"]),
                expression=m.get("expression"),
                numerator=m.get("numerator"),
                denominator=m.get("denominator"),
                target_expression=m.get("target_expression"),
                display_folder=m.get("display_folder"),
                synonyms=tuple(str(s) for s in m.get("synonyms", [])),
            )
            for m in catalog.get("metrics", [])
        }

        return IngestedMetadata(
            schema_version=str(catalog["schema_version"]),
            project_name=str(catalog["project_name"]),
            default_dialect=str(catalog["default_dialect"]),
            security=security,
            models=models,
            allowed_joins=joins,
            metrics=metrics,
            dbt_snapshot=snapshot,
        )

    def _build_snapshot(
        self,
        manifest_path: Path | None,
        schema_paths: Sequence[Path] | None,
    ) -> DbtMetadataSnapshot | None:
        snapshots: list[DbtMetadataSnapshot] = []
        if manifest_path is not None:
            snapshots.append(self.extract_from_manifest(manifest_path))
        if schema_paths:
            snapshots.append(self.extract_from_schema_yml(schema_paths))
        if not snapshots:
            return None

        merged_models: dict[str, DbtDiscoveredModel] = {}
        merged_fks: list[ForeignKeySpec] = []
        for snap in snapshots:
            for name, model in snap.models.items():
                if name not in merged_models:
                    merged_models[name] = model
                    continue
                existing = merged_models[name]
                combined_cols = {**existing.columns}
                for col_name, col_desc in model.columns.items():
                    if not combined_cols.get(col_name) and col_desc:
                        combined_cols[col_name] = col_desc
                merged_models[name] = replace(
                    existing,
                    description=existing.description or model.description,
                    columns=combined_cols,
                    unique_id=existing.unique_id or model.unique_id,
                )
            merged_fks.extend(snap.foreign_keys)

        deduped_fks = tuple(
            sorted(
                {(fk.from_model, fk.from_column, fk.to_model, fk.to_column): fk for fk in merged_fks}.values(),
                key=lambda fk: (fk.from_model, fk.from_column, fk.to_model),
            )
        )
        return DbtMetadataSnapshot(models=merged_models, foreign_keys=deduped_fks)
