"""Deterministic NL2SQL evaluation harness (AST equivalence, result-set hash, adversarial block)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
import yaml
from sqlglot import exp

from semantic_bi_gateway.lakehouse import MultiLakehouseBridge
from semantic_bi_gateway.nl2sql import (
    AstSqlFirewall,
    Nl2SqlGateway,
    ReadOnlyDuckDbExecutor,
    SqlFirewallViolation,
)
from semantic_bi_gateway.semantic import (
    DbtMetadataIngestor,
    SemanticCompilationError,
    SemanticCompiler,
)


def _strip_redundant_parens(node: exp.Expression) -> exp.Expression:
    if isinstance(node, exp.Paren) and not isinstance(
        node.this, (exp.Add, exp.Sub, exp.And, exp.Or)
    ):
        return node.this
    return node


def canonicalize_sql_ast(sql: str) -> str:
    """Normalize DuckDB SQL into a canonical comment-free AST SQL string."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    simplified = tree.transform(_strip_redundant_parens)
    return simplified.sql(dialect="duckdb", comments=False)


def ast_equivalent(candidate_sql: str, expected_sql: str) -> bool:
    """Return True when two DuckDB SQL queries parse to equivalent canonical ASTs."""
    try:
        return canonicalize_sql_ast(candidate_sql) == canonicalize_sql_ast(expected_sql)
    except Exception:
        return False


@dataclass(frozen=True)
class EvalCaseOutcome:
    """Evaluation outcome for a single business NL2SQL benchmark case."""

    case_id: str
    suite_id: str
    language: str
    question: str
    ast_match: bool
    result_hash_match: bool
    generated_sql: str
    expected_sql: str
    error: str | None = None


@dataclass(frozen=True)
class AdversarialCaseOutcome:
    """Evaluation outcome for a single adversarial firewall probe."""

    case_id: str
    kind: str
    payload: str
    blocked: bool
    expected_block_reason: str
    actual_reason: str


@dataclass(frozen=True)
class SuiteMetrics:
    """Aggregated evaluation metrics for one benchmark suite."""

    suite_id: str
    total_cases: int
    ast_matches: int
    hash_matches: int
    ast_match_rate: float
    hash_match_rate: float


@dataclass(frozen=True)
class EvalReport:
    """Full deterministic NL2SQL benchmark and adversarial evaluation report."""

    mode: str
    total_business_cases: int
    ast_matches: int
    hash_matches: int
    ast_equivalence_rate: float
    result_set_hash_match_rate: float
    total_adversarial_cases: int
    adversarial_blocked: int
    adversarial_block_rate: float
    ast_equivalence_min: float
    result_set_hash_match_min: float
    adversarial_block_rate_min: float
    gates_passed: bool
    suite_metrics: tuple[SuiteMetrics, ...]
    business_outcomes: tuple[EvalCaseOutcome, ...]
    adversarial_outcomes: tuple[AdversarialCaseOutcome, ...]


class Nl2SqlEvalHarness:
    """Executes offline NL2SQL accuracy and adversarial security evaluations."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve()

    def run(self, benchmark_path: Path | None = None) -> EvalReport:
        """Run all business and adversarial benchmark cases and evaluate CI gates."""
        bench_file = (
            benchmark_path.resolve()
            if benchmark_path is not None
            else self._repo_root / "benchmarks" / "nl2sql_benchmark.json"
        )
        registry_file = self._repo_root / "catalog" / "benchmark_registry.yaml"
        metrics_file = self._repo_root / "catalog" / "metrics.yaml"
        schema_file = self._repo_root / "dbt" / "models" / "marts" / "schema.yml"

        payload = json.loads(bench_file.read_text(encoding="utf-8"))
        registry = yaml.safe_load(registry_file.read_text(encoding="utf-8"))
        gates = registry["ci_gates"]

        ingested = DbtMetadataIngestor().ingest(metrics_file, schema_paths=[schema_file])
        compiled = SemanticCompiler().compile(ingested)

        bridge = MultiLakehouseBridge(self._repo_root, allow_sibling_discovery=False)
        resolved = bridge.resolve_database(auto_bootstrap_synthetic=True)

        firewall = AstSqlFirewall(compiled)
        executor = ReadOnlyDuckDbExecutor(compiled, firewall=firewall, db_path=resolved.db_path)
        gateway = Nl2SqlGateway(compiled, db_path=resolved.db_path, mode="deterministic")

        business_outcomes: list[EvalCaseOutcome] = []
        for case in payload["business_cases"]:
            case_id = str(case["id"])
            suite_id = str(case["suite_id"])
            language = str(case["language"])
            question = str(case["question"])
            expected_sql = str(case["expected_sql"])

            expected_exec = executor.execute(expected_sql)
            try:
                actual_resp = gateway.ask(question, mode="deterministic")
                gen_sql = actual_resp.translation.sql
                ast_ok = ast_equivalent(gen_sql, expected_exec.sql)
                hash_ok = actual_resp.execution.result_hash_sha256 == expected_exec.result_hash_sha256
                err_msg = None
            except Exception as exc:
                gen_sql = ""
                ast_ok = False
                hash_ok = False
                err_msg = f"{type(exc).__name__}: {exc}"

            business_outcomes.append(
                EvalCaseOutcome(
                    case_id=case_id,
                    suite_id=suite_id,
                    language=language,
                    question=question,
                    ast_match=ast_ok,
                    result_hash_match=hash_ok,
                    generated_sql=gen_sql,
                    expected_sql=expected_exec.sql,
                    error=err_msg,
                )
            )

        adversarial_outcomes: list[AdversarialCaseOutcome] = []
        for adv in payload["adversarial_cases"]:
            adv_id = str(adv["id"])
            kind = str(adv["kind"])
            adv_payload = str(adv["payload"])
            expected_reason = str(adv["expected_block_reason"])
            blocked = False
            actual_reason = "ALLOWED_UNEXPECTEDLY"

            try:
                if kind == "sql":
                    firewall.validate(adv_payload)
                else:
                    gateway.translate(adv_payload, mode="deterministic")
            except SqlFirewallViolation as exc:
                blocked = True
                actual_reason = exc.code
            except SemanticCompilationError as exc:
                blocked = True
                actual_reason = type(exc).__name__

            adversarial_outcomes.append(
                AdversarialCaseOutcome(
                    case_id=adv_id,
                    kind=kind,
                    payload=adv_payload,
                    blocked=blocked,
                    expected_block_reason=expected_reason,
                    actual_reason=actual_reason,
                )
            )

        suite_buckets: dict[str, list[EvalCaseOutcome]] = {}
        for outcome in business_outcomes:
            suite_buckets.setdefault(outcome.suite_id, []).append(outcome)

        suite_metrics = tuple(
            SuiteMetrics(
                suite_id=suite_id,
                total_cases=len(items),
                ast_matches=sum(1 for i in items if i.ast_match),
                hash_matches=sum(1 for i in items if i.result_hash_match),
                ast_match_rate=sum(1 for i in items if i.ast_match) / len(items),
                hash_match_rate=sum(1 for i in items if i.result_hash_match) / len(items),
            )
            for suite_id, items in suite_buckets.items()
        )

        total_biz = len(business_outcomes)
        ast_matches = sum(1 for o in business_outcomes if o.ast_match)
        hash_matches = sum(1 for o in business_outcomes if o.result_hash_match)
        ast_rate = ast_matches / total_biz if total_biz else 0.0
        hash_rate = hash_matches / total_biz if total_biz else 0.0

        total_adv = len(adversarial_outcomes)
        adv_blocked = sum(1 for a in adversarial_outcomes if a.blocked)
        adv_rate = adv_blocked / total_adv if total_adv else 0.0

        ast_min = float(gates["ast_equivalence_min"])
        hash_min = float(gates["result_set_hash_match_min"])
        adv_min = float(gates["adversarial_block_rate_min"])

        gates_passed = (
            hash_rate >= hash_min
            and ast_rate >= ast_min
            and adv_rate >= adv_min
        )

        return EvalReport(
            mode="deterministic",
            total_business_cases=total_biz,
            ast_matches=ast_matches,
            hash_matches=hash_matches,
            ast_equivalence_rate=ast_rate,
            result_set_hash_match_rate=hash_rate,
            total_adversarial_cases=total_adv,
            adversarial_blocked=adv_blocked,
            adversarial_block_rate=adv_rate,
            ast_equivalence_min=ast_min,
            result_set_hash_match_min=hash_min,
            adversarial_block_rate_min=adv_min,
            gates_passed=gates_passed,
            suite_metrics=suite_metrics,
            business_outcomes=tuple(business_outcomes),
            adversarial_outcomes=tuple(adversarial_outcomes),
        )


def render_eval_report_markdown(report: EvalReport) -> str:
    """Render deterministic Markdown evaluation report for version control and CI."""
    status_label = "PASSED" if report.gates_passed else "FAILED"
    lines: list[str] = [
        "# NL2SQL Benchmark & Adversarial Security Evaluation Report",
        "",
        "> **Single Source of Truth:** Deterministically generated by "
        "`python scripts/eval_nl2sql.py` from [`benchmarks/nl2sql_benchmark.json`](../benchmarks/nl2sql_benchmark.json). "
        "Do not edit manually.",
        "",
        f"- **Evaluation Mode:** `{report.mode}` (offline, zero LLM cost)",
        f"- **Overall CI Gate Status:** `{status_label}`",
        f"- **Result-Set Hash Match:** `{report.result_set_hash_match_rate * 100:.1f}%` "
        f"(`{report.hash_matches}/{report.total_business_cases}` | CI floor: `>= {report.result_set_hash_match_min * 100:.0f}%`)",
        f"- **AST Equivalence Match:** `{report.ast_equivalence_rate * 100:.1f}%` "
        f"(`{report.ast_matches}/{report.total_business_cases}` | CI floor: `>= {report.ast_equivalence_min * 100:.0f}%`)",
        f"- **Adversarial Block Rate:** `{report.adversarial_block_rate * 100:.1f}%` "
        f"(`{report.adversarial_blocked}/{report.total_adversarial_cases}` | CI floor: `== {report.adversarial_block_rate_min * 100:.0f}%`)",
        "",
        "---",
        "",
        "## 1. Accuracy Breakdown by Benchmark Suite",
        "",
        "| Suite ID | Cases | AST Match | Result-Set Hash Match |",
        "| :--- | :---: | :---: | :---: |",
    ]

    for suite in report.suite_metrics:
        lines.append(
            f"| `{suite.suite_id}` | {suite.total_cases} | "
            f"{suite.ast_matches}/{suite.total_cases} ({suite.ast_match_rate * 100:.1f}%) | "
            f"{suite.hash_matches}/{suite.total_cases} ({suite.hash_match_rate * 100:.1f}%) |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 2. Adversarial Security Probes",
            "",
            "| Probe ID | Kind | Expected Policy Code | Observed Outcome | Status |",
            "| :--- | :---: | :--- | :--- | :---: |",
        ]
    )

    for adv in report.adversarial_outcomes:
        badge = "BLOCKED" if adv.blocked else "BYPASSED"
        lines.append(
            f"| `{adv.case_id}` | `{adv.kind}` | `{adv.expected_block_reason}` | "
            f"`{adv.actual_reason}` | `{badge}` |"
        )

    failed_cases = [o for o in report.business_outcomes if not (o.ast_match and o.result_hash_match)]
    lines.extend(["", "---", "", "## 3. Mismatched Business Cases", ""])
    if not failed_cases:
        lines.append("None — 100% of business cases matched both canonical AST and result-set SHA-256 hash.")
    else:
        for item in failed_cases:
            lines.append(
                f"- `{item.case_id}` ({item.suite_id}): ast={item.ast_match}, "
                f"hash={item.result_hash_match}, error={item.error}"
            )

    lines.append("")
    return "\n".join(lines)


def report_to_dict(report: EvalReport) -> dict[str, Any]:
    """Serialize EvalReport into JSON-friendly dictionary."""
    return {
        "mode": report.mode,
        "gates_passed": report.gates_passed,
        "total_business_cases": report.total_business_cases,
        "ast_matches": report.ast_matches,
        "hash_matches": report.hash_matches,
        "ast_equivalence_rate": round(report.ast_equivalence_rate, 4),
        "result_set_hash_match_rate": round(report.result_set_hash_match_rate, 4),
        "total_adversarial_cases": report.total_adversarial_cases,
        "adversarial_blocked": report.adversarial_blocked,
        "adversarial_block_rate": round(report.adversarial_block_rate, 4),
        "thresholds": {
            "ast_equivalence_min": report.ast_equivalence_min,
            "result_set_hash_match_min": report.result_set_hash_match_min,
            "adversarial_block_rate_min": report.adversarial_block_rate_min,
        },
        "suites": [
            {
                "suite_id": s.suite_id,
                "total_cases": s.total_cases,
                "ast_matches": s.ast_matches,
                "hash_matches": s.hash_matches,
                "ast_match_rate": round(s.ast_match_rate, 4),
                "hash_match_rate": round(s.hash_match_rate, 4),
            }
            for s in report.suite_metrics
        ],
    }
