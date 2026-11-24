"""CLI runner and CI gate for deterministic NL2SQL accuracy and adversarial firewall evals."""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

from semantic_bi_gateway.evals import (
    Nl2SqlEvalHarness,
    render_eval_report_markdown,
    report_to_dict,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    """Execute NL2SQL benchmark harness, enforce CI gates, and render/check EVAL_REPORT.md."""
    parser = argparse.ArgumentParser(description="Run NL2SQL benchmark and adversarial evals.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository or worktree root directory.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Enforce CI gates and verify benchmarks/EVAL_REPORT.md is synchronized.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print JSON summary to stdout.",
    )
    args = parser.parse_args(argv)

    root = args.repo_root.resolve()
    harness = Nl2SqlEvalHarness(root)
    report = harness.run()
    rendered_md = render_eval_report_markdown(report)
    if "\r" in rendered_md:
        raise ValueError("Rendered EVAL_REPORT.md contains CR characters.")
    expected_bytes = rendered_md.encode("utf-8")
    report_path = root / "benchmarks" / "EVAL_REPORT.md"

    if args.as_json:
        print(json.dumps(report_to_dict(report), indent=2))
    else:
        print(
            f"[EVAL] Mode={report.mode} | "
            f"HashMatch={report.hash_matches}/{report.total_business_cases} "
            f"({report.result_set_hash_match_rate * 100:.1f}%) | "
            f"ASTMatch={report.ast_matches}/{report.total_business_cases} "
            f"({report.ast_equivalence_rate * 100:.1f}%) | "
            f"AdversarialBlocked={report.adversarial_blocked}/{report.total_adversarial_cases} "
            f"({report.adversarial_block_rate * 100:.1f}%)"
        )

    if not report.gates_passed:
        print("[FAIL] NL2SQL evaluation gates did not meet minimum CI floors.", file=sys.stderr)
        for item in report.business_outcomes:
            if not (item.ast_match and item.result_hash_match):
                print(
                    f"  - {item.case_id}: ast={item.ast_match}, hash={item.result_hash_match}, err={item.error}",
                    file=sys.stderr,
                )
        for adv in report.adversarial_outcomes:
            if not adv.blocked:
                print(f"  - {adv.case_id}: BYPASSED ({adv.payload})", file=sys.stderr)
        return 1

    if args.check:
        if not report_path.is_file():
            print(f"[FAIL] Missing {report_path}", file=sys.stderr)
            return 1
        actual_bytes = report_path.read_bytes()
        if actual_bytes != expected_bytes:
            diff = difflib.unified_diff(
                actual_bytes.decode("utf-8", errors="replace").splitlines(),
                rendered_md.splitlines(),
                fromfile="benchmarks/EVAL_REPORT.md (actual)",
                tofile="benchmarks/EVAL_REPORT.md (expected)",
                lineterm="",
            )
            print("[FAIL] benchmarks/EVAL_REPORT.md is out of sync:", file=sys.stderr)
            for line in diff:
                print(line, file=sys.stderr)
            return 1
        print("[PASS] NL2SQL CI gates passed and benchmarks/EVAL_REPORT.md is synchronized.")
        return 0

    report_path.write_bytes(expected_bytes)
    print(f"[OK] Rendered {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
