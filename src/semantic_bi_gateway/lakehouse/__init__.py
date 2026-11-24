"""Multi-lakehouse read-only bridge, Client Path Shield, and synthetic DuckDB bootstrap."""

from semantic_bi_gateway.lakehouse.registry import (
    DEFAULT_LAKEHOUSE_SPECS,
    BridgedQueryResult,
    LakehouseSpec,
    LakehouseStatus,
    MultiLakehouseBridge,
    ResolvedLakehouse,
)
from semantic_bi_gateway.lakehouse.shield import (
    ClientPathShield,
    ClientPathShieldError,
    SourceMode,
)
from semantic_bi_gateway.lakehouse.synthetic_bootstrap import materialize_synthetic_duckdb

__all__ = [
    "BridgedQueryResult",
    "ClientPathShield",
    "ClientPathShieldError",
    "DEFAULT_LAKEHOUSE_SPECS",
    "LakehouseSpec",
    "LakehouseStatus",
    "MultiLakehouseBridge",
    "ResolvedLakehouse",
    "SourceMode",
    "materialize_synthetic_duckdb",
]
