"""Offline tests for deterministic Power BI codegen, golden files, and syntax/WCAG validators."""

from __future__ import annotations

import json
from pathlib import Path

from semantic_bi_gateway.bi import (
    TARGET_REQUIRED_PARAMS,
    PowerBiCodeGenerator,
    contrast_ratio,
    generate_corporate_theme,
    validate_dax_syntax,
    validate_power_query_syntax,
    validate_tmdl_syntax,
    validate_wcag_aa_theme,
)
from semantic_bi_gateway.semantic import DbtMetadataIngestor, SemanticCompiler

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = REPO_ROOT / "tests" / "golden" / "bi"


def _compile_default_model():
    ingested = DbtMetadataIngestor().ingest(
        REPO_ROOT / "catalog" / "metrics.yaml",
        schema_paths=[REPO_ROOT / "dbt" / "models" / "marts" / "schema.yml"],
    )
    return SemanticCompiler().compile(ingested)


def test_powerbi_codegen_matches_golden_files_byte_for_byte(tmp_path: Path) -> None:
    compiled = _compile_default_model()
    bundle = PowerBiCodeGenerator().generate(compiled, output_dir=tmp_path)

    assert bundle.model_count == 6
    assert bundle.metric_count == 8
    assert bundle.measure_count == 52

    expected_files = (
        "corporate_theme.json",
        "kpi_dictionary.md",
        "measures.dax",
        "measures.tmdl",
        "power_query_databricks.pq",
        "power_query_duckdb.pq",
        "power_query_snowflake.pq",
    )
    for filename in expected_files:
        golden_path = GOLDEN_DIR / filename
        generated_path = tmp_path / filename
        assert golden_path.is_file(), f"Missing golden file: {golden_path}"
        assert generated_path.is_file(), f"Missing generated file: {generated_path}"
        assert generated_path.read_bytes() == golden_path.read_bytes(), (
            f"Determinism mismatch against golden file: {filename}"
        )


def test_structural_validators_accept_generated_artifacts() -> None:
    compiled = _compile_default_model()
    bundle = PowerBiCodeGenerator().generate(compiled)

    dax_ok, dax_errors = validate_dax_syntax(bundle.artifacts["measures.dax"])
    assert dax_ok, f"DAX errors: {dax_errors}"

    tmdl_ok, tmdl_errors = validate_tmdl_syntax(bundle.artifacts["measures.tmdl"])
    assert tmdl_ok, f"TMDL errors: {tmdl_errors}"

    for target, params in TARGET_REQUIRED_PARAMS.items():
        pq_ok, pq_errors = validate_power_query_syntax(
            bundle.artifacts[f"power_query_{target}.pq"],
            params,
        )
        assert pq_ok, f"Power Query {target} errors: {pq_errors}"

    theme_ok, theme_errors = validate_wcag_aa_theme(
        json.loads(bundle.artifacts["corporate_theme.json"])
    )
    assert theme_ok, f"WCAG theme errors: {theme_errors}"
    assert contrast_ratio("#0F172A", "#FFFFFF") >= 4.5


def test_negative_validators_reject_broken_dax_pq_and_low_contrast_theme() -> None:
    bad_dax = "DEFINE\nMEASURE '_Measures'[Bad] = SUM('fct'[a] / COUNT('fct'[b])"
    dax_ok, dax_errors = validate_dax_syntax(bad_dax)
    assert not dax_ok
    assert dax_errors

    bad_pq = 'let\n    Source = Odbc.DataSource("pwd=supersecret"),\n    Out = Source'
    pq_ok, pq_errors = validate_power_query_syntax(bad_pq, ("pDatabasePath", "pSchemaName"))
    assert not pq_ok
    assert pq_errors

    theme = generate_corporate_theme("broken-theme")
    theme["foreground"] = "#CBD5E1"  # Light gray on #FFFFFF fails WCAG AA (< 4.5:1)
    theme_ok, theme_errors = validate_wcag_aa_theme(theme)
    assert not theme_ok
    assert any("foreground" in err for err in theme_errors)
