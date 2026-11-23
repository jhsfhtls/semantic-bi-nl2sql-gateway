"""Deterministic DAX and PBIP TMDL measure generator from compiled semantic metrics."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from semantic_bi_gateway.semantic.models import CompiledSemanticModel, MetricSpec

_AGG_CALL_RE = re.compile(
    r"^(SUM|COUNT|AVG|MIN|MAX)\(\s*(DISTINCT\s+)?([a-z_][a-z0-9_]*)\s*\)$",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class DaxMeasure:
    """Compiled Power BI DAX measure definition."""

    name: str
    expression: str
    format_string: str
    display_folder: str
    description: str
    metric_name: str
    variant: str


def translate_sql_expression_to_dax(sql_expr: str, table_name: str) -> str:
    """Deterministically translate a certified SQL metric expression to DAX."""
    cleaned = sql_expr.strip()
    if _NUMBER_RE.match(cleaned):
        return cleaned

    match = _AGG_CALL_RE.match(cleaned)
    if match:
        func = match.group(1).upper()
        is_distinct = bool(match.group(2))
        column = match.group(3)
        col_ref = f"'{table_name}'[{column}]"
        if func == "COUNT" and is_distinct:
            return f"DISTINCTCOUNT({col_ref})"
        if func == "AVG":
            return f"AVERAGE({col_ref})"
        return f"{func}({col_ref})"

    raise ValueError(f"Unsupported SQL expression for deterministic DAX translation: {sql_expr!r}")


def _build_base_dax(metric: MetricSpec, table_name: str) -> str:
    if metric.metric_type == "ratio":
        if not metric.numerator or not metric.denominator:
            raise ValueError(f"Ratio metric '{metric.name}' missing numerator or denominator.")
        num_dax = translate_sql_expression_to_dax(metric.numerator, table_name)
        den_dax = translate_sql_expression_to_dax(metric.denominator, table_name)
        return f"DIVIDE({num_dax}, {den_dax}, BLANK())"
    if not metric.expression:
        raise ValueError(f"Metric '{metric.name}' missing SQL expression.")
    return translate_sql_expression_to_dax(metric.expression, table_name)


def build_dax_measures(
    compiled: CompiledSemanticModel,
    date_table: str = "dim_date",
    date_column: str = "date_day",
) -> tuple[DaxMeasure, ...]:
    """Generate sorted deterministic DAX measures (base, Time Intelligence, target variance)."""
    measures: list[DaxMeasure] = []
    date_ref = f"'{date_table}'[{date_column}]"

    for metric_name in sorted(compiled.metrics):
        metric = compiled.metrics[metric_name]
        if not metric.certified:
            continue

        table_name = compiled.models[metric.model].table
        folder = metric.display_folder or "KPIs"
        base_name = metric.label
        base_expr = _build_base_dax(metric, table_name)

        measures.append(
            DaxMeasure(
                name=base_name,
                expression=base_expr,
                format_string=metric.format,
                display_folder=folder,
                description=metric.description,
                metric_name=metric.name,
                variant="base",
            )
        )

        if metric.time_intelligence:
            measures.extend(
                [
                    DaxMeasure(
                        name=f"{base_name} YTD",
                        expression=f"TOTALYTD([{base_name}], {date_ref})",
                        format_string=metric.format,
                        display_folder=f"{folder}\\Time Intelligence",
                        description=f"Year-to-date cumulative value of {base_name}.",
                        metric_name=metric.name,
                        variant="ytd",
                    ),
                    DaxMeasure(
                        name=f"{base_name} YoY %",
                        expression=(
                            f"VAR _py = CALCULATE([{base_name}], SAMEPERIODLASTYEAR({date_ref})) "
                            f"RETURN DIVIDE([{base_name}] - _py, _py, BLANK())"
                        ),
                        format_string="0.00%",
                        display_folder=f"{folder}\\Time Intelligence",
                        description=f"Year-over-year percentage change for {base_name}.",
                        metric_name=metric.name,
                        variant="yoy_pct",
                    ),
                    DaxMeasure(
                        name=f"{base_name} MoM %",
                        expression=(
                            f"VAR _pm = CALCULATE([{base_name}], DATEADD({date_ref}, -1, MONTH)) "
                            f"RETURN DIVIDE([{base_name}] - _pm, _pm, BLANK())"
                        ),
                        format_string="0.00%",
                        display_folder=f"{folder}\\Time Intelligence",
                        description=f"Month-over-month percentage change for {base_name}.",
                        metric_name=metric.name,
                        variant="mom_pct",
                    ),
                    DaxMeasure(
                        name=f"{base_name} R12M",
                        expression=(
                            f"CALCULATE([{base_name}], "
                            f"DATESINPERIOD({date_ref}, MAX({date_ref}), -12, MONTH))"
                        ),
                        format_string=metric.format,
                        display_folder=f"{folder}\\Time Intelligence",
                        description=f"Rolling 12-month window calculation for {base_name}.",
                        metric_name=metric.name,
                        variant="rolling_12m",
                    ),
                ]
            )

        if metric.target_expression:
            target_dax = translate_sql_expression_to_dax(metric.target_expression, table_name)
            target_name = f"{base_name} Target"
            measures.extend(
                [
                    DaxMeasure(
                        name=target_name,
                        expression=target_dax,
                        format_string=metric.format,
                        display_folder=f"{folder}\\Targets",
                        description=f"Certified target benchmark for {base_name}.",
                        metric_name=metric.name,
                        variant="target",
                    ),
                    DaxMeasure(
                        name=f"{base_name} Variance vs Target",
                        expression=f"[{base_name}] - [{target_name}]",
                        format_string=metric.format,
                        display_folder=f"{folder}\\Targets",
                        description=f"Absolute variance of {base_name} against target.",
                        metric_name=metric.name,
                        variant="variance_abs",
                    ),
                    DaxMeasure(
                        name=f"{base_name} Variance vs Target %",
                        expression=f"DIVIDE([{base_name}] - [{target_name}], [{target_name}], BLANK())",
                        format_string="0.00%",
                        display_folder=f"{folder}\\Targets",
                        description=f"Relative percentage variance of {base_name} against target.",
                        metric_name=metric.name,
                        variant="variance_pct",
                    ),
                ]
            )

    return tuple(measures)


def render_dax(measures: Sequence[DaxMeasure]) -> str:
    """Render deterministic DAX script defining all measures on '_Measures'."""
    lines: list[str] = [
        "-- Auto-generated by semantic-bi-nl2sql-gateway (ADR-003)",
        "-- Do not edit manually; regenerate via sbg compile-bi.",
        "",
        "DEFINE",
    ]
    for measure in measures:
        lines.extend(
            [
                f"-- {measure.metric_name} [{measure.variant}] | Folder: {measure.display_folder} | Format: {measure.format_string}",
                f"MEASURE '_Measures'[{measure.name}] =",
                f"    {measure.expression}",
                "",
            ]
        )
    return "\n".join(lines)


def render_tmdl(measures: Sequence[DaxMeasure]) -> str:
    """Render deterministic PBIP-compatible TMDL measure table."""
    lines: list[str] = [
        "table '_Measures'",
        "\tlineageTag: sbg-semantic-measures",
        "",
    ]
    for measure in measures:
        clean_desc = " ".join(measure.description.split())
        lines.extend(
            [
                f"\tmeasure '{measure.name}' =",
                f"\t\t\t{measure.expression}",
                f"\t\tformatString: {measure.format_string}",
                f"\t\tdisplayFolder: {measure.display_folder}",
                f"\t\tdescription: {clean_desc}",
                "",
            ]
        )
    return "\n".join(lines)
