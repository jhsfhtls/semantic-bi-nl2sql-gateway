"""Read-only DuckDB query executor enforcing AST firewall, locked settings, and timeouts."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from semantic_bi_gateway.nl2sql.firewall import (
    AstSqlFirewall,
    FirewallDecision,
    SqlFirewallViolation,
)
from semantic_bi_gateway.semantic.models import CompiledSemanticModel


def _normalize_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(round(value, 6))
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, (int, bool, str)):
        return value
    return str(value)


def compute_result_hash(columns: tuple[str, ...], rows: tuple[dict[str, Any], ...]) -> str:
    """Compute deterministic SHA-256 hash over normalized query result-set."""
    payload = {"columns": list(columns), "rows": list(rows)}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class QueryExecutionResult:
    """Immutable query execution response with audit hash."""

    sql: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    row_count: int
    execution_ms: float
    result_hash_sha256: str
    firewall_decision: FirewallDecision


class ReadOnlyDuckDbExecutor:
    """Executes AST-validated queries against a locked, read-only DuckDB connection."""

    def __init__(
        self,
        compiled: CompiledSemanticModel,
        firewall: AstSqlFirewall | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._compiled = compiled
        self._firewall = firewall or AstSqlFirewall(compiled)
        self._db_path = db_path.resolve() if db_path is not None else None

    @property
    def db_path(self) -> Path | None:
        """Return configured DuckDB path or None for in-memory mode."""
        return self._db_path

    def execute(self, raw_sql: str, db_path: Path | None = None) -> QueryExecutionResult:
        """Validate SQL with AstSqlFirewall and execute on locked read-only DuckDB."""
        decision = self._firewall.validate(raw_sql)
        target_db = db_path.resolve() if db_path is not None else self._db_path
        if target_db is not None and not target_db.is_file():
            raise FileNotFoundError(f"DuckDB database file not found: {target_db}")

        conn = (
            duckdb.connect(str(target_db), read_only=True)
            if target_db is not None
            else duckdb.connect(":memory:")
        )
        timeout_seconds = float(self._compiled.security.query_timeout_seconds)
        timed_out = threading.Event()

        def _interrupt_query() -> None:
            timed_out.set()
            try:
                conn.interrupt()
            except Exception:
                pass

        timer = threading.Timer(timeout_seconds, _interrupt_query)
        start = time.perf_counter()
        try:
            conn.execute("SET enable_external_access = false;")
            conn.execute("SET lock_configuration = true;")

            timer.start()
            cursor = conn.execute(decision.sanitized_sql)
            description = cursor.description or []
            columns = tuple(col[0] for col in description)
            raw_rows = cursor.fetchall()
            timer.cancel()
        except duckdb.InterruptException as exc:
            raise SqlFirewallViolation(
                "QUERY_TIMEOUT",
                f"Query exceeded timeout ceiling ({timeout_seconds}s).",
            ) from exc
        except duckdb.Error as exc:
            if timed_out.is_set():
                raise SqlFirewallViolation(
                    "QUERY_TIMEOUT",
                    f"Query exceeded timeout ceiling ({timeout_seconds}s).",
                ) from exc
            raise
        finally:
            timer.cancel()
            conn.close()

        elapsed_ms = round((time.perf_counter() - start) * 1000.0, 3)
        normalized_rows = tuple(
            {col: _normalize_cell(val) for col, val in zip(columns, row, strict=True)}
            for row in raw_rows
        )
        result_hash = compute_result_hash(columns, normalized_rows)

        return QueryExecutionResult(
            sql=decision.sanitized_sql,
            columns=columns,
            rows=normalized_rows,
            row_count=len(normalized_rows),
            execution_ms=elapsed_ms,
            result_hash_sha256=result_hash,
            firewall_decision=decision,
        )
