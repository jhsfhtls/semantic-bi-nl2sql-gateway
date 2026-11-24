"""AST-based SQL security firewall enforcing SELECT-only, allowlists, PII blocks, and LIMIT."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from semantic_bi_gateway.semantic.models import CompiledSemanticModel

_MAX_SQL_LENGTH = 8192

_BLOCKED_AST_NODE_NAMES = (
    "Command",
    "Drop",
    "Delete",
    "Insert",
    "Update",
    "Create",
    "Alter",
    "Attach",
    "Detach",
    "Copy",
    "Pragma",
    "Set",
    "Transaction",
    "Commit",
    "Rollback",
    "Grant",
    "Revoke",
    "Merge",
    "TruncateTable",
    "LoadData",
    "Export",
    "Install",
    "Union",
    "Intersect",
    "Except",
)

_BLOCKED_AST_CLASSES: tuple[type[exp.Expression], ...] = tuple(
    cls for name in _BLOCKED_AST_NODE_NAMES if (cls := getattr(exp, name, None)) is not None
)

_DANGEROUS_FUNC_PREFIXES = (
    "READ_",
    "HTTP_",
    "SQLITE_",
    "POSTGRES_",
    "MYSQL_",
    "ICEBERG_",
    "DELTA_",
    "DUCKDB_",
    "PRAGMA_",
    "EXPORT_",
    "IMPORT_",
    "ATTACH_",
    "SNIFF_",
)

_DANGEROUS_FUNC_EXACT = frozenset(
    {
        "GLOB",
        "GETENV",
        "CURRENT_SETTING",
        "CALL",
        "COPY_TO",
        "QUERY",
        "QUERY_TABLE",
    }
)

# Maps catalog allowed_functions names to sqlglot internal AST sql_name() equivalents.
_SQLGLOT_FUNC_SYNONYMS: dict[str, tuple[str, ...]] = {
    "DATE_TRUNC": ("DATE_TRUNC", "TIMESTAMP_TRUNC"),
    "STRFTIME": ("STRFTIME", "TIME_TO_STR"),
    "CASE": ("CASE", "IF"),
}


class SqlFirewallViolation(Exception):
    """Raised when untrusted SQL or input violates AstSqlFirewall policy."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"[{code}] {reason}")


@dataclass(frozen=True)
class FirewallDecision:
    """Audit record returned after AstSqlFirewall validation."""

    allowed: bool
    sanitized_sql: str
    applied_limit: int
    referenced_tables: tuple[str, ...]
    referenced_columns: tuple[str, ...]
    reason: str = "OK"


class AstSqlFirewall:
    """Validates and normalizes untrusted DuckDB SQL exclusively via sqlglot AST inspection."""

    def __init__(self, compiled: CompiledSemanticModel) -> None:
        self._compiled = compiled
        self._certified_tables: dict[str, set[str]] = {
            model.table.split(".")[-1].lower(): {col.name.lower() for col in model.columns}
            for model in compiled.models.values()
        }
        self._all_certified_columns: set[str] = set().union(*self._certified_tables.values())
        self._allowed_funcs = self._build_allowed_function_names()

    def _build_allowed_function_names(self) -> frozenset[str]:
        allowed: set[str] = set()
        for fn in self._compiled.security.allowed_functions:
            upper = fn.upper()
            allowed.add(upper)
            allowed.update(_SQLGLOT_FUNC_SYNONYMS.get(upper, ()))
        return frozenset(allowed)

    def normalize_and_precheck(self, raw_sql: str) -> str:
        """Apply pre-parse length, control-char, and Unicode NFKC obfuscation checks."""
        if not isinstance(raw_sql, str) or not raw_sql.strip():
            raise SqlFirewallViolation("EMPTY_SQL", "SQL query must be a non-empty string.")
        if len(raw_sql) > _MAX_SQL_LENGTH:
            raise SqlFirewallViolation(
                "QUERY_TOO_LARGE",
                f"SQL query exceeds maximum length ({_MAX_SQL_LENGTH} characters).",
            )
        if "\x00" in raw_sql:
            raise SqlFirewallViolation("NULL_BYTE_DETECTED", "Null bytes are forbidden in SQL.")

        for ch in raw_sql:
            category = unicodedata.category(ch)
            if category == "Cf":
                raise SqlFirewallViolation(
                    "OBFUSCATION_UNICODE_CF",
                    "Invisible or bidirectional Unicode format characters are forbidden.",
                )
            if category == "Cc" and ch not in ("\n", "\r", "\t"):
                raise SqlFirewallViolation(
                    "CONTROL_CHAR_DETECTED",
                    "Control characters are forbidden in SQL.",
                )

        normalized = unicodedata.normalize("NFKC", raw_sql).strip()
        if not normalized:
            raise SqlFirewallViolation("EMPTY_SQL", "SQL query is empty after normalization.")
        return normalized

    def validate(self, raw_sql: str) -> FirewallDecision:
        """Parse and validate SQL against SELECT-only, allowlist, PII, and LIMIT rules."""
        normalized = self.normalize_and_precheck(raw_sql)

        try:
            parsed = sqlglot.parse(normalized, read="duckdb")
        except ParseError as exc:
            raise SqlFirewallViolation("PARSE_ERROR", f"Invalid SQL syntax: {exc}") from exc

        non_null_statements = [stmt for stmt in parsed if stmt is not None]
        if len(non_null_statements) != 1:
            raise SqlFirewallViolation(
                "MULTI_STATEMENT",
                f"Expected a single SELECT statement; received {len(non_null_statements)}.",
            )

        stmt = non_null_statements[0]
        if not isinstance(stmt, exp.Select):
            raise SqlFirewallViolation(
                "NON_SELECT_STATEMENT",
                f"Only SELECT queries are permitted (got {type(stmt).__name__}).",
            )

        self._check_forbidden_nodes(stmt)
        cte_columns = self._extract_cte_definitions(stmt)
        referenced_tables, referenced_columns = self._validate_scope_tree(
            stmt=stmt,
            cte_columns=cte_columns,
        )
        applied_limit = self._enforce_limit(stmt)
        sanitized_sql = stmt.sql(dialect="duckdb", comments=False)

        return FirewallDecision(
            allowed=True,
            sanitized_sql=sanitized_sql,
            applied_limit=applied_limit,
            referenced_tables=tuple(sorted(referenced_tables)),
            referenced_columns=tuple(sorted(referenced_columns)),
        )

    def _check_forbidden_nodes(self, stmt: exp.Select) -> None:
        for node in stmt.walk():
            if isinstance(node, _BLOCKED_AST_CLASSES):
                raise SqlFirewallViolation(
                    "FORBIDDEN_SQL_OPERATION",
                    f"Operation '{type(node).__name__}' is prohibited by AST firewall.",
                )
            if isinstance(node, exp.With) and node.args.get("recursive"):
                raise SqlFirewallViolation(
                    "RECURSIVE_CTE_BLOCKED",
                    "Recursive CTEs are prohibited to prevent unbounded execution.",
                )
            if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count):
                raise SqlFirewallViolation(
                    "WILDCARD_SELECT_BLOCKED",
                    "Wildcard '*' selection is prohibited; select certified columns explicitly.",
                )
            if isinstance(node, exp.Func) and not isinstance(node, exp.Connector):
                self._validate_function_node(node)
            if isinstance(node, exp.Alias):
                alias_name = node.alias
                if alias_name and self._compiled.security.is_sensitive_identifier(alias_name):
                    raise SqlFirewallViolation(
                        "SENSITIVE_ALIAS_BLOCKED",
                        f"Alias '{alias_name}' matches a blocked sensitive identifier.",
                    )

    def _validate_function_node(self, node: exp.Func) -> None:
        raw_name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()) or ""
        fn_name = raw_name.upper()
        if not fn_name:
            fn_name = type(node).__name__.upper()

        if fn_name in _DANGEROUS_FUNC_EXACT or fn_name.startswith(_DANGEROUS_FUNC_PREFIXES):
            raise SqlFirewallViolation(
                "BLOCKED_FUNCTION",
                f"Dangerous DuckDB/IO function '{fn_name}' is prohibited.",
            )

        if isinstance(node, exp.Anonymous) or fn_name not in self._allowed_funcs:
            if isinstance(node, exp.Anonymous) and fn_name in self._allowed_funcs:
                return
            raise SqlFirewallViolation(
                "UNALLOWLISTED_FUNCTION",
                f"SQL function '{fn_name}' is not in security.allowed_functions.",
            )

    def _extract_cte_definitions(self, stmt: exp.Select) -> dict[str, set[str]]:
        cte_columns: dict[str, set[str]] = {}
        with_clause = stmt.args.get("with_")
        if not isinstance(with_clause, exp.With):
            return cte_columns

        for cte in with_clause.expressions:
            if not isinstance(cte, exp.CTE):
                continue
            cte_name = cte.alias_or_name.lower()
            if not cte_name:
                continue
            inner = cte.this
            if not isinstance(inner, exp.Select):
                raise SqlFirewallViolation(
                    "NON_SELECT_CTE",
                    f"CTE '{cte_name}' must contain a valid SELECT query.",
                )
            projected: set[str] = set()
            for expr in inner.expressions:
                alias_or_name = expr.alias_or_name.lower() if hasattr(expr, "alias_or_name") else ""
                if alias_or_name:
                    if self._compiled.security.is_sensitive_identifier(alias_or_name):
                        raise SqlFirewallViolation(
                            "SENSITIVE_COLUMN_BLOCKED",
                            f"CTE '{cte_name}' projects blocked sensitive column '{alias_or_name}'.",
                        )
                    projected.add(alias_or_name)
            cte_columns[cte_name] = projected
        return cte_columns

    def _validate_scope_tree(
        self,
        stmt: exp.Select,
        cte_columns: dict[str, set[str]],
    ) -> tuple[set[str], set[str]]:
        referenced_tables: set[str] = set()
        referenced_columns: set[str] = set()

        select_nodes = [node for node in stmt.walk() if isinstance(node, exp.Select)]
        for select_node in select_nodes:
            tables, cols = self._validate_single_select_scope(select_node, cte_columns)
            referenced_tables.update(tables)
            referenced_columns.update(cols)

        return referenced_tables, referenced_columns

    def _validate_single_select_scope(
        self,
        select_node: exp.Select,
        cte_columns: dict[str, set[str]],
    ) -> tuple[set[str], set[str]]:
        alias_to_relation: dict[str, str] = {}
        active_relations: set[str] = set()
        referenced_tables: set[str] = set()
        referenced_columns: set[str] = set()

        for table_node in self._direct_select_tables(select_node):
            rel_name = self._validate_table_node(table_node, cte_columns)
            active_relations.add(rel_name)
            if rel_name in self._certified_tables:
                referenced_tables.add(rel_name)
            alias = table_node.alias_or_name.lower()
            if alias:
                alias_to_relation[alias] = rel_name
            alias_to_relation[rel_name] = rel_name

        select_aliases: set[str] = set()
        for expr in select_node.expressions:
            if isinstance(expr, exp.Alias) and expr.alias:
                select_aliases.add(expr.alias.lower())

        for col_node in self._direct_select_columns(select_node):
            col_name = col_node.name.lower()
            table_qualifier = col_node.table.lower() if col_node.table else ""

            if self._compiled.security.is_sensitive_identifier(col_name):
                raise SqlFirewallViolation(
                    "SENSITIVE_COLUMN_BLOCKED",
                    f"Access to blocked sensitive column '{col_name}' is prohibited.",
                )

            if table_qualifier:
                target_rel = alias_to_relation.get(table_qualifier)
                if target_rel is None:
                    raise SqlFirewallViolation(
                        "UNCERTIFIED_TABLE_QUALIFIER",
                        f"Column '{table_qualifier}.{col_name}' references unknown table/alias.",
                    )
                allowed_cols = (
                    cte_columns[target_rel]
                    if target_rel in cte_columns
                    else self._certified_tables.get(target_rel, set())
                )
                if col_name not in allowed_cols:
                    raise SqlFirewallViolation(
                        "UNCERTIFIED_COLUMN",
                        f"Column '{col_name}' is not certified on '{target_rel}'.",
                    )
                referenced_columns.add(f"{target_rel}.{col_name}")
            else:
                if col_name in select_aliases:
                    continue
                valid_in_scope = any(
                    col_name in (cte_columns.get(rel) or self._certified_tables.get(rel, set()))
                    for rel in active_relations
                )
                if not valid_in_scope:
                    raise SqlFirewallViolation(
                        "UNCERTIFIED_COLUMN",
                        f"Unqualified column '{col_name}' is not certified in active FROM scope.",
                    )
                referenced_columns.add(col_name)

        return referenced_tables, referenced_columns

    def _direct_select_tables(self, select_node: exp.Select) -> list[exp.Table]:
        tables: list[exp.Table] = []
        from_clause = select_node.args.get("from_")
        if from_clause is not None:
            for node in from_clause.walk(prune=lambda n: isinstance(n, exp.Select)):
                if isinstance(node, exp.Table):
                    tables.append(node)
        for join_node in select_node.args.get("joins") or []:
            for node in join_node.walk(prune=lambda n: isinstance(n, exp.Select)):
                if isinstance(node, exp.Table):
                    tables.append(node)
        return tables

    def _direct_select_columns(self, select_node: exp.Select) -> list[exp.Column]:
        cols: list[exp.Column] = []
        for node in select_node.walk(prune=lambda n: n is not select_node and isinstance(n, exp.Select)):
            if isinstance(node, exp.Column):
                cols.append(node)
        return cols

    def _validate_table_node(
        self,
        table_node: exp.Table,
        cte_columns: dict[str, set[str]],
    ) -> str:
        if not isinstance(table_node.this, exp.Identifier):
            raise SqlFirewallViolation(
                "UNSAFE_TABLE_EXPRESSION",
                f"Table source must be a certified identifier (got {type(table_node.this).__name__}).",
            )
        if table_node.catalog:
            raise SqlFirewallViolation(
                "EXTERNAL_CATALOG_BLOCKED",
                f"External catalog qualifier '{table_node.catalog}' is prohibited.",
            )
        if table_node.db and table_node.db.lower() != "main":
            raise SqlFirewallViolation(
                "EXTERNAL_SCHEMA_BLOCKED",
                f"External schema '{table_node.db}' is prohibited; only 'main' is allowed.",
            )

        table_name = table_node.name.lower()
        if table_name not in self._certified_tables and table_name not in cte_columns:
            raise SqlFirewallViolation(
                "UNCERTIFIED_TABLE",
                f"Table '{table_name}' is not registered in the certified semantic catalog.",
            )
        return table_name

    def _enforce_limit(self, stmt: exp.Select) -> int:
        default_limit = self._compiled.security.default_limit
        max_limit = self._compiled.security.max_limit
        limit_node = stmt.args.get("limit")
        if limit_node is None:
            stmt.limit(default_limit, copy=False)
            return default_limit

        expr = limit_node.expression
        if not isinstance(expr, exp.Literal) or expr.is_string:
            raise SqlFirewallViolation("INVALID_LIMIT", "LIMIT must be a positive integer literal.")
        try:
            requested = int(expr.this)
        except ValueError as exc:
            raise SqlFirewallViolation("INVALID_LIMIT", "LIMIT must be an integer literal.") from exc

        if requested <= 0:
            raise SqlFirewallViolation("INVALID_LIMIT", "LIMIT must be greater than zero.")
        if requested > max_limit:
            stmt.limit(max_limit, copy=False)
            return max_limit
        return requested
