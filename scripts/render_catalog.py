"""Deterministic renderer and sync checker for CATALOG.md."""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from validate_metrics_schema import validate_catalog  # noqa: E402


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"YAML file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping root in {path}")
    return data


def _validate_dax_templates(data: dict[str, Any]) -> None:
    if not data.get("dax_templates"):
        raise ValueError("catalog/dax_templates.yaml must define 'dax_templates'.")
    if not data.get("power_query_connectors"):
        raise ValueError("catalog/dax_templates.yaml must define 'power_query_connectors'.")


def _validate_benchmark_registry(data: dict[str, Any]) -> None:
    gates = data.get("ci_gates")
    suites = data.get("benchmark_suites")
    if not isinstance(gates, dict) or not isinstance(suites, list) or not suites:
        raise ValueError("catalog/benchmark_registry.yaml missing 'ci_gates' or 'benchmark_suites'.")


def render_markdown(
    metrics_data: dict[str, Any],
    dax_data: dict[str, Any],
    bench_data: dict[str, Any],
) -> str:
    """Render deterministic CATALOG.md from validated catalog YAML files."""
    models = metrics_data["semantic_models"]
    joins = metrics_data["allowed_joins"]
    metrics = metrics_data["metrics"]
    security = metrics_data["security"]
    dax_templates = dax_data["dax_templates"]
    pq_targets = dax_data["power_query_connectors"]
    gates = bench_data["ci_gates"]
    suites = bench_data["benchmark_suites"]

    total_business_q = sum(
        int(s["target_cases"]) for s in suites if s["suite_id"] != "adversarial_firewall"
    )
    total_adversarial = sum(
        int(s["target_cases"]) for s in suites if s["suite_id"] == "adversarial_firewall"
    )

    lines: list[str] = [
        "# Semantic Metrics, Power BI Templates & Benchmark Catalog",
        "",
        "> **Single Source of Truth (SSOT):** Deterministically rendered from "
        "[`catalog/metrics.yaml`](catalog/metrics.yaml), "
        "[`catalog/dax_templates.yaml`](catalog/dax_templates.yaml), and "
        "[`catalog/benchmark_registry.yaml`](catalog/benchmark_registry.yaml). "
        "Do not edit manually.",
        "",
        f"- **Metrics Catalog Version:** `{metrics_data['schema_version']}` "
        f"(Dialect: `{metrics_data['default_dialect']}`)",
        f"- **Semantic Models:** `{len(models)}` "
        f"(`{sum(1 for m in models if m['role'] == 'fact')}` facts, "
        f"`{sum(1 for m in models if m['role'] == 'dimension')}` dimensions)",
        f"- **Allowed Joins (`many_to_one` / `one_to_one`):** `{len(joins)}`",
        f"- **Certified KPIs:** `{len(metrics)}` | **Blocked Sensitive Columns:** `{len(security['blocked_sensitive_columns'])}`",
        f"- **Power BI Templates:** `{len(dax_templates)}` DAX/TMDL patterns + `{len(pq_targets)}` Power Query M connectors",
        f"- **NL2SQL Benchmark Target:** `{total_business_q}` business questions + `{total_adversarial}` adversarial security probes",
        "",
        "---",
        "",
        "## 1. Certified Semantic Models",
        "",
        "| Model | Role | Grain | Primary Key | Time Dimension | Source Lakehouse |",
        "| :--- | :---: | :--- | :--- | :--- | :--- |",
    ]

    for model in models:
        time_dim = f"`{model['time_dimension']}`" if model.get("time_dimension") else "—"
        src = f"`{model.get('source_lakehouse') or 'synthetic_embedded'}`"
        lines.append(
            f"| `{model['name']}` | `{model['role']}` | {model['grain']} | "
            f"`{model['primary_key']}` | {time_dim} | {src} |"
        )

    lines.extend(
        [
            "",
            "## 2. Certified KPI Metrics",
            "",
            "| Metric | Label | Base Model | Type | Formula | Time Intel | Certified |",
            "| :--- | :--- | :--- | :---: | :--- | :---: | :---: |",
        ]
    )

    for metric in metrics:
        formula = metric.get("expression") or f"({metric['numerator']}) / ({metric['denominator']})"
        ti = "Yes" if metric["time_intelligence"] else "No"
        cert = "Yes" if metric["certified"] else "No"
        lines.append(
            f"| `{metric['name']}` | {metric['label']} | `{metric['model']}` | "
            f"`{metric['type']}` | `{formula}` | {ti} | {cert} |"
        )

    lines.extend(
        [
            "",
            "## 3. Allowed Join Graph (Fan-Out & Chasm-Trap Safe)",
            "",
            "| Join Name | Left Model | Right Model | Join Keys | Cardinality | Type |",
            "| :--- | :--- | :--- | :--- | :---: | :---: |",
        ]
    )

    for join in joins:
        lines.append(
            f"| `{join['name']}` | `{join['left_model']}` | `{join['right_model']}` | "
            f"`{join['left_key']} = {join['right_key']}` | `{join['cardinality']}` | `{join['join_type']}` |"
        )

    lines.extend(
        [
            "",
            "## 4. Power BI DAX/TMDL & Power Query M Templates",
            "",
            "| Template ID | Category | Status | Description |",
            "| :--- | :--- | :---: | :--- |",
        ]
    )

    for tpl in dax_templates:
        lines.append(
            f"| `{tpl['template_id']}` | {tpl['category']} | `{tpl['status']}` | {tpl['description']} |"
        )

    lines.extend(
        [
            "",
            "### Power Query M Targets",
            "",
            "| Target | Connector Function | Parameters | Status |",
            "| :--- | :--- | :--- | :---: |",
        ]
    )

    for target in pq_targets:
        params = ", ".join(f"`{p}`" for p in target["parameters"])
        lines.append(
            f"| `{target['target_id']}` | `{target['connector_fn']}` | {params} | `{target['status']}` |"
        )

    lines.extend(
        [
            "",
            "## 5. NL2SQL Benchmark Suites & CI Quality Gates",
            "",
            f"- **Result-Set Hash Match Floor:** `>= {gates['result_set_hash_match_min'] * 100:.0f}%`",
            f"- **AST Equivalence Floor:** `>= {gates['ast_equivalence_min'] * 100:.0f}%`",
            f"- **Adversarial Block Rate Floor:** `== {gates['adversarial_block_rate_min'] * 100:.0f}%`",
            "",
            "| Suite ID | Category | Target Cases | Languages | Status | Description |",
            "| :--- | :--- | :---: | :---: | :---: | :--- |",
        ]
    )

    for suite in suites:
        langs = ", ".join(suite["languages"])
        lines.append(
            f"| `{suite['suite_id']}` | {suite['label']} | {suite['target_cases']} | "
            f"`{langs}` | `{suite['status']}` | {suite['description']} |"
        )

    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Render or verify CATALOG.md determinism."""
    parser = argparse.ArgumentParser(description="Render or check CATALOG.md.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository or worktree root directory.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify CATALOG.md is synchronized without modifying files.",
    )
    args = parser.parse_args(argv)

    root = args.repo_root.resolve()
    ok, errors, _ = validate_catalog(root)
    if not ok:
        print("[FAIL] Invalid catalog/metrics.yaml:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    metrics_data = _load_yaml(root / "catalog" / "metrics.yaml")
    dax_data = _load_yaml(root / "catalog" / "dax_templates.yaml")
    bench_data = _load_yaml(root / "catalog" / "benchmark_registry.yaml")
    _validate_dax_templates(dax_data)
    _validate_benchmark_registry(bench_data)

    rendered = render_markdown(metrics_data, dax_data, bench_data)
    if "\r" in rendered:
        raise ValueError("Rendered CATALOG.md contains CR characters.")
    expected_bytes = rendered.encode("utf-8")
    output_path = root / "CATALOG.md"

    if args.check:
        if not output_path.is_file():
            print(f"[FAIL] Missing {output_path}", file=sys.stderr)
            return 1
        actual_bytes = output_path.read_bytes()
        if actual_bytes != expected_bytes:
            diff = difflib.unified_diff(
                actual_bytes.decode("utf-8", errors="replace").splitlines(),
                rendered.splitlines(),
                fromfile="CATALOG.md (actual)",
                tofile="CATALOG.md (expected)",
                lineterm="",
            )
            print("[FAIL] CATALOG.md is out of sync:", file=sys.stderr)
            for line in diff:
                print(line, file=sys.stderr)
            return 1
        print("[PASS] CATALOG.md is 100% synchronized with catalog/*.yaml.")
        return 0

    output_path.write_bytes(expected_bytes)
    print(f"[OK] Rendered {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
