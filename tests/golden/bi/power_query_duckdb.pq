// Auto-generated Power Query M connector for DuckDB (ADR-003)
// Zero embedded secrets; configure pDatabasePath and pSchemaName in Power BI.
let
    pDatabasePath = "dbt/target/sbg_semantic_demo.duckdb" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true],
    pSchemaName = "main" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true],
    Source = Odbc.DataSource("Driver={DuckDB Driver};Database=" & pDatabasePath & ";access_mode=READ_ONLY", [HierarchicalNavigation=true]),
    SchemaNav = Source{[Name=pSchemaName,Kind="Schema"]}[Data],
    dim_customer_segment = Table.TransformColumnTypes(
        SchemaNav{[Name="dim_customer_segment",Kind="Table"]}[Data],
        {
                {"segment_id", type text},
                {"segment_name", type text},
                {"tier_group", type text},
                {"account_band", type text},
                {"is_enterprise", type logical}
        }
    ),
    dim_date = Table.TransformColumnTypes(
        SchemaNav{[Name="dim_date",Kind="Table"]}[Data],
        {
                {"date_day", type date},
                {"year_num", Int64.Type},
                {"quarter_num", Int64.Type},
                {"quarter_label", type text},
                {"month_num", Int64.Type},
                {"month_name", type text},
                {"year_month", type text},
                {"is_weekend", type logical},
                {"fiscal_year", Int64.Type}
        }
    ),
    dim_region = Table.TransformColumnTypes(
        SchemaNav{[Name="dim_region",Kind="Table"]}[Data],
        {
                {"region_id", type text},
                {"region_name", type text},
                {"uf_code", type text},
                {"macro_region", type text},
                {"country_code", type text},
                {"currency_code", type text}
        }
    ),
    dim_service_tier = Table.TransformColumnTypes(
        SchemaNav{[Name="dim_service_tier",Kind="Table"]}[Data],
        {
                {"service_tier_id", type text},
                {"service_tier_name", type text},
                {"priority_level", type text},
                {"sla_target_hours", type number},
                {"support_window", type text}
        }
    ),
    fct_revenue_orders = Table.TransformColumnTypes(
        SchemaNav{[Name="fct_revenue_orders",Kind="Table"]}[Data],
        {
                {"order_id", type text},
                {"order_date", type date},
                {"region_id", type text},
                {"segment_id", type text},
                {"service_tier_id", type text},
                {"gross_revenue_brl", type number},
                {"discount_brl", type number},
                {"net_revenue_brl", type number},
                {"direct_cost_brl", type number},
                {"gross_margin_brl", type number},
                {"target_revenue_brl", type number},
                {"order_status", type text}
        }
    ),
    fct_sla_tickets = Table.TransformColumnTypes(
        SchemaNav{[Name="fct_sla_tickets",Kind="Table"]}[Data],
        {
                {"ticket_id", type text},
                {"created_date", type date},
                {"region_id", type text},
                {"segment_id", type text},
                {"service_tier_id", type text},
                {"resolution_hours", type number},
                {"is_sla_met", Int64.Type},
                {"is_breached", Int64.Type},
                {"ticket_status", type text}
        }
    ),
    SemanticModels = [
        dim_customer_segment = dim_customer_segment,
        dim_date = dim_date,
        dim_region = dim_region,
        dim_service_tier = dim_service_tier,
        fct_revenue_orders = fct_revenue_orders,
        fct_sla_tickets = fct_sla_tickets
    ]
in
    SemanticModels
