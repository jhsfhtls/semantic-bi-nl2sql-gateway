"""Validate catalog/metrics.yaml against catalog/metrics_schema.json and semantic invariants."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

_SQL_IDENT_RE = re.compile(r"\b[a-z_][a-z0-9_]*\b", re.IGNORECASE)
_SQL_KEYWORDS = frozenset(
    {
        "sum",
        "count",
        "avg",
        "min",
        "max",
        "distinct",
        "nullif",
        "coalesce",
        "case",
        "when",
        "then",
        "else",
        "end",
        "cast",
        "as",
        "and",
        "or",
        "not",
        "in",
        "is",
        "null",
    }
)


def check_utf8_lf(file_path: Path) -> list[str]:
    """Return encoding/line-ending violation messages for a file."""
    if not file_path.is_file():
        return [f"Missing file: {file_path}"]
    raw = file_path.read_bytes()
    errors: list[str] = []
    if raw.startswith(b"\xef\xbb\xbf"):
        errors.append(f"{file_path.name}: UTF-8 BOM detected (Zero-BOM required).")
    if b"\r" in raw:
        errors.append(f"{file_path.name}: CR/CRLF line ending detected (LF required).")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        errors.append(f"{file_path.name}: invalid UTF-8 ({exc}).")
    return errors


def _compile_blocked_patterns(patterns: list[str], errors: list[str]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for raw_pattern in patterns:
        try:
            compiled.append(re.compile(raw_pattern))
        except re.error as exc:
            errors.append(f"Invalid blocked_column_patterns regex '{raw_pattern}': {exc}")
    return compiled


def _is_sensitive_identifier(
    name: str,
    blocked_exact: set[str],
    blocked_patterns: list[re.Pattern[str]],
) -> bool:
    lowered = name.lower()
    if lowered in blocked_exact:
        return True
    return any(pattern.search(lowered) for pattern in blocked_patterns)


def _validate_models(
    models: list[dict[str, Any]],
    blocked_exact: set[str],
    blocked_patterns: list[re.Pattern[str]],
    errors: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]]]:
    models_by_name: dict[str, dict[str, Any]] = {}
    columns_by_model: dict[str, set[str]] = {}
    seen_tables: set[str] = set()

    for model in models:
        name = model["name"]
        table = model["table"]
        if name in models_by_name:
            errors.append(f"Duplicate semantic_model name: '{name}'.")
            continue
        if table in seen_tables:
            errors.append(f"Duplicate physical table mapping '{table}' in semantic_models.")
        seen_tables.add(table)
        models_by_name[name] = model

        col_names: set[str] = set()
        for col in model["columns"]:
            col_name = col["name"]
            if col_name in col_names:
                errors.append(f"Model '{name}' has duplicate column '{col_name}'.")
            col_names.add(col_name)
            if col.get("sensitive", False) or _is_sensitive_identifier(
                col_name, blocked_exact, blocked_patterns
            ):
                errors.append(
                    f"Model '{name}' exposes sensitive/blocked column '{col_name}' in certified catalog."
                )

        columns_by_model[name] = col_names
        if model["primary_key"] not in col_names:
            errors.append(f"Model '{name}' primary_key '{model['primary_key']}' not found in columns.")

        time_dim = model.get("time_dimension")
        if time_dim is not None and time_dim not in col_names:
            errors.append(f"Model '{name}' time_dimension '{time_dim}' not found in columns.")

    return models_by_name, columns_by_model


def _validate_joins(
    joins: list[dict[str, Any]],
    models_by_name: dict[str, dict[str, Any]],
    columns_by_model: dict[str, set[str]],
    errors: list[str],
) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {name: set() for name in models_by_name}
    seen_names: set[str] = set()

    for join in joins:
        join_name = join["name"]
        if join_name in seen_names:
            errors.append(f"Duplicate allowed_join name: '{join_name}'.")
        seen_names.add(join_name)

        left_model = join["left_model"]
        right_model = join["right_model"]
        if left_model not in models_by_name:
            errors.append(f"Join '{join_name}': unknown left_model '{left_model}'.")
            continue
        if right_model not in models_by_name:
            errors.append(f"Join '{join_name}': unknown right_model '{right_model}'.")
            continue
        if left_model == right_model:
            errors.append(f"Join '{join_name}': self-join on '{left_model}' is disallowed.")

        left_role = models_by_name[left_model]["role"]
        right_role = models_by_name[right_model]["role"]
        if left_role == "fact" and right_role == "fact":
            errors.append(
                f"Join '{join_name}': direct fact-to-fact join ({left_model} -> {right_model}) "
                "violates chasm-trap invariant."
            )

        if join["left_key"] not in columns_by_model[left_model]:
            errors.append(
                f"Join '{join_name}': left_key '{join['left_key']}' missing from '{left_model}'."
            )
        if join["right_key"] not in columns_by_model[right_model]:
            errors.append(
                f"Join '{join_name}': right_key '{join['right_key']}' missing from '{right_model}'."
            )
        if not join["safe_for_aggregation"]:
            errors.append(f"Join '{join_name}': safe_for_aggregation must be true.")

        adjacency[left_model].add(right_model)

    return adjacency


def _reachable_models(start_model: str, adjacency: dict[str, set[str]]) -> set[str]:
    visited: set[str] = {start_model}
    stack = [start_model]
    while stack:
        current = stack.pop()
        for neighbor in adjacency.get(current, ()):
            if neighbor not in visited:
                visited.add(neighbor)
                stack.append(neighbor)
    return visited


def _validate_metrics(
    metrics: list[dict[str, Any]],
    models_by_name: dict[str, dict[str, Any]],
    columns_by_model: dict[str, set[str]],
    adjacency: dict[str, set[str]],
    blocked_exact: set[str],
    blocked_patterns: list[re.Pattern[str]],
    errors: list[str],
) -> None:
    seen_names: set[str] = set()

    for metric in metrics:
        name = metric["name"]
        if name in seen_names:
            errors.append(f"Duplicate metric name: '{name}'.")
        seen_names.add(name)

        base_model = metric["model"]
        if base_model not in models_by_name:
            errors.append(f"Metric '{name}': unknown base model '{base_model}'.")
            continue

        reachable = _reachable_models(base_model, adjacency)
        for dim_ref in metric["dimensions"]:
            dim_model, dim_col = dim_ref.split(".", 1)
            if dim_model not in models_by_name:
                errors.append(f"Metric '{name}': dimension model '{dim_model}' not found.")
                continue
            if dim_col not in columns_by_model[dim_model]:
                errors.append(
                    f"Metric '{name}': dimension column '{dim_col}' missing from '{dim_model}'."
                )
            if dim_model not in reachable:
                errors.append(
                    f"Metric '{name}': dimension model '{dim_model}' is unreachable from '{base_model}' "
                    "via allowed_joins."
                )

        formula_fields = ("expression", "numerator", "denominator", "target_expression")
        for field in formula_fields:
            raw_formula = metric.get(field)
            if not raw_formula:
                continue
            for token in _SQL_IDENT_RE.findall(raw_formula):
                lowered = token.lower()
                if lowered in _SQL_KEYWORDS or lowered.isdigit():
                    continue
                if _is_sensitive_identifier(lowered, blocked_exact, blocked_patterns):
                    errors.append(
                        f"Metric '{name}' ({field}) references blocked sensitive identifier '{token}'."
                    )


def validate_catalog(repo_root: Path) -> tuple[bool, list[str], dict[str, int]]:
    """Validate schema, encoding, and semantic invariants for catalog/metrics.yaml."""
    schema_path = repo_root / "catalog" / "metrics_schema.json"
    metrics_path = repo_root / "catalog" / "metrics.yaml"

    errors: list[str] = []
    errors.extend(check_utf8_lf(schema_path))
    errors.extend(check_utf8_lf(metrics_path))
    if errors:
        return False, errors, {}

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    catalog = yaml.safe_load(metrics_path.read_text(encoding="utf-8"))

    validator = Draft202012Validator(schema)
    schema_errors = sorted(validator.iter_errors(catalog), key=lambda err: list(err.path))
    for err in schema_errors:
        loc = ".".join(str(part) for part in err.path) or "<root>"
        errors.append(f"Schema error at {loc}: {err.message}")
    if errors:
        return False, errors, {}

    security = catalog["security"]
    if security["default_limit"] > security["max_limit"]:
        errors.append("security.default_limit cannot exceed security.max_limit.")

    blocked_exact = {col.lower() for col in security["blocked_sensitive_columns"]}
    blocked_patterns = _compile_blocked_patterns(security["blocked_column_patterns"], errors)

    models_by_name, columns_by_model = _validate_models(
        catalog["semantic_models"], blocked_exact, blocked_patterns, errors
    )
    adjacency = _validate_joins(catalog["allowed_joins"], models_by_name, columns_by_model, errors)
    _validate_metrics(
        catalog["metrics"],
        models_by_name,
        columns_by_model,
        adjacency,
        blocked_exact,
        blocked_patterns,
        errors,
    )

    stats = {
        "models": len(catalog["semantic_models"]),
        "joins": len(catalog["allowed_joins"]),
        "metrics": len(catalog["metrics"]),
        "blocked_sensitive_columns": len(security["blocked_sensitive_columns"]),
    }
    return len(errors) == 0, errors, stats


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for semantic metrics catalog validation."""
    parser = argparse.ArgumentParser(description="Validate catalog/metrics.yaml contract.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository or worktree root directory.",
    )
    args = parser.parse_args(argv)

    ok, errors, stats = validate_catalog(args.repo_root.resolve())
    if not ok:
        print("[FAIL] Metrics catalog validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(
        "[PASS] catalog/metrics.yaml validated against catalog/metrics_schema.json: "
        f"{stats['models']} models, {stats['joins']} allowed joins, "
        f"{stats['metrics']} certified metrics, "
        f"{stats['blocked_sensitive_columns']} blocked sensitive columns."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
