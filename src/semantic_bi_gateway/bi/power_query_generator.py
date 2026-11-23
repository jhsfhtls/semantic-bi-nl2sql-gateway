"""Parameterized Power Query M generator for DuckDB, Databricks, and Snowflake."""

from __future__ import annotations

from typing import Literal

from semantic_bi_gateway.semantic.models import CompiledSemanticModel, ModelSpec

PowerQueryTarget = Literal["duckdb", "databricks", "snowflake"]

TARGET_REQUIRED_PARAMS: dict[PowerQueryTarget, tuple[str, ...]] = {
    "duckdb": ("pDatabasePath", "pSchemaName"),
    "databricks": ("pServerHostname", "pHttpPath", "pCatalog", "pSchema"),
    "snowflake": ("pAccountServer", "pWarehouse", "pDatabase", "pSchema"),
}


def map_sql_type_to_power_query(sql_type: str) -> str:
    """Map semantic catalog SQL types to canonical Power Query M column types."""
    normalized = sql_type.strip().upper()
    if normalized in {"INTEGER", "BIGINT"}:
        return "Int64.Type"
    if normalized == "BOOLEAN":
        return "type logical"
    if normalized == "DATE":
        return "type date"
    if normalized == "TIMESTAMP":
        return "type datetime"
    if normalized == "DOUBLE" or normalized.startswith("DECIMAL("):
        return "type number"
    return "type text"


def _render_type_pairs(model: ModelSpec) -> list[str]:
    pairs: list[str] = []
    for idx, col in enumerate(model.columns):
        m_type = map_sql_type_to_power_query(col.data_type)
        comma = "," if idx < len(model.columns) - 1 else ""
        pairs.append(f'                {{"{col.name}", {m_type}}}{comma}')
    return pairs


def _header_for_target(target: PowerQueryTarget) -> list[str]:
    if target == "duckdb":
        return [
            "// Auto-generated Power Query M connector for DuckDB (ADR-003)",
            "// Zero embedded secrets; configure pDatabasePath and pSchemaName in Power BI.",
            "let",
            '    pDatabasePath = "dbt/target/sbg_semantic_demo.duckdb" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true],',
            '    pSchemaName = "main" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true],',
            '    Source = Odbc.DataSource("Driver={DuckDB Driver};Database=" & pDatabasePath & ";access_mode=READ_ONLY", [HierarchicalNavigation=true]),',
            '    SchemaNav = Source{[Name=pSchemaName,Kind="Schema"]}[Data],',
        ]
    if target == "databricks":
        return [
            "// Auto-generated Power Query M connector for Databricks Unity Catalog (ADR-003)",
            "// Zero embedded secrets; authenticate via Power BI OAuth / AAD organizational account.",
            "let",
            '    pServerHostname = "adb-example.azuredatabricks.net" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true],',
            '    pHttpPath = "/sql/1.0/warehouses/example" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true],',
            '    pCatalog = "analytics_prod" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true],',
            '    pSchema = "semantic_marts" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true],',
            "    Source = Databricks.Catalogs(pServerHostname, pHttpPath, [Catalog=pCatalog, Database=pSchema]),",
            '    CatalogNav = Source{[Name=pCatalog,Kind="Database"]}[Data],',
            '    SchemaNav = CatalogNav{[Name=pSchema,Kind="Schema"]}[Data],',
        ]
    if target == "snowflake":
        return [
            "// Auto-generated Power Query M connector for Snowflake (ADR-003)",
            "// Zero embedded secrets; authenticate via Power BI SSO / OAuth.",
            "let",
            '    pAccountServer = "org-account.snowflakecomputing.com" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true],',
            '    pWarehouse = "BI_READONLY_WH" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true],',
            '    pDatabase = "ANALYTICS_PROD" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true],',
            '    pSchema = "SEMANTIC_MARTS" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true],',
            "    Source = Snowflake.Databases(pAccountServer, pWarehouse),",
            '    DatabaseNav = Source{[Name=pDatabase,Kind="Database"]}[Data],',
            '    SchemaNav = DatabaseNav{[Name=pSchema,Kind="Schema"]}[Data],',
        ]
    raise ValueError(f"Unsupported Power Query target: {target}")


def generate_power_query(compiled: CompiledSemanticModel, target: PowerQueryTarget) -> str:
    """Render deterministic Power Query M script loading all certified semantic models."""
    lines = _header_for_target(target)
    model_names = sorted(compiled.models)

    for model_name in model_names:
        model = compiled.models[model_name]
        table_name = model.table.split(".")[-1]
        lines.extend(
            [
                f"    {model_name} = Table.TransformColumnTypes(",
                f'        SchemaNav{{[Name="{table_name}",Kind="Table"]}}[Data],',
                "        {",
                *_render_type_pairs(model),
                "        }",
                "    ),",
            ]
        )

    lines.append("    SemanticModels = [")
    for idx, model_name in enumerate(model_names):
        comma = "," if idx < len(model_names) - 1 else ""
        lines.append(f"        {model_name} = {model_name}{comma}")
    lines.extend(["    ]", "in", "    SemanticModels", ""])
    return "\n".join(lines)
