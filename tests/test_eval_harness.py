"""Offline tests for the 50-question NL2SQL benchmark and adversarial evaluation harness."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from semantic_bi_gateway.evals import (
    Nl2SqlEvalHarness,
    ast_equivalent,
    report_to_dict,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_ast_equivalent_helper() -> None:
    sql_a = "SELECT /* comment */ region_name FROM dim_region LIMIT 100"
    sql_b = "select region_name from dim_region limit 100"
    sql_c = "SELECT uf_code FROM dim_region LIMIT 100"
    assert ast_equivalent(sql_a, sql_b)
    assert not ast_equivalent(sql_a, sql_c)


def test_eval_harness_meets_ci_gates() -> None:
    harness = Nl2SqlEvalHarness(REPO_ROOT)
    report = harness.run()

    assert report.total_business_cases == 50
    assert report.total_adversarial_cases == 20
    assert report.gates_passed is True
    assert report.result_set_hash_match_rate >= report.result_set_hash_match_min
    assert report.ast_equivalence_rate >= report.ast_equivalence_min
    assert report.adversarial_block_rate == 1.0

    summary = report_to_dict(report)
    assert summary["ast_matches"] == 50
    assert summary["hash_matches"] == 50
    assert summary["adversarial_blocked"] == 20


def test_eval_nl2sql_cli_check_mode_passes() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "eval_nl2sql.py"),
            "--check",
            "--repo-root",
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"eval_nl2sql --check failed:\n{proc.stdout}\n{proc.stderr}"
