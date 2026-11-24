"""Deterministic bilingual (PT/EN) NL2SQL compiler bound strictly to the semantic catalog."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from semantic_bi_gateway.nl2sql.firewall import (
    AstSqlFirewall,
    FirewallDecision,
    SqlFirewallViolation,
)
from semantic_bi_gateway.semantic.compiler import SemanticCompiler
from semantic_bi_gateway.semantic.models import (
    CompiledSemanticModel,
    FactSubPlan,
    MetricSpec,
    QueryPlan,
    UncertifiedEntityError,
)

_ADVERSARIAL_SQL_RE = re.compile(
    r"(?:;|--|\*/|/\*|\b(?:drop\s+table|drop\s+view|delete\s+from|insert\s+into|"
    r"update\s+\w+\s+set|alter\s+table|attach\s+|copy\s+\w+\s+to|install\s+|load\s+|"
    r"pragma\b|read_csv|read_parquet|read_json|read_text|sqlite_master)\b)",
    re.IGNORECASE,
)

_PII_TERM_RE = re.compile(
    r"\b(?:cpf|cnpj_pf|e-?mail|telefone|phone|celular|endereco\s+completo|rg|"
    r"password|senha|secret|api_key|salary_raw)\b",
    re.IGNORECASE,
)

_METRIC_PHRASES: tuple[tuple[str, str], ...] = (
    # Longest/most specific phrases first to prevent partial collisions.
    ("gross margin percentage", "gross_margin_pct"),
    ("margem bruta percentual", "gross_margin_pct"),
    ("gross margin pct", "gross_margin_pct"),
    ("percentual de margem", "gross_margin_pct"),
    ("margem percentual", "gross_margin_pct"),
    ("gross margin %", "gross_margin_pct"),
    ("taxa de margem", "gross_margin_pct"),
    ("margem %", "gross_margin_pct"),
    ("total gross margin", "total_gross_margin"),
    ("margem bruta total", "total_gross_margin"),
    ("gross margin", "total_gross_margin"),
    ("gross profit", "total_gross_margin"),
    ("margem bruta", "total_gross_margin"),
    ("lucro bruto", "total_gross_margin"),
    ("average order value", "average_order_value"),
    ("valor medio do pedido", "average_order_value"),
    ("average ticket", "average_order_value"),
    ("ticket medio", "average_order_value"),
    ("aov", "average_order_value"),
    ("mean time to resolution", "mttr_hours"),
    ("tempo medio de resolucao", "mttr_hours"),
    ("mttr hours", "mttr_hours"),
    ("mttr", "mttr_hours"),
    ("sla compliance rate", "sla_compliance_rate"),
    ("cumprimento de sla", "sla_compliance_rate"),
    ("conformidade de sla", "sla_compliance_rate"),
    ("sla compliance", "sla_compliance_rate"),
    ("aderencia sla", "sla_compliance_rate"),
    ("taxa de sla", "sla_compliance_rate"),
    ("sla rate", "sla_compliance_rate"),
    ("sla", "sla_compliance_rate"),
    ("quantidade de pedidos", "order_count"),
    ("numero de pedidos", "order_count"),
    ("total de pedidos", "order_count"),
    ("contagem de pedidos", "order_count"),
    ("number of orders", "order_count"),
    ("how many orders", "order_count"),
    ("quantos pedidos", "order_count"),
    ("order count", "order_count"),
    ("total orders", "order_count"),
    ("orders", "order_count"),
    ("pedidos", "order_count"),
    ("quantidade de chamados", "ticket_count"),
    ("numero de chamados", "ticket_count"),
    ("total de chamados", "ticket_count"),
    ("volume de tickets", "ticket_count"),
    ("number of tickets", "ticket_count"),
    ("how many tickets", "ticket_count"),
    ("quantos chamados", "ticket_count"),
    ("service ticket count", "ticket_count"),
    ("ticket count", "ticket_count"),
    ("chamados", "ticket_count"),
    ("tickets", "ticket_count"),
    ("total net revenue", "total_revenue"),
    ("total revenue", "total_revenue"),
    ("receita liquida total", "total_revenue"),
    ("receita liquida", "total_revenue"),
    ("receita total", "total_revenue"),
    ("net revenue", "total_revenue"),
    ("faturamento", "total_revenue"),
    ("receita", "total_revenue"),
    ("revenue", "total_revenue"),
)

_DIMENSION_PHRASES: tuple[tuple[str, str], ...] = (
    ("macro region", "dim_region.macro_region"),
    ("macro_region", "dim_region.macro_region"),
    ("macrorregiao", "dim_region.macro_region"),
    ("macrorregioes", "dim_region.macro_region"),
    ("service tier", "dim_service_tier.service_tier_name"),
    ("nivel de servico", "dim_service_tier.service_tier_name"),
    ("niveis de servico", "dim_service_tier.service_tier_name"),
    ("customer segment", "dim_customer_segment.segment_name"),
    ("tier group", "dim_customer_segment.tier_group"),
    ("grupo de tier", "dim_customer_segment.tier_group"),
    ("order status", "fct_revenue_orders.order_status"),
    ("status do pedido", "fct_revenue_orders.order_status"),
    ("ticket status", "fct_sla_tickets.ticket_status"),
    ("status do chamado", "fct_sla_tickets.ticket_status"),
    ("status do ticket", "fct_sla_tickets.ticket_status"),
    ("year_month", "dim_date.year_month"),
    ("year month", "dim_date.year_month"),
    ("ano mes", "dim_date.year_month"),
    ("competencia", "dim_date.year_month"),
    ("mensal", "dim_date.year_month"),
    ("monthly", "dim_date.year_month"),
    ("por mes", "dim_date.year_month"),
    ("by month", "dim_date.year_month"),
    ("per month", "dim_date.year_month"),
    ("trimestral", "dim_date.quarter_label"),
    ("quarterly", "dim_date.quarter_label"),
    ("por trimestre", "dim_date.quarter_label"),
    ("by quarter", "dim_date.quarter_label"),
    ("trimestre", "dim_date.quarter_label"),
    ("quarter", "dim_date.quarter_label"),
    ("por ano", "dim_date.year_num"),
    ("by year", "dim_date.year_num"),
    ("anual", "dim_date.year_num"),
    ("yearly", "dim_date.year_num"),
    ("por uf", "dim_region.uf_code"),
    ("by uf", "dim_region.uf_code"),
    ("por estado", "dim_region.uf_code"),
    ("by state", "dim_region.uf_code"),
    ("estados", "dim_region.uf_code"),
    ("estado", "dim_region.uf_code"),
    ("states", "dim_region.uf_code"),
    ("uf_code", "dim_region.uf_code"),
    ("ufs", "dim_region.uf_code"),
    ("uf", "dim_region.uf_code"),
    ("por regiao", "dim_region.region_name"),
    ("by region", "dim_region.region_name"),
    ("regioes", "dim_region.region_name"),
    ("regiao", "dim_region.region_name"),
    ("regions", "dim_region.region_name"),
    ("region", "dim_region.region_name"),
    ("segmentos", "dim_customer_segment.segment_name"),
    ("segmento", "dim_customer_segment.segment_name"),
    ("segments", "dim_customer_segment.segment_name"),
    ("segment", "dim_customer_segment.segment_name"),
    ("prioridades", "dim_service_tier.priority_level"),
    ("prioridade", "dim_service_tier.priority_level"),
    ("priority", "dim_service_tier.priority_level"),
)

_UF_CODES = frozenset({"AM", "SP", "RJ", "MG", "BA", "PE", "PR", "DF"})


def normalize_text(text: str) -> str:
    """Apply Unicode NFKC, accent stripping, and lowercasing for bilingual matching."""
    nfkc = unicodedata.normalize("NFKC", text)
    nfd = unicodedata.normalize("NFD", nfkc)
    stripped = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    return " ".join(stripped.lower().split())


@dataclass(frozen=True)
class SqlFilter:
    """Certified WHERE predicate bound to a semantic model column."""

    model: str
    column: str
    sql_predicate: str


@dataclass(frozen=True)
class Nl2SqlTranslation:
    """Result of translating a natural-language question into firewall-validated DuckDB SQL."""

    question: str
    mode: str
    sql: str
    strategy: str
    metrics: tuple[str, ...]
    dimensions: tuple[str, ...]
    filters: tuple[str, ...]
    firewall_decision: FirewallDecision


def _qualify_metric_sql(metric: MetricSpec) -> str:
    """Build table-qualified SQL expression for a certified metric."""
    table_name = metric.model

    def _qualify_call(expr: str) -> str:
        match = re.match(
            r"^(SUM|COUNT|AVG|MIN|MAX)\(\s*(DISTINCT\s+)?([a-z_][a-z0-9_]*)\s*\)$",
            expr.strip(),
            re.IGNORECASE,
        )
        if not match:
            return expr
        fn = match.group(1).upper()
        distinct_kw = "DISTINCT " if match.group(2) else ""
        col = match.group(3)
        return f"{fn}({distinct_kw}{table_name}.{col})"

    if metric.metric_type == "ratio" and metric.numerator and metric.denominator:
        num = _qualify_call(metric.numerator)
        den = _qualify_call(metric.denominator)
        return f"({num}) / NULLIF({den}, 0)"
    if metric.expression:
        return _qualify_call(metric.expression)
    raise ValueError(f"Metric '{metric.name}' has no compilable SQL formula.")


class DeterministicNl2SqlEngine:
    """Offline, zero-token bilingual NL2SQL compiler enforcing semantic graph and AST firewall."""

    def __init__(
        self,
        compiled: CompiledSemanticModel,
        firewall: AstSqlFirewall | None = None,
    ) -> None:
        self._compiled = compiled
        self._compiler = SemanticCompiler()
        self._firewall = firewall or AstSqlFirewall(compiled)

    def translate(self, question: str) -> Nl2SqlTranslation:
        """Translate a Portuguese or English business question into validated DuckDB SQL."""
        if not isinstance(question, str) or not question.strip():
            raise SqlFirewallViolation("EMPTY_QUESTION", "Question must be a non-empty string.")

        self._check_pre_guards(question)
        norm_q = normalize_text(question)

        metrics = self._extract_metrics(norm_q)
        dimensions = self._extract_dimensions(norm_q)
        filters = self._extract_filters(norm_q, question, metrics)
        order_dir, limit_val = self._extract_order_and_limit(norm_q)

        plan = self._compiler.plan_query(self._compiled, metrics=metrics, dimensions=dimensions)
        raw_sql = self._render_sql_from_plan(plan, filters, order_dir, limit_val)
        decision = self._firewall.validate(raw_sql)

        return Nl2SqlTranslation(
            question=question,
            mode="deterministic",
            sql=decision.sanitized_sql,
            strategy=plan.strategy,
            metrics=metrics,
            dimensions=dimensions,
            filters=tuple(f.sql_predicate for f in filters),
            firewall_decision=decision,
        )

    def _check_pre_guards(self, question: str) -> None:
        nfkc = unicodedata.normalize("NFKC", question)
        if "\x00" in question or any(unicodedata.category(ch) == "Cf" for ch in question):
            raise SqlFirewallViolation(
                "ADVERSARIAL_INPUT",
                "Invisible format characters or null bytes are prohibited in questions.",
            )
        if _ADVERSARIAL_SQL_RE.search(nfkc):
            raise SqlFirewallViolation(
                "ADVERSARIAL_INPUT",
                "Question contains prohibited SQL injection or file-access primitives.",
            )
        norm = normalize_text(question)
        if _PII_TERM_RE.search(norm):
            raise SqlFirewallViolation(
                "SENSITIVE_COLUMN_BLOCKED",
                "Requests for sensitive PII columns (CPF, email, phone, RG, secrets) are blocked.",
            )

    def _extract_metrics(self, norm_q: str) -> tuple[str, ...]:
        matched_spans: list[tuple[int, int, str]] = []
        occupied: list[tuple[int, int]] = []

        for phrase, metric_name in _METRIC_PHRASES:
            if metric_name not in self._compiled.metrics:
                continue
            pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)")
            for match in pattern.finditer(norm_q):
                span = (match.start(), match.end())
                if any(not (span[1] <= a or span[0] >= b) for a, b in occupied):
                    continue
                occupied.append(span)
                matched_spans.append((span[0], span[1], metric_name))

        matched_spans.sort(key=lambda item: item[0])
        ordered = tuple(dict.fromkeys(item[2] for item in matched_spans))
        if not ordered:
            raise UncertifiedEntityError(
                "No certified KPI metric matched the question. "
                f"Available metrics: {', '.join(sorted(self._compiled.metrics))}."
            )
        return ordered

    def _extract_dimensions(self, norm_q: str) -> tuple[str, ...]:
        matched_spans: list[tuple[int, int, str]] = []
        occupied: list[tuple[int, int]] = []

        for phrase, dim_ref in _DIMENSION_PHRASES:
            pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)")
            for match in pattern.finditer(norm_q):
                span = (match.start(), match.end())
                if any(not (span[1] <= a or span[0] >= b) for a, b in occupied):
                    continue
                occupied.append(span)
                matched_spans.append((span[0], span[1], dim_ref))

        matched_spans.sort(key=lambda item: item[0])
        return tuple(dict.fromkeys(item[2] for item in matched_spans))

    def _extract_filters(
        self,
        norm_q: str,
        raw_q: str,
        metrics: tuple[str, ...],
    ) -> tuple[SqlFilter, ...]:
        filters: list[SqlFilter] = []
        metric_models = {self._compiled.metrics[m].model for m in metrics}

        if ym_match := re.search(r"\b(202[56]-(?:0[1-9]|1[0-2]))\b", norm_q):
            filters.append(
                SqlFilter("dim_date", "year_month", f"dim_date.year_month = '{ym_match.group(1)}'")
            )
        elif q_match := re.search(r"\b(202[56])-q([1-4])\b", norm_q):
            label = f"{q_match.group(1)}-Q{q_match.group(2)}"
            filters.append(
                SqlFilter("dim_date", "quarter_label", f"dim_date.quarter_label = '{label}'")
            )
        elif q_match2 := re.search(r"\bq([1-4])\s+(?:de\s+|in\s+|of\s+)?(202[56])\b", norm_q):
            label = f"{q_match2.group(2)}-Q{q_match2.group(1)}"
            filters.append(
                SqlFilter("dim_date", "quarter_label", f"dim_date.quarter_label = '{label}'")
            )
        elif yr_match := re.search(r"\b(?:em|de|in|for|ano|year)?\s*(2025|2026)\b", norm_q):
            filters.append(
                SqlFilter("dim_date", "year_num", f"dim_date.year_num = {int(yr_match.group(1))}")
            )

        uf_match = re.search(r"\b(?:uf|estado|state|em|na|no|in|for)\s+(am|sp|rj|mg|ba|pe|pr|df)\b", norm_q)
        raw_uf_match = re.search(r"\b(AM|SP|RJ|MG|BA|PE|PR|DF)\b", raw_q)
        if uf_match:
            uf = uf_match.group(1).upper()
            filters.append(SqlFilter("dim_region", "uf_code", f"dim_region.uf_code = '{uf}'"))
        elif raw_uf_match and raw_uf_match.group(1) in _UF_CODES:
            uf = raw_uf_match.group(1)
            filters.append(SqlFilter("dim_region", "uf_code", f"dim_region.uf_code = '{uf}'"))

        for norm_val, canonical in (
            ("centro-oeste", "Centro-Oeste"),
            ("centro oeste", "Centro-Oeste"),
            ("nordeste", "Nordeste"),
            ("sudeste", "Sudeste"),
            ("norte", "Norte"),
            ("sul", "Sul"),
        ):
            if re.search(rf"(?<!\w){re.escape(norm_val)}(?!\w)", norm_q):
                filters.append(
                    SqlFilter("dim_region", "macro_region", f"dim_region.macro_region = '{canonical}'")
                )
                break

        for norm_val, canonical in (
            ("mid-market", "Mid-Market"),
            ("mid market", "Mid-Market"),
            ("public sector", "Public Sector"),
            ("setor publico", "Public Sector"),
            ("enterprise", "Enterprise"),
            ("smb", "SMB"),
        ):
            if re.search(rf"(?<!\w){re.escape(norm_val)}(?!\w)", norm_q):
                filters.append(
                    SqlFilter(
                        "dim_customer_segment",
                        "segment_name",
                        f"dim_customer_segment.segment_name = '{canonical}'",
                    )
                )
                break

        for norm_val, canonical in (
            ("gold 24x7", "Gold 24x7"),
            ("gold", "Gold 24x7"),
            ("silver business hours", "Silver Business Hours"),
            ("silver", "Silver Business Hours"),
            ("standard", "Standard"),
        ):
            if re.search(rf"(?<!\w){re.escape(norm_val)}(?!\w)", norm_q):
                filters.append(
                    SqlFilter(
                        "dim_service_tier",
                        "service_tier_name",
                        f"dim_service_tier.service_tier_name = '{canonical}'",
                    )
                )
                break

        if prio_match := re.search(r"\b(p1|p2|p3)\b", norm_q):
            filters.append(
                SqlFilter(
                    "dim_service_tier",
                    "priority_level",
                    f"dim_service_tier.priority_level = '{prio_match.group(1).upper()}'",
                )
            )

        if "fct_revenue_orders" in metric_models:
            for norm_val, canonical in (
                ("invoiced", "INVOICED"),
                ("faturados", "INVOICED"),
                ("faturado", "INVOICED"),
                ("collected", "COLLECTED"),
                ("recebidos", "COLLECTED"),
                ("recebido", "COLLECTED"),
                ("pending", "PENDING"),
                ("pendentes", "PENDING"),
                ("pendente", "PENDING"),
            ):
                if re.search(rf"(?<!\w){re.escape(norm_val)}(?!\w)", norm_q):
                    filters.append(
                        SqlFilter(
                            "fct_revenue_orders",
                            "order_status",
                            f"fct_revenue_orders.order_status = '{canonical}'",
                        )
                    )
                    break

        if "fct_sla_tickets" in metric_models:
            for norm_val, canonical in (
                ("sla_breached", "SLA_BREACHED"),
                ("violados", "SLA_BREACHED"),
                ("violado", "SLA_BREACHED"),
                ("completed", "COMPLETED"),
                ("concluidos", "COMPLETED"),
                ("concluido", "COMPLETED"),
                ("in_execution", "IN_EXECUTION"),
                ("em execucao", "IN_EXECUTION"),
            ):
                if re.search(rf"(?<!\w){re.escape(norm_val)}(?!\w)", norm_q):
                    filters.append(
                        SqlFilter(
                            "fct_sla_tickets",
                            "ticket_status",
                            f"fct_sla_tickets.ticket_status = '{canonical}'",
                        )
                    )
                    break

        return tuple(filters)

    def _extract_order_and_limit(self, norm_q: str) -> tuple[str, int]:
        default_limit = self._compiled.security.default_limit
        max_limit = self._compiled.security.max_limit

        if top_match := re.search(r"\b(?:top|maiores|primeiros|highest|melhores)\s+(\d+)\b", norm_q):
            return "DESC", min(max(int(top_match.group(1)), 1), max_limit)
        if bot_match := re.search(r"\b(?:menores|piores|lowest|bottom|ultimos)\s+(\d+)\b", norm_q):
            return "ASC", min(max(int(bot_match.group(1)), 1), max_limit)
        if re.search(r"\b(?:menor|lowest|piores?|worst|minimum)\b", norm_q):
            return "ASC", default_limit
        return "DESC", default_limit

    def _render_sql_from_plan(
        self,
        plan: QueryPlan,
        filters: tuple[SqlFilter, ...],
        order_dir: str,
        limit_val: int,
    ) -> str:
        if plan.strategy == "single_fact":
            return self._render_single_fact_sql(
                subplan=plan.fact_subplans[0],
                filters=filters,
                order_dir=order_dir,
                limit_val=limit_val,
            )
        return self._render_multi_fact_cte_sql(
            plan=plan,
            filters=filters,
            order_dir=order_dir,
            limit_val=limit_val,
        )

    def _joins_with_filters(
        self,
        subplan: FactSubPlan,
        filters: tuple[SqlFilter, ...],
    ) -> tuple[list[tuple[str, str, str, str, str]], list[str]]:
        joins_ordered: list[tuple[str, str, str, str, str]] = []
        seen_edges: set[str] = set()
        for step in subplan.joins:
            edge = step.edge
            if edge.name not in seen_edges:
                seen_edges.add(edge.name)
                joins_ordered.append(
                    (
                        edge.join_type.upper(),
                        self._compiled.models[edge.right_model].table,
                        edge.left_model,
                        edge.left_key,
                        edge.right_key,
                    )
                )

        where_clauses: list[str] = []
        for flt in filters:
            if flt.model == subplan.fact_model or self._compiled.models[flt.model].role == "dimension":
                if flt.model != subplan.fact_model:
                    for step in self._compiler.resolve_join_path(
                        self._compiled, subplan.fact_model, flt.model
                    ):
                        edge = step.edge
                        if edge.name not in seen_edges:
                            seen_edges.add(edge.name)
                            joins_ordered.append(
                                (
                                    edge.join_type.upper(),
                                    self._compiled.models[edge.right_model].table,
                                    edge.left_model,
                                    edge.left_key,
                                    edge.right_key,
                                )
                            )
                where_clauses.append(flt.sql_predicate)

        return joins_ordered, where_clauses

    def _render_single_fact_sql(
        self,
        subplan: FactSubPlan,
        filters: tuple[SqlFilter, ...],
        order_dir: str,
        limit_val: int,
    ) -> str:
        select_items: list[str] = []
        group_items: list[str] = []
        for dim_ref in subplan.dimensions:
            dim_model, dim_col = dim_ref.split(".", 1)
            select_items.append(f"{dim_model}.{dim_col} AS {dim_col}")
            group_items.append(f"{dim_model}.{dim_col}")

        for metric in subplan.metrics:
            select_items.append(f"{_qualify_metric_sql(metric)} AS {metric.name}")

        joins_ordered, where_clauses = self._joins_with_filters(subplan, filters)
        lines = [f"SELECT {', '.join(select_items)}", f"FROM {subplan.table}"]
        for join_type, right_table, left_model, left_key, right_key in joins_ordered:
            lines.append(
                f"{join_type} JOIN {right_table} ON {left_model}.{left_key} = {right_table}.{right_key}"
            )
        if where_clauses:
            lines.append(f"WHERE {' AND '.join(where_clauses)}")
        if group_items:
            lines.append(f"GROUP BY {', '.join(group_items)}")
            first_dim = subplan.dimensions[0]
            if first_dim.startswith("dim_date."):
                lines.append(f"ORDER BY {first_dim.split('.', 1)[1]} ASC")
            else:
                lines.append(f"ORDER BY {subplan.metrics[0].name} {order_dir}")
        lines.append(f"LIMIT {limit_val}")
        return "\n".join(lines)

    def _render_multi_fact_cte_sql(
        self,
        plan: QueryPlan,
        filters: tuple[SqlFilter, ...],
        order_dir: str,
        limit_val: int,
    ) -> str:
        cte_blocks: list[str] = []
        cte_names: list[str] = []
        dim_aliases = [dim.split(".", 1)[1] for dim in plan.conformed_dimensions]

        for subplan in plan.fact_subplans:
            cte_name = f"cte_{subplan.fact_model}"
            cte_names.append(cte_name)
            select_items = [
                f"{dim.split('.', 1)[0]}.{dim.split('.', 1)[1]} AS {dim.split('.', 1)[1]}"
                for dim in subplan.dimensions
            ]
            group_items = [dim for dim in subplan.dimensions]
            for metric in subplan.metrics:
                select_items.append(f"{_qualify_metric_sql(metric)} AS {metric.name}")

            joins_ordered, where_clauses = self._joins_with_filters(subplan, filters)
            sub_lines = [f"    SELECT {', '.join(select_items)}", f"    FROM {subplan.table}"]
            for join_type, right_table, left_model, left_key, right_key in joins_ordered:
                sub_lines.append(
                    f"    {join_type} JOIN {right_table} ON {left_model}.{left_key} = {right_table}.{right_key}"
                )
            if where_clauses:
                sub_lines.append(f"    WHERE {' AND '.join(where_clauses)}")
            if group_items:
                sub_lines.append(f"    GROUP BY {', '.join(group_items)}")
            cte_blocks.append(f"{cte_name} AS (\n" + "\n".join(sub_lines) + "\n)")

        base_cte = cte_names[0]
        outer_select = [f"{base_cte}.{alias} AS {alias}" for alias in dim_aliases]
        for cte_name, subplan in zip(cte_names, plan.fact_subplans, strict=True):
            for metric in subplan.metrics:
                outer_select.append(f"{cte_name}.{metric.name} AS {metric.name}")

        lines = ["WITH " + ",\n".join(cte_blocks), f"SELECT {', '.join(outer_select)}", f"FROM {base_cte}"]
        for next_cte in cte_names[1:]:
            if dim_aliases:
                on_clause = " AND ".join(f"{base_cte}.{a} = {next_cte}.{a}" for a in dim_aliases)
                lines.append(f"INNER JOIN {next_cte} ON {on_clause}")
            else:
                lines.append(f"CROSS JOIN {next_cte}")

        if dim_aliases:
            if plan.conformed_dimensions[0].startswith("dim_date."):
                lines.append(f"ORDER BY {base_cte}.{dim_aliases[0]} ASC")
            else:
                lines.append(f"ORDER BY {plan.metrics[0].name} {order_dir}")
        lines.append(f"LIMIT {limit_val}")
        return "\n".join(lines)
