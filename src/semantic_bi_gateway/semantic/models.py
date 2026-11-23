"""Immutable semantic-layer domain models and compilation exceptions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


class SemanticCompilationError(Exception):
    """Base error raised when semantic catalog compilation or query planning fails."""


class FanOutJoinError(SemanticCompilationError):
    """Raised when a join path would multiply fact rows (N:M, 1:N, unsafe, or cyclic)."""


class ChasmTrapError(SemanticCompilationError):
    """Raised when joining multiple facts directly or grouping multi-fact queries by non-conformed fields."""


class UncertifiedEntityError(SemanticCompilationError):
    """Raised when referencing an unknown or uncertified model, column, or metric."""


class SensitiveColumnError(SemanticCompilationError):
    """Raised when a blocked PII/sensitive column is exposed or queried."""


@dataclass(frozen=True)
class ColumnSpec:
    """Typed semantic column definition."""

    name: str
    data_type: str
    role: str
    description: str
    sensitive: bool = False
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class ForeignKeySpec:
    """Foreign key relationship extracted from dbt schema.yml or manifest.json."""

    from_model: str
    from_column: str
    to_model: str
    to_column: str
    source: str = "dbt"


@dataclass(frozen=True)
class ModelSpec:
    """Semantic fact or dimension model definition."""

    name: str
    role: Literal["fact", "dimension"]
    table: str
    grain: str
    primary_key: str
    time_dimension: str | None
    description: str
    columns: tuple[ColumnSpec, ...]
    source_lakehouse: str | None = None

    @property
    def column_map(self) -> dict[str, ColumnSpec]:
        """Return mapping of lowercase column names to ColumnSpec."""
        return {col.name.lower(): col for col in self.columns}


@dataclass(frozen=True)
class JoinEdge:
    """Directed join edge in the semantic graph."""

    name: str
    left_model: str
    right_model: str
    left_key: str
    right_key: str
    join_type: Literal["left", "inner"]
    cardinality: str
    safe_for_aggregation: bool


@dataclass(frozen=True)
class MetricSpec:
    """Certified semantic KPI metric definition."""

    name: str
    label: str
    description: str
    model: str
    metric_type: Literal["simple", "ratio", "derived", "cumulative"]
    format: str
    time_intelligence: bool
    dimensions: tuple[str, ...]
    owner: str
    certified: bool
    expression: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    target_expression: str | None = None
    display_folder: str | None = None
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecurityPolicy:
    """Catalog-level security allowlist and PII blocklist rules."""

    default_limit: int
    max_limit: int
    query_timeout_seconds: float
    blocked_sensitive_columns: frozenset[str]
    blocked_column_patterns: tuple[re.Pattern[str], ...]
    allowed_functions: frozenset[str]

    def is_sensitive_identifier(self, identifier: str) -> bool:
        """Return True if identifier matches an explicit sensitive column or PII regex."""
        lowered = identifier.strip().lower()
        if lowered in self.blocked_sensitive_columns:
            return True
        return any(pattern.search(lowered) for pattern in self.blocked_column_patterns)


@dataclass(frozen=True)
class DbtDiscoveredModel:
    """Model metadata extracted from dbt manifest.json or schema.yml."""

    name: str
    description: str
    columns: dict[str, str]
    unique_id: str | None = None


@dataclass(frozen=True)
class DbtMetadataSnapshot:
    """Normalized snapshot of dbt models, column docs, and foreign key relationships."""

    models: dict[str, DbtDiscoveredModel]
    foreign_keys: tuple[ForeignKeySpec, ...]


@dataclass(frozen=True)
class IngestedMetadata:
    """Merged semantic catalog and optional dbt project metadata prior to compilation."""

    schema_version: str
    project_name: str
    default_dialect: str
    security: SecurityPolicy
    models: dict[str, ModelSpec]
    allowed_joins: tuple[JoinEdge, ...]
    metrics: dict[str, MetricSpec]
    dbt_snapshot: DbtMetadataSnapshot | None = None


@dataclass(frozen=True)
class JoinStep:
    """Single directed join step along a compiled join path."""

    edge: JoinEdge


@dataclass(frozen=True)
class FactSubPlan:
    """Execution plan slice for a single fact table and its safe dimension joins."""

    fact_model: str
    table: str
    metrics: tuple[MetricSpec, ...]
    dimensions: tuple[str, ...]
    joins: tuple[JoinStep, ...]


@dataclass(frozen=True)
class QueryPlan:
    """Compiled logical query plan free of fan-out and chasm traps."""

    strategy: Literal["single_fact", "multi_fact_cte"]
    fact_subplans: tuple[FactSubPlan, ...]
    conformed_dimensions: tuple[str, ...]
    metrics: tuple[MetricSpec, ...]


@dataclass(frozen=True)
class CompiledSemanticModel:
    """Validated, graph-compiled semantic catalog ready for BI codegen and NL2SQL."""

    schema_version: str
    project_name: str
    default_dialect: str
    security: SecurityPolicy
    models: dict[str, ModelSpec]
    metrics: dict[str, MetricSpec]
    allowed_joins: tuple[JoinEdge, ...]
    forward_adjacency: dict[str, tuple[JoinEdge, ...]]
    reverse_edges: dict[str, tuple[JoinEdge, ...]]
    foreign_keys: tuple[ForeignKeySpec, ...]
