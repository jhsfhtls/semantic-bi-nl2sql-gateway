"""Command-line interface (`sbg`) for semantic-bi-nl2sql-gateway."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from semantic_bi_gateway import __version__


def resolve_repo_root(explicit_root: Path | None = None) -> Path:
    """Resolve repository root safely across editable installs and git worktrees."""
    if explicit_root is not None:
        return explicit_root.resolve()
    cwd_root = Path.cwd().resolve()
    if (cwd_root / "catalog" / "metrics.yaml").is_file():
        return cwd_root
    package_root = Path(__file__).resolve().parents[3]
    return package_root


def collect_status(repo_root: Path) -> dict[str, Any]:
    """Read certified catalog metadata and return gateway status payload."""
    metrics_path = repo_root / "catalog" / "metrics.yaml"
    if not metrics_path.is_file():
        raise FileNotFoundError(f"Semantic catalog not found: {metrics_path}")

    catalog = yaml.safe_load(metrics_path.read_text(encoding="utf-8"))
    security = catalog.get("security", {})
    return {
        "service": "semantic-bi-nl2sql-gateway",
        "version": __version__,
        "default_mode": "deterministic",
        "default_dialect": catalog.get("default_dialect", "duckdb"),
        "schema_version": catalog.get("schema_version", "unknown"),
        "semantic_models_count": len(catalog.get("semantic_models", [])),
        "allowed_joins_count": len(catalog.get("allowed_joins", [])),
        "certified_metrics_count": len(catalog.get("metrics", [])),
        "blocked_sensitive_columns_count": len(security.get("blocked_sensitive_columns", [])),
        "catalog_path": str(metrics_path),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sbg",
        description="Semantic BI compiler and AST-guarded NL2SQL gateway CLI.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"sbg {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    status_parser = subparsers.add_parser("status", help="Show semantic catalog and gateway status.")
    status_parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Optional repository/worktree root path.",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit status as machine-readable JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for `sbg`."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "status":
        repo_root = resolve_repo_root(args.repo_root)
        status = collect_status(repo_root)
        if args.as_json:
            print(json.dumps(status, indent=2))
            return 0
        print(
            f"semantic-bi-nl2sql-gateway v{status['version']} "
            f"[mode={status['default_mode']}, dialect={status['default_dialect']}]"
        )
        print(
            f"Catalog: {status['semantic_models_count']} models | "
            f"{status['allowed_joins_count']} allowed joins | "
            f"{status['certified_metrics_count']} certified metrics | "
            f"{status['blocked_sensitive_columns_count']} blocked sensitive columns"
        )
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
