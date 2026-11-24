"""Command-line interface (`sbg`) for semantic-bi-nl2sql-gateway."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from semantic_bi_gateway._version import __version__
from semantic_bi_gateway.lakehouse import ClientPathShieldError
from semantic_bi_gateway.nl2sql import LlmProviderUnavailableError, SqlFirewallViolation
from semantic_bi_gateway.sdk import SemanticBiGateway
from semantic_bi_gateway.semantic import SemanticCompilationError


def _emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


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

    def _add_repo_root_arg(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--repo-root",
            type=Path,
            default=None,
            help="Optional repository or worktree root path.",
        )
        sub.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Emit output as machine-readable JSON.",
        )

    status_parser = subparsers.add_parser("status", help="Show semantic catalog and gateway status.")
    _add_repo_root_arg(status_parser)

    compile_parser = subparsers.add_parser("compile-bi", help="Generate Power BI DAX, TMDL, M, theme, and KPI dict.")
    compile_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional directory to write generated BI artifacts.",
    )
    _add_repo_root_arg(compile_parser)

    ask_parser = subparsers.add_parser("ask", help="Translate and execute a governed PT/EN business question.")
    ask_parser.add_argument("question", help="Business question in Portuguese or English.")
    ask_parser.add_argument(
        "--lakehouse",
        default=None,
        help="Optional preferred local lakehouse id (defaults to embedded synthetic dataset).",
    )
    ask_parser.add_argument(
        "--mode",
        choices=("deterministic", "llm"),
        default="deterministic",
        help="Translation mode (default: deterministic offline).",
    )
    ask_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Translate and validate SQL via AST firewall without executing on DuckDB.",
    )
    _add_repo_root_arg(ask_parser)

    val_parser = subparsers.add_parser("validate-sql", help="Validate untrusted DuckDB SQL through AstSqlFirewall.")
    val_parser.add_argument("sql", help="SQL query string to validate.")
    _add_repo_root_arg(val_parser)

    eval_parser = subparsers.add_parser("eval", help="Run the 50-question NL2SQL + 20-adversarial benchmark.")
    _add_repo_root_arg(eval_parser)

    kpi_parser = subparsers.add_parser("kpi-dict", help="Generate Markdown KPI dictionary from certified catalog.")
    kpi_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output Markdown file path.",
    )
    _add_repo_root_arg(kpi_parser)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for `sbg`."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command is None:
        parser.print_help()
        return 0

    gateway = SemanticBiGateway(repo_root=args.repo_root)

    if args.command == "status":
        status = gateway.status()
        if args.as_json:
            _emit_json(status)
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

    if args.command == "compile-bi":
        try:
            result = gateway.compile_bi(output_dir=args.output_dir)
        except ClientPathShieldError as exc:
            if args.as_json:
                _emit_json({"error": "CLIENT_PATH_SHIELD", "reason": str(exc)})
            else:
                print(f"[BLOCKED] {exc}", file=sys.stderr)
            return 2

        if args.as_json:
            _emit_json(result)
            return 0
        print(
            f"[OK] Generated {len(result['artifacts'])} BI artifacts "
            f"({result['measure_count']} DAX measures, {result['metric_count']} certified KPIs)."
        )
        if result["written_files"]:
            print(f"Output directory: {Path(result['written_files'][0]).parent}")
        return 0

    if args.command == "ask":
        try:
            result = gateway.ask(
                question=args.question,
                lakehouse=args.lakehouse,
                mode=args.mode,
                dry_run=args.dry_run,
            )
        except SqlFirewallViolation as exc:
            if args.as_json:
                _emit_json({"error": exc.code, "reason": exc.reason})
            else:
                print(f"[BLOCKED:{exc.code}] {exc.reason}", file=sys.stderr)
            return 2
        except (SemanticCompilationError, LlmProviderUnavailableError) as exc:
            if args.as_json:
                _emit_json({"error": type(exc).__name__, "reason": str(exc)})
            else:
                print(f"[ERROR:{type(exc).__name__}] {exc}", file=sys.stderr)
            return 2

        if args.as_json:
            _emit_json(result)
            return 0
        print(f"SQL ({result['strategy']}): {result['sql']}")
        if not result["dry_run"]:
            print(f"Rows ({result['row_count']}): {json.dumps(result['rows'], ensure_ascii=False)}")
        return 0

    if args.command == "validate-sql":
        result = gateway.validate_sql(args.sql)
        if args.as_json:
            _emit_json(result)
        elif result["allowed"]:
            print(f"[ALLOWED] Limit={result['applied_limit']} | SQL: {result['sanitized_sql']}")
        else:
            print(f"[BLOCKED:{result['code']}] {result['reason']}", file=sys.stderr)
        return 0 if result["allowed"] else 2

    if args.command == "eval":
        result = gateway.run_evals()
        if args.as_json:
            _emit_json(result)
        else:
            print(
                f"[EVAL] GatesPassed={result['gates_passed']} | "
                f"HashMatch={result['hash_matches']}/{result['total_business_cases']} | "
                f"ASTMatch={result['ast_matches']}/{result['total_business_cases']} | "
                f"AdversarialBlocked={result['adversarial_blocked']}/{result['total_adversarial_cases']}"
            )
        return 0 if result["gates_passed"] else 1

    if args.command == "kpi-dict":
        result = gateway.kpi_dictionary()
        if args.output is not None:
            try:
                safe_path = gateway.bridge.shield.validate_output_path(args.output)
            except ClientPathShieldError as exc:
                if args.as_json:
                    _emit_json({"error": "CLIENT_PATH_SHIELD", "reason": str(exc)})
                else:
                    print(f"[BLOCKED] {exc}", file=sys.stderr)
                return 2
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            safe_path.write_bytes(result["markdown"].encode("utf-8"))
        if args.as_json:
            _emit_json(result)
        else:
            print(result["markdown"])
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
