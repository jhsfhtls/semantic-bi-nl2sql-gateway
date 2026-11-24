"""NL2SQL accuracy and adversarial security evaluation harness."""

from semantic_bi_gateway.evals.harness import (
    AdversarialCaseOutcome,
    EvalCaseOutcome,
    EvalReport,
    Nl2SqlEvalHarness,
    SuiteMetrics,
    ast_equivalent,
    canonicalize_sql_ast,
    render_eval_report_markdown,
    report_to_dict,
)

__all__ = [
    "AdversarialCaseOutcome",
    "EvalCaseOutcome",
    "EvalReport",
    "Nl2SqlEvalHarness",
    "SuiteMetrics",
    "ast_equivalent",
    "canonicalize_sql_ast",
    "render_eval_report_markdown",
    "report_to_dict",
]
