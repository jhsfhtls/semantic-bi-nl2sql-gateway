"""Linter ensuring 100% column and model documentation coverage in the dbt project."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def lint_manifest(manifest_path: Path, project_name: str = "sbg_semantic_demo") -> tuple[bool, dict[str, int]]:
    """Verify every model and column in dbt manifest.json has a non-empty description."""
    if not manifest_path.is_file():
        print(
            f"[ERROR] dbt manifest not found at {manifest_path}. Run 'dbt docs generate' first.",
            file=sys.stderr,
        )
        return False, {}

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    nodes = manifest.get("nodes", {})
    prefix = f"model.{project_name}."

    total_models = 0
    total_columns = 0
    undocumented_models: list[str] = []
    undocumented_columns: list[str] = []

    for unique_id, node in nodes.items():
        if not unique_id.startswith(prefix):
            continue
        model_name = node.get("name", unique_id)
        total_models += 1

        if not str(node.get("description", "")).strip():
            undocumented_models.append(model_name)

        for col_name, col_meta in node.get("columns", {}).items():
            total_columns += 1
            if not str(col_meta.get("description", "")).strip():
                undocumented_columns.append(f"{model_name}.{col_name}")

    print("=================================================================")
    print("dbt Documentation & Metadata Governance Linter (sbg_semantic_demo)")
    print("=================================================================")
    print(f"Total Models Evaluated:  {total_models}")
    print(f"Total Columns Evaluated: {total_columns}")

    if total_models == 0:
        print(f"[FAIL] No models found with prefix '{prefix}'.", file=sys.stderr)
        return False, {}

    has_errors = False
    if undocumented_models:
        print(f"\n[FAIL] Found {len(undocumented_models)} undocumented models:")
        for model_name in undocumented_models:
            print(f"  - {model_name}")
        has_errors = True

    if undocumented_columns:
        print(f"\n[FAIL] Found {len(undocumented_columns)} undocumented columns:")
        for col_ref in undocumented_columns:
            print(f"  - {col_ref}")
        has_errors = True

    if has_errors:
        print("\nResult: REJECTED - 100% model and column description coverage is mandatory.", file=sys.stderr)
        return False, {"models": total_models, "columns": total_columns}

    print("\nResult: APPROVED - 100% Documentation Coverage across all models and columns!")
    print("=================================================================")
    return True, {"models": total_models, "columns": total_columns}


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for dbt documentation coverage verification."""
    parser = argparse.ArgumentParser(description="Lint dbt manifest documentation coverage.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository or worktree root directory.",
    )
    args = parser.parse_args(argv)
    manifest_path = args.repo_root.resolve() / "dbt" / "target" / "manifest.json"
    ok, _ = lint_manifest(manifest_path)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
