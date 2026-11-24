"""FastAPI REST gateway exposing SemanticBiGateway under /api/v1/* with AST firewall."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from semantic_bi_gateway._version import __version__
from semantic_bi_gateway.nl2sql import LlmProviderUnavailableError, SqlFirewallViolation
from semantic_bi_gateway.sdk import SemanticBiGateway
from semantic_bi_gateway.semantic import SemanticCompilationError


class AskRequest(BaseModel):
    """Request payload for /api/v1/ask."""

    question: str = Field(..., min_length=1, max_length=2000)
    lakehouse: str | None = None
    mode: Literal["deterministic", "llm"] = "deterministic"
    dry_run: bool = False


class ValidateSqlRequest(BaseModel):
    """Request payload for /api/v1/validate-sql."""

    sql: str = Field(..., min_length=1, max_length=8192)


def create_app(
    repo_root: Path | None = None,
    gateway: SemanticBiGateway | None = None,
) -> FastAPI:
    """Create configured FastAPI app sharing SemanticBiGateway."""
    gw = gateway or SemanticBiGateway(repo_root=repo_root)
    app = FastAPI(
        title="semantic-bi-nl2sql-gateway",
        version=__version__,
        description="dbt semantic-layer compiler, Power BI codegen, and AST-guarded NL2SQL API.",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "semantic-bi-nl2sql-gateway", "version": __version__}

    @app.get("/api/v1/status")
    def status() -> dict[str, Any]:
        return gw.status()

    @app.post("/api/v1/compile-bi")
    def compile_bi() -> dict[str, Any]:
        # HTTP endpoint always generates in-memory without arbitrary server disk writes.
        return gw.compile_bi(output_dir=None)

    @app.post("/api/v1/ask")
    def ask(req: AskRequest) -> dict[str, Any]:
        try:
            return gw.ask(
                question=req.question,
                lakehouse=req.lakehouse,
                mode=req.mode,
                dry_run=req.dry_run,
            )
        except SqlFirewallViolation as exc:
            raise HTTPException(
                status_code=403,
                detail={"code": exc.code, "reason": exc.reason},
            ) from exc
        except SemanticCompilationError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": type(exc).__name__, "reason": str(exc)},
            ) from exc
        except LlmProviderUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": type(exc).__name__, "reason": str(exc)},
            ) from exc

    @app.post("/api/v1/validate-sql")
    def validate_sql(req: ValidateSqlRequest) -> dict[str, Any]:
        return gw.validate_sql(req.sql)

    @app.post("/api/v1/eval")
    def run_evals() -> dict[str, Any]:
        return gw.run_evals()

    @app.get("/api/v1/kpi-dict")
    def kpi_dict() -> dict[str, Any]:
        return gw.kpi_dictionary()

    return app
