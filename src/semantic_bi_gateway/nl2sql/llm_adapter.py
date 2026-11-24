"""Optional LLM NL2SQL adapter and unified Nl2SqlGateway enforcing AstSqlFirewall."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import httpx

from semantic_bi_gateway.nl2sql.deterministic import (
    DeterministicNl2SqlEngine,
    Nl2SqlTranslation,
)
from semantic_bi_gateway.nl2sql.executor import QueryExecutionResult, ReadOnlyDuckDbExecutor
from semantic_bi_gateway.nl2sql.firewall import AstSqlFirewall
from semantic_bi_gateway.semantic.models import CompiledSemanticModel

_SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


class LlmProviderUnavailableError(RuntimeError):
    """Raised when optional LLM mode is requested without configured provider credentials."""


class LlmSqlProposer(Protocol):
    """Protocol for pluggable LLM SQL proposers (always treated as untrusted by firewall)."""

    def propose_sql(self, question: str, compiled: CompiledSemanticModel) -> str:
        """Return candidate DuckDB SQL string to be validated by AstSqlFirewall."""


def _extract_sql_block(text: str) -> str:
    match = _SQL_FENCE_RE.search(text)
    return match.group(1).strip() if match else text.strip()


def _build_schema_prompt(question: str, compiled: CompiledSemanticModel) -> str:
    models_desc: list[str] = []
    for model_name in sorted(compiled.models):
        model = compiled.models[model_name]
        cols = ", ".join(f"{col.name}:{col.data_type}" for col in model.columns)
        models_desc.append(f"- {model.table} ({model.role}): {cols}")

    metrics_desc: list[str] = []
    for metric_name in sorted(compiled.metrics):
        metric = compiled.metrics[metric_name]
        formula = metric.expression or f"({metric.numerator}) / NULLIF({metric.denominator}, 0)"
        metrics_desc.append(f"- {metric.name} [{metric.model}]: {formula}")

    return (
        "Generate ONE DuckDB SELECT query using ONLY these certified tables, columns, and metrics.\n"
        "Never use wildcards, DDL/DML, file functions, or sensitive columns.\n\n"
        "Models:\n"
        + "\n".join(models_desc)
        + "\n\nMetrics:\n"
        + "\n".join(metrics_desc)
        + f"\n\nQuestion: {question}\nReturn ONLY SQL."
    )


class OptionalLlmAdapter:
    """Isolated optional LLM SQL proposer; reads credentials via environment variables only."""

    def __init__(
        self,
        provider: Literal["auto", "gemini", "openai", "anthropic", "ollama"] = "auto",
        timeout_seconds: float = 15.0,
    ) -> None:
        self._provider = provider
        self._timeout_seconds = timeout_seconds

    def _resolve_provider(self) -> tuple[str, str]:
        if self._provider == "gemini":
            key = os.environ.get("GEMINI_API_KEY", "").strip()
            if not key:
                raise LlmProviderUnavailableError("GEMINI_API_KEY is not configured.")
            return "gemini", key
        if self._provider == "openai":
            key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not key:
                raise LlmProviderUnavailableError("OPENAI_API_KEY is not configured.")
            return "openai", key
        if self._provider == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not key:
                raise LlmProviderUnavailableError("ANTHROPIC_API_KEY is not configured.")
            return "anthropic", key
        if self._provider == "ollama":
            host = os.environ.get("OLLAMA_HOST", "").strip()
            if not host:
                raise LlmProviderUnavailableError("OLLAMA_HOST is not configured.")
            return "ollama", host

        for env_name, prov in (
            ("GEMINI_API_KEY", "gemini"),
            ("OPENAI_API_KEY", "openai"),
            ("ANTHROPIC_API_KEY", "anthropic"),
            ("OLLAMA_HOST", "ollama"),
        ):
            val = os.environ.get(env_name, "").strip()
            if val:
                return prov, val

        raise LlmProviderUnavailableError(
            "No optional LLM provider configured in environment; use default deterministic mode."
        )

    def propose_sql(self, question: str, compiled: CompiledSemanticModel) -> str:
        """Request candidate SQL from configured provider; never logs or returns secrets."""
        provider, credential_or_host = self._resolve_provider()
        prompt = _build_schema_prompt(question, compiled)

        if provider == "gemini":
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
            resp = httpx.post(
                url,
                headers={"x-goog-api-key": credential_or_host},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=self._timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["candidates"][0]["content"]["parts"][0]["text"]
            return _extract_sql_block(raw)

        if provider == "openai":
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {credential_or_host}"},
                json={
                    "model": "gpt-4o-mini",
                    "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=self._timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
            return _extract_sql_block(data["choices"][0]["message"]["content"])

        if provider == "anthropic":
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": credential_or_host,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": "claude-3-5-haiku-latest",
                    "max_tokens": 512,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=self._timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
            return _extract_sql_block(data["content"][0]["text"])

        base = credential_or_host.rstrip("/")
        resp = httpx.post(
            f"{base}/api/generate",
            json={"model": "qwen2.5-coder:7b", "prompt": prompt, "stream": False},
            timeout=self._timeout_seconds,
        )
        resp.raise_for_status()
        return _extract_sql_block(resp.json()["response"])


@dataclass(frozen=True)
class GovernedAskResponse:
    """Combined translation + read-only execution payload returned by Nl2SqlGateway."""

    translation: Nl2SqlTranslation
    execution: QueryExecutionResult


class Nl2SqlGateway:
    """Unified NL2SQL gateway defaulting to offline deterministic mode with AST firewall."""

    def __init__(
        self,
        compiled: CompiledSemanticModel,
        db_path: Path | None = None,
        mode: Literal["deterministic", "llm"] = "deterministic",
        llm_adapter: LlmSqlProposer | None = None,
    ) -> None:
        self._compiled = compiled
        self._mode = mode
        self._firewall = AstSqlFirewall(compiled)
        self._deterministic = DeterministicNl2SqlEngine(compiled, self._firewall)
        self._executor = ReadOnlyDuckDbExecutor(compiled, self._firewall, db_path=db_path)
        self._llm_adapter = llm_adapter or OptionalLlmAdapter()

    @property
    def mode(self) -> str:
        """Return active translation mode ('deterministic' or 'llm')."""
        return self._mode

    def translate(self, question: str, mode: Literal["deterministic", "llm"] | None = None) -> Nl2SqlTranslation:
        """Translate question to firewall-verified DuckDB SQL using requested or default mode."""
        active_mode = mode or self._mode
        if active_mode == "deterministic":
            return self._deterministic.translate(question)
        if active_mode != "llm":
            raise ValueError(f"Unsupported NL2SQL mode '{active_mode}'.")

        candidate_sql = self._llm_adapter.propose_sql(question, self._compiled)
        decision = self._firewall.validate(candidate_sql)
        return Nl2SqlTranslation(
            question=question,
            mode="llm",
            sql=decision.sanitized_sql,
            strategy="llm_ast_verified",
            metrics=(),
            dimensions=(),
            filters=(),
            firewall_decision=decision,
        )

    def ask(
        self,
        question: str,
        mode: Literal["deterministic", "llm"] | None = None,
        db_path: Path | None = None,
    ) -> GovernedAskResponse:
        """Translate question, enforce AstSqlFirewall, and execute read-only on DuckDB."""
        translation = self.translate(question, mode=mode)
        execution = self._executor.execute(translation.sql, db_path=db_path)
        return GovernedAskResponse(translation=translation, execution=execution)
