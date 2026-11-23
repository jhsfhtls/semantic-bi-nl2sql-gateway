"""Semantic compiler building a fan-out-safe and chasm-trap-free join graph."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Sequence

from semantic_bi_gateway.semantic.models import (
    ChasmTrapError,
    CompiledSemanticModel,
    FactSubPlan,
    FanOutJoinError,
    IngestedMetadata,
    JoinEdge,
    JoinStep,
    MetricSpec,
    QueryPlan,
    SecurityPolicy,
    SemanticCompilationError,
    SensitiveColumnError,
    UncertifiedEntityError,
)

_SAFE_CARDINALITIES = frozenset({"many_to_one", "one_to_one"})
_SQL_IDENT_RE = re.compile(r"\b[a-z_][a-z0-9_]*\b", re.IGNORECASE)
_SQL_KEYWORDS = frozenset(
    {
        "sum",
        "count",
        "avg",
        "min",
        "max",
        "distinct",
        "nullif",
        "coalesce",
        "case",
        "when",
        "then",
        "else",
        "end",
        "cast",
        "as",
        "and",
        "or",
        "not",
        "in",
        "is",
        "null",
    }
)


class SemanticCompiler:
    """Compiles ingested semantic metadata into a validated directed join graph and query plans."""

    def compile(self, ingested: IngestedMetadata) -> CompiledSemanticModel:
        """Validate security, join cardinalities, cycles, and chasm traps to build compiled model."""
        self._validate_models_security(ingested)
        forward_adj, reverse_adj = self._validate_and_index_joins(ingested)
        self._detect_cycles(forward_adj)
        self._validate_metrics(ingested)

        foreign_keys = ingested.dbt_snapshot.foreign_keys if ingested.dbt_snapshot else ()
        return CompiledSemanticModel(
            schema_version=ingested.schema_version,
            project_name=ingested.project_name,
            default_dialect=ingested.default_dialect,
            security=ingested.security,
            models=dict(ingested.models),
            metrics=dict(ingested.metrics),
            allowed_joins=ingested.allowed_joins,
            forward_adjacency={k: tuple(v) for k, v in forward_adj.items()},
            reverse_edges={k: tuple(v) for k, v in reverse_adj.items()},
            foreign_keys=foreign_keys,
        )

    def _validate_models_security(self, ingested: IngestedMetadata) -> None:
        for model in ingested.models.values():
            col_map = model.column_map
            if model.primary_key.lower() not in col_map:
                raise SemanticCompilationError(
                    f"Model '{model.name}' primary_key '{model.primary_key}' missing from columns."
                )
            if model.time_dimension and model.time_dimension.lower() not in col_map:
                raise SemanticCompilationError(
                    f"Model '{model.name}' time_dimension '{model.time_dimension}' missing from columns."
                )
            for col in model.columns:
                if col.sensitive or ingested.security.is_sensitive_identifier(col.name):
                    raise SensitiveColumnError(
                        f"Certified model '{model.name}' exposes blocked sensitive column '{col.name}'."
                    )

    def _validate_and_index_joins(
        self, ingested: IngestedMetadata
    ) -> tuple[dict[str, list[JoinEdge]], dict[str, list[JoinEdge]]]:
        forward_adj: dict[str, list[JoinEdge]] = {name: [] for name in ingested.models}
        reverse_adj: dict[str, list[JoinEdge]] = {name: [] for name in ingested.models}

        for edge in ingested.allowed_joins:
            if edge.left_model not in ingested.models:
                raise UncertifiedEntityError(
                    f"Join '{edge.name}' references unknown left_model '{edge.left_model}'."
                )
            if edge.right_model not in ingested.models:
                raise UncertifiedEntityError(
                    f"Join '{edge.name}' references unknown right_model '{edge.right_model}'."
                )
            if edge.left_model == edge.right_model:
                raise FanOutJoinError(
                    f"Join '{edge.name}': self-join on '{edge.left_model}' is disallowed."
                )

            left_model = ingested.models[edge.left_model]
            right_model = ingested.models[edge.right_model]
            if left_model.role == "fact" and right_model.role == "fact":
                raise ChasmTrapError(
                    f"Join '{edge.name}': direct fact-to-fact join "
                    f"({edge.left_model} -> {edge.right_model}) causes a chasm trap."
                )

            if edge.cardinality not in _SAFE_CARDINALITIES or not edge.safe_for_aggregation:
                raise FanOutJoinError(
                    f"Join '{edge.name}' ({edge.left_model} -> {edge.right_model}) has unsafe "
                    f"cardinality '{edge.cardinality}' (safe_for_aggregation={edge.safe_for_aggregation})."
                )

            if edge.left_key.lower() not in left_model.column_map:
                raise SemanticCompilationError(
                    f"Join '{edge.name}': left_key '{edge.left_key}' missing from '{edge.left_model}'."
                )
            if edge.right_key.lower() not in right_model.column_map:
                raise SemanticCompilationError(
                    f"Join '{edge.name}': right_key '{edge.right_key}' missing from '{edge.right_model}'."
                )

            forward_adj[edge.left_model].append(edge)
            reverse_adj[edge.right_model].append(edge)

        for edges in forward_adj.values():
            edges.sort(key=lambda e: (e.right_model, e.name))
        for edges in reverse_adj.values():
            edges.sort(key=lambda e: (e.left_model, e.name))

        return forward_adj, reverse_adj

    def _detect_cycles(self, forward_adj: dict[str, list[JoinEdge]]) -> None:
        # 0 = unvisited, 1 = visiting, 2 = visited
        state: dict[str, int] = {node: 0 for node in forward_adj}

        def dfs(node: str) -> None:
            state[node] = 1
            for edge in forward_adj[node]:
                nxt = edge.right_model
                if state[nxt] == 1:
                    raise FanOutJoinError(
                        f"Join graph contains a cycle involving '{node}' -> '{nxt}'."
                    )
                if state[nxt] == 0:
                    dfs(nxt)
            state[node] = 2

        for node in sorted(forward_adj):
            if state[node] == 0:
                dfs(node)

    def _validate_metrics(self, ingested: IngestedMetadata) -> None:
        for metric in ingested.metrics.values():
            if metric.model not in ingested.models:
                raise UncertifiedEntityError(
                    f"Metric '{metric.name}' references unknown base model '{metric.model}'."
                )
            for formula in (
                metric.expression,
                metric.numerator,
                metric.denominator,
                metric.target_expression,
            ):
                if not formula:
                    continue
                for token in _SQL_IDENT_RE.findall(formula):
                    lowered = token.lower()
                    if lowered in _SQL_KEYWORDS or lowered.isdigit():
                        continue
                    if ingested.security.is_sensitive_identifier(lowered):
                        raise SensitiveColumnError(
                            f"Metric '{metric.name}' formula references blocked sensitive column '{token}'."
                        )

    def resolve_join_path(
        self,
        compiled: CompiledSemanticModel,
        start_model: str,
        target_model: str,
    ) -> tuple[JoinStep, ...]:
        """Find a directed safe join path from start_model to target_model without fan-out."""
        if start_model not in compiled.models:
            raise UncertifiedEntityError(f"Unknown source model '{start_model}'.")
        if target_model not in compiled.models:
            raise UncertifiedEntityError(f"Unknown target model '{target_model}'.")
        if start_model == target_model:
            return ()

        path = self._bfs_forward(compiled.forward_adjacency, start_model, target_model)
        if path is not None:
            return tuple(JoinStep(edge=edge) for edge in path)

        if self._has_reverse_path(compiled.reverse_edges, start_model, target_model):
            raise FanOutJoinError(
                f"Cannot join '{start_model}' -> '{target_model}': traversing a reverse "
                "many_to_one relationship would cause row fan-out."
            )

        start_role = compiled.models[start_model].role
        target_role = compiled.models[target_model].role
        if start_role == "fact" and target_role == "fact":
            raise ChasmTrapError(
                f"Direct path between fact models '{start_model}' and '{target_model}' "
                "is blocked to prevent chasm traps."
            )

        raise SemanticCompilationError(
            f"No safe join path exists from '{start_model}' to '{target_model}'."
        )

    def _bfs_forward(
        self,
        forward_adjacency: dict[str, tuple[JoinEdge, ...]],
        start: str,
        target: str,
    ) -> list[JoinEdge] | None:
        queue: deque[tuple[str, list[JoinEdge]]] = deque([(start, [])])
        visited: set[str] = {start}
        while queue:
            current, path = queue.popleft()
            for edge in forward_adjacency.get(current, ()):
                nxt = edge.right_model
                if nxt in visited:
                    continue
                next_path = [*path, edge]
                if nxt == target:
                    return next_path
                visited.add(nxt)
                queue.append((nxt, next_path))
        return None

    def _has_reverse_path(
        self,
        reverse_edges: dict[str, tuple[JoinEdge, ...]],
        start: str,
        target: str,
    ) -> bool:
        queue: deque[str] = deque([start])
        visited: set[str] = {start}
        while queue:
            current = queue.popleft()
            for edge in reverse_edges.get(current, ()):
                prev = edge.left_model
                if prev == target:
                    return True
                if prev not in visited:
                    visited.add(prev)
                    queue.append(prev)
        return False

    def plan_query(
        self,
        compiled: CompiledSemanticModel,
        metrics: Sequence[str],
        dimensions: Sequence[str] = (),
    ) -> QueryPlan:
        """Compile metrics and dimensions into a single-fact or multi-fact CTE query plan."""
        if not metrics:
            raise SemanticCompilationError("At least one certified metric is required to plan a query.")

        resolved_metrics = tuple(self._resolve_metric(compiled, name) for name in metrics)
        normalized_dims = tuple(dict.fromkeys(dim.strip() for dim in dimensions if dim.strip()))

        for dim_ref in normalized_dims:
            self._validate_dimension_reference(compiled, dim_ref, compiled.security)

        fact_groups: dict[str, list[MetricSpec]] = {}
        for metric in resolved_metrics:
            fact_groups.setdefault(metric.model, []).append(metric)

        if len(fact_groups) == 1:
            fact_model, group_metrics = next(iter(fact_groups.items()))
            join_steps = self._collect_joins_for_fact(compiled, fact_model, normalized_dims)
            subplan = FactSubPlan(
                fact_model=fact_model,
                table=compiled.models[fact_model].table,
                metrics=tuple(group_metrics),
                dimensions=normalized_dims,
                joins=join_steps,
            )
            return QueryPlan(
                strategy="single_fact",
                fact_subplans=(subplan,),
                conformed_dimensions=normalized_dims,
                metrics=resolved_metrics,
            )

        # Multi-fact query: every grouping dimension must belong to a conformed dimension
        # reachable via safe joins from every participating fact model.
        for dim_ref in normalized_dims:
            dim_model, _ = dim_ref.split(".", 1)
            if compiled.models[dim_model].role != "dimension":
                raise ChasmTrapError(
                    f"Multi-fact query dimension '{dim_ref}' is not on a conformed dimension model."
                )
            for fact_model in fact_groups:
                try:
                    self.resolve_join_path(compiled, fact_model, dim_model)
                except SemanticCompilationError as exc:
                    raise ChasmTrapError(
                        f"Dimension '{dim_ref}' is not conformed across fact models "
                        f"({', '.join(fact_groups.keys())}): {exc}"
                    ) from exc

        subplans: list[FactSubPlan] = []
        for fact_model, group_metrics in fact_groups.items():
            join_steps = self._collect_joins_for_fact(compiled, fact_model, normalized_dims)
            subplans.append(
                FactSubPlan(
                    fact_model=fact_model,
                    table=compiled.models[fact_model].table,
                    metrics=tuple(group_metrics),
                    dimensions=normalized_dims,
                    joins=join_steps,
                )
            )

        return QueryPlan(
            strategy="multi_fact_cte",
            fact_subplans=tuple(subplans),
            conformed_dimensions=normalized_dims,
            metrics=resolved_metrics,
        )

    def _resolve_metric(self, compiled: CompiledSemanticModel, metric_name: str) -> MetricSpec:
        metric = compiled.metrics.get(metric_name)
        if metric is None or not metric.certified:
            raise UncertifiedEntityError(
                f"Metric '{metric_name}' is not certified in the semantic catalog."
            )
        return metric

    def _validate_dimension_reference(
        self,
        compiled: CompiledSemanticModel,
        dim_ref: str,
        security: SecurityPolicy,
    ) -> tuple[str, str]:
        if "." not in dim_ref:
            if security.is_sensitive_identifier(dim_ref):
                raise SensitiveColumnError(f"Blocked sensitive column requested: '{dim_ref}'.")
            raise UncertifiedEntityError(
                f"Dimension '{dim_ref}' must be qualified as '<model>.<column>'."
            )
        dim_model, dim_col = dim_ref.split(".", 1)
        if security.is_sensitive_identifier(dim_col):
            raise SensitiveColumnError(f"Blocked sensitive dimension column '{dim_ref}'.")
        model_spec = compiled.models.get(dim_model)
        if model_spec is None:
            raise UncertifiedEntityError(f"Unknown dimension model '{dim_model}' in '{dim_ref}'.")
        if dim_col.lower() not in model_spec.column_map:
            raise UncertifiedEntityError(
                f"Column '{dim_col}' is not registered on certified model '{dim_model}'."
            )
        return dim_model, dim_col

    def _collect_joins_for_fact(
        self,
        compiled: CompiledSemanticModel,
        fact_model: str,
        dimensions: Sequence[str],
    ) -> tuple[JoinStep, ...]:
        ordered_steps: list[JoinStep] = []
        seen_edges: set[str] = set()
        for dim_ref in dimensions:
            dim_model, _ = dim_ref.split(".", 1)
            for step in self.resolve_join_path(compiled, fact_model, dim_model):
                if step.edge.name not in seen_edges:
                    seen_edges.add(step.edge.name)
                    ordered_steps.append(step)
        return tuple(ordered_steps)
