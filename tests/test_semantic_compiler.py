"""Offline tests for dbt metadata ingestion and fan-out/chasm-safe semantic compilation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from semantic_bi_gateway.semantic import (
    ChasmTrapError,
    ColumnSpec,
    DbtMetadataIngestor,
    FanOutJoinError,
    JoinEdge,
    SemanticCompilationError,
    SemanticCompiler,
    SensitiveColumnError,
    UncertifiedEntityError,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
METRICS_PATH = REPO_ROOT / "catalog" / "metrics.yaml"
MART_SCHEMA_PATH = REPO_ROOT / "dbt" / "models" / "marts" / "schema.yml"
STAGING_SCHEMA_PATH = REPO_ROOT / "dbt" / "models" / "staging" / "_staging.yml"
MANIFEST_PATH = REPO_ROOT / "dbt" / "target" / "manifest.json"


def test_ingest_from_dbt_schema_yml_extracts_all_fks() -> None:
    ingestor = DbtMetadataIngestor()
    ingested = ingestor.ingest(
        METRICS_PATH,
        schema_paths=[STAGING_SCHEMA_PATH, MART_SCHEMA_PATH],
    )
    assert ingested.dbt_snapshot is not None
    assert len(ingested.dbt_snapshot.models) == 11
    fks = {
        (fk.from_model, fk.from_column, fk.to_model, fk.to_column)
        for fk in ingested.dbt_snapshot.foreign_keys
    }
    expected_fks = {
        ("fct_revenue_orders", "order_date", "dim_date", "date_day"),
        ("fct_revenue_orders", "region_id", "dim_region", "region_id"),
        ("fct_revenue_orders", "segment_id", "dim_customer_segment", "segment_id"),
        ("fct_revenue_orders", "service_tier_id", "dim_service_tier", "service_tier_id"),
        ("fct_sla_tickets", "created_date", "dim_date", "date_day"),
        ("fct_sla_tickets", "region_id", "dim_region", "region_id"),
        ("fct_sla_tickets", "segment_id", "dim_customer_segment", "segment_id"),
        ("fct_sla_tickets", "service_tier_id", "dim_service_tier", "service_tier_id"),
    }
    assert expected_fks.issubset(fks)


def test_ingest_from_manifest_json_extracts_models_and_fks(tmp_path: Path) -> None:
    ingestor = DbtMetadataIngestor()
    synthetic_manifest = {
        "nodes": {
            "model.sbg_semantic_demo.dim_date": {"resource_type": "model", "name": "dim_date", "description": "Calendar", "columns": {"date_day": {"description": "Day"}}},
            "model.sbg_semantic_demo.dim_region": {"resource_type": "model", "name": "dim_region", "description": "Region", "columns": {"region_id": {"description": "Region ID"}}},
            "model.sbg_semantic_demo.dim_customer_segment": {"resource_type": "model", "name": "dim_customer_segment", "description": "Seg", "columns": {"segment_id": {"description": "Seg ID"}}},
            "model.sbg_semantic_demo.dim_service_tier": {"resource_type": "model", "name": "dim_service_tier", "description": "Tier", "columns": {"service_tier_id": {"description": "Tier ID"}}},
            "model.sbg_semantic_demo.fct_revenue_orders": {"resource_type": "model", "name": "fct_revenue_orders", "description": "Orders", "columns": {"order_id": {"description": "Order"}}},
            "model.sbg_semantic_demo.fct_sla_tickets": {"resource_type": "model", "name": "fct_sla_tickets", "description": "Tickets", "columns": {"ticket_id": {"description": "Ticket"}}},
            "test.sbg_semantic_demo.relationships_fct_revenue_orders_region_id": {
                "resource_type": "test",
                "column_name": "region_id",
                "test_metadata": {
                    "name": "relationships",
                    "kwargs": {
                        "model": "{{ get_where_subquery(ref('fct_revenue_orders')) }}",
                        "column_name": "region_id",
                        "to": "ref('dim_region')",
                        "field": "region_id",
                    },
                },
            },
        }
    }
    tmp_manifest = tmp_path / "manifest.json"
    tmp_manifest.write_text(json.dumps(synthetic_manifest), encoding="utf-8")

    ingested = ingestor.ingest(METRICS_PATH, manifest_path=tmp_manifest)
    assert ingested.dbt_snapshot is not None
    assert any(
        fk.from_model == "fct_revenue_orders" and fk.to_model == "dim_region"
        for fk in ingested.dbt_snapshot.foreign_keys
    )

    if MANIFEST_PATH.is_file():
        real_ingested = ingestor.ingest(METRICS_PATH, manifest_path=MANIFEST_PATH)
        assert real_ingested.dbt_snapshot is not None
        assert len(real_ingested.dbt_snapshot.models) == 11
        assert len(real_ingested.dbt_snapshot.foreign_keys) == 8


def test_compile_and_plan_single_fact_query() -> None:
    ingestor = DbtMetadataIngestor()
    compiler = SemanticCompiler()
    compiled = compiler.compile(ingestor.ingest(METRICS_PATH, schema_paths=[MART_SCHEMA_PATH]))

    plan = compiler.plan_query(
        compiled,
        metrics=["total_revenue", "gross_margin_pct"],
        dimensions=["dim_region.uf_code", "dim_date.year_month"],
    )
    assert plan.strategy == "single_fact"
    assert len(plan.fact_subplans) == 1
    subplan = plan.fact_subplans[0]
    assert subplan.fact_model == "fct_revenue_orders"
    joined_targets = [step.edge.right_model for step in subplan.joins]
    assert joined_targets == ["dim_region", "dim_date"]


def test_multi_fact_query_uses_conformed_cte_strategy() -> None:
    ingestor = DbtMetadataIngestor()
    compiler = SemanticCompiler()
    compiled = compiler.compile(ingestor.ingest(METRICS_PATH, schema_paths=[MART_SCHEMA_PATH]))

    plan = compiler.plan_query(
        compiled,
        metrics=["total_revenue", "sla_compliance_rate"],
        dimensions=["dim_region.region_name", "dim_date.year_month"],
    )
    assert plan.strategy == "multi_fact_cte"
    assert [sp.fact_model for sp in plan.fact_subplans] == [
        "fct_revenue_orders",
        "fct_sla_tickets",
    ]


def test_multi_fact_query_rejects_non_conformed_dimension_chasm_trap() -> None:
    ingestor = DbtMetadataIngestor()
    compiler = SemanticCompiler()
    compiled = compiler.compile(ingestor.ingest(METRICS_PATH))

    with pytest.raises(ChasmTrapError):
        compiler.plan_query(
            compiled,
            metrics=["total_revenue", "sla_compliance_rate"],
            dimensions=["fct_revenue_orders.order_status"],
        )


def test_fan_out_prevention_rejects_unsafe_cardinality_cycles_and_reverse_joins() -> None:
    ingestor = DbtMetadataIngestor()
    compiler = SemanticCompiler()
    base = ingestor.ingest(METRICS_PATH)

    # 1. many_to_many / one_to_many join edge rejected at compile time
    bad_m2m = replace(
        base,
        allowed_joins=(
            *base.allowed_joins,
            JoinEdge(
                name="bad_m2m",
                left_model="dim_region",
                right_model="dim_service_tier",
                left_key="region_id",
                right_key="service_tier_id",
                join_type="left",
                cardinality="many_to_many",
                safe_for_aggregation=True,
            ),
        ),
    )
    with pytest.raises(FanOutJoinError):
        compiler.compile(bad_m2m)

    # 2. safe_for_aggregation=False rejected at compile time
    unsafe_flag = replace(
        base,
        allowed_joins=(replace(base.allowed_joins[0], safe_for_aggregation=False),),
    )
    with pytest.raises(FanOutJoinError):
        compiler.compile(unsafe_flag)

    # 3. Cyclic join graph rejected at compile time
    cyclic = replace(
        base,
        allowed_joins=(
            *base.allowed_joins,
            JoinEdge(
                name="dim_a_to_b",
                left_model="dim_region",
                right_model="dim_service_tier",
                left_key="region_id",
                right_key="service_tier_id",
                join_type="left",
                cardinality="one_to_one",
                safe_for_aggregation=True,
            ),
            JoinEdge(
                name="dim_b_to_a",
                left_model="dim_service_tier",
                right_model="dim_region",
                left_key="service_tier_id",
                right_key="region_id",
                join_type="left",
                cardinality="one_to_one",
                safe_for_aggregation=True,
            ),
        ),
    )
    with pytest.raises(FanOutJoinError):
        compiler.compile(cyclic)

    # 4. Reverse traversal of many_to_one (dim -> fact) rejected during path resolution
    compiled = compiler.compile(base)
    with pytest.raises(FanOutJoinError):
        compiler.resolve_join_path(compiled, "dim_region", "fct_revenue_orders")


def test_direct_fact_to_fact_join_rejected_as_chasm_trap() -> None:
    ingestor = DbtMetadataIngestor()
    compiler = SemanticCompiler()
    base = ingestor.ingest(METRICS_PATH)

    fact_to_fact = replace(
        base,
        allowed_joins=(
            *base.allowed_joins,
            JoinEdge(
                name="illegal_fact_join",
                left_model="fct_revenue_orders",
                right_model="fct_sla_tickets",
                left_key="region_id",
                right_key="region_id",
                join_type="left",
                cardinality="many_to_one",
                safe_for_aggregation=True,
            ),
        ),
    )
    with pytest.raises(ChasmTrapError):
        compiler.compile(fact_to_fact)


def test_uncertified_entities_and_sensitive_columns_rejected() -> None:
    ingestor = DbtMetadataIngestor()
    compiler = SemanticCompiler()
    base = ingestor.ingest(METRICS_PATH)
    compiled = compiler.compile(base)

    with pytest.raises(UncertifiedEntityError):
        compiler.plan_query(compiled, metrics=["hallucinated_kpi"])

    with pytest.raises(SensitiveColumnError):
        compiler.plan_query(compiled, metrics=["total_revenue"], dimensions=["dim_region.cpf"])

    with pytest.raises(SensitiveColumnError):
        compiler.plan_query(compiled, metrics=["total_revenue"], dimensions=["email"])

    with pytest.raises(SemanticCompilationError):
        compiler.plan_query(compiled, metrics=[])

    # Certified model attempting to expose a PII column fails compilation
    dim_region = base.models["dim_region"]
    leaked_model = replace(
        dim_region,
        columns=(
            *dim_region.columns,
            ColumnSpec(name="cpf", data_type="VARCHAR", role="attribute", description="PII"),
        ),
    )
    bad_models = {**base.models, "dim_region": leaked_model}
    with pytest.raises(SensitiveColumnError):
        compiler.compile(replace(base, models=bad_models))
