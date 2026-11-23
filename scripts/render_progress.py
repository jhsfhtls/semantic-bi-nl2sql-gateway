"""Deterministic renderer and sync checker for JORNADA_IMPLEMENTACAO.md."""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
VALID_STATUSES = {"COMPLETED", "IN_PROGRESS", "PENDING"}


def _load_progress(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Progress state file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("state/project_progress.json root must be a JSON object.")
    return data


def _validate_progress(data: dict[str, Any]) -> None:
    required = (
        "schema_version",
        "last_updated",
        "project_name",
        "current_phase_id",
        "overall_status",
        "phases",
        "history",
    )
    for key in required:
        if key not in data:
            raise ValueError(f"Missing required key '{key}' in project_progress.json.")

    phases = data.get("phases")
    if not isinstance(phases, list) or not phases:
        raise ValueError("'phases' must be a non-empty list.")

    seen_ids: set[str] = set()
    for idx, phase in enumerate(phases):
        if not isinstance(phase, dict):
            raise ValueError(f"phases[{idx}] must be an object.")
        for field in ("id", "label", "name", "branch", "status", "gate"):
            val = phase.get(field)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"phases[{idx}] missing non-empty '{field}'.")
        if phase["id"] in seen_ids:
            raise ValueError(f"Duplicate phase id '{phase['id']}'.")
        seen_ids.add(phase["id"])
        if phase["status"] not in VALID_STATUSES:
            raise ValueError(f"Invalid phase status '{phase['status']}' in '{phase['id']}'.")
        if not isinstance(phase.get("deliverables"), list) or not phase["deliverables"]:
            raise ValueError(f"phases[{idx}] must have non-empty 'deliverables'.")

    if data["current_phase_id"] not in seen_ids:
        raise ValueError(f"current_phase_id '{data['current_phase_id']}' not in phases.")


def _status_label(status: str) -> str:
    return {
        "COMPLETED": "CONCLUIDO",
        "IN_PROGRESS": "EM ANDAMENTO",
        "PENDING": "PENDENTE",
    }.get(status, status)


def render_markdown(data: dict[str, Any]) -> str:
    """Render deterministic JORNADA_IMPLEMENTACAO.md in Brazilian Portuguese."""
    phases = data["phases"]
    history = data["history"]
    total = len(phases)
    done = sum(1 for p in phases if p["status"] == "COMPLETED")
    pct = (done / total) * 100.0
    current = next(p for p in phases if p["id"] == data["current_phase_id"])

    lines: list[str] = [
        '<div align="center">',
        "  <h2>Jornada de Implementação — Semantic BI & NL2SQL Gateway</h2>",
        "  <p><i>Painel Vivo de Governança Semântica, Codegen Power BI & Firewall AST NL2SQL</i></p>",
        f"  <p><b>Versão:</b> {data['schema_version']} &nbsp;|&nbsp; "
        f"<b>Última Atualização:</b> {data['last_updated']} &nbsp;|&nbsp; "
        "<b>Padrões:</b> ADR-001 • ADR-002 • ADR-003 • C.O.R.E.</p>",
        "  <hr/>",
        "</div>",
        "<br/>",
        "",
        "# Jornada de Implementação & Painel Vivo de Governança",
        "",
        "> **Single Source of Truth (SSOT):** Renderizado deterministicamente a partir de "
        "[`state/project_progress.json`](state/project_progress.json) via "
        "`python scripts/render_progress.py`. Não editar manualmente.",
        "",
        "## 1. Painel Executivo em Tempo Real",
        "",
        f"- **Última Fase Concluída / Ativa:** `{current['label']} — {current['name']}`",
        f"- **Status Geral:** `{data['overall_status']}`",
        f"- **Progresso Estrutural:** **{done} de {total} Fases Concluídas ({pct:.1f}%)**",
        "",
        "```mermaid",
        "flowchart LR",
    ]

    for idx, phase in enumerate(phases):
        node_id = f"F{idx:02d}"
        badge = _status_label(phase["status"])
        safe_label = phase["label"].replace('"', "'")
        lines.append(f'    {node_id}["{safe_label}: {badge}"]')
        if idx > 0:
            prev_id = f"F{idx - 1:02d}"
            lines.append(f"    {prev_id} --> {node_id}")

    lines.extend(
        [
            "```",
            "",
            "---",
            "",
            "## 2. Cronograma de Fases (1 PR por Fase)",
            "",
            "| Fase | Escopo | Branch | Status | Conclusão | Entregáveis & Gate |",
            "| :---: | :--- | :--- | :---: | :---: | :--- |",
        ]
    )

    for phase in phases:
        completed_at = f"`{phase['completed_at']}`" if phase.get("completed_at") else "—"
        deliverables_html = "<br/>".join(f"• {item}" for item in phase["deliverables"])
        gate_text = f"<br/><i>Gate: {phase['gate']}</i>"
        lines.append(
            f"| **{phase['label']}** | **{phase['name']}** | `{phase['branch']}` | "
            f"`{_status_label(phase['status'])}` | {completed_at} | {deliverables_html}{gate_text} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 3. Trilha Histórica de Marcos",
            "",
            "| Data | Fase | Marco | Resumo |",
            "| :---: | :---: | :--- | :--- |",
        ]
    )

    for entry in history:
        lines.append(
            f"| `{entry['date']}` | **{entry['phase']}** | {entry['milestone']} | {entry['summary']} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 4. Runbook Rápido de Verificação",
            "",
            "```bash",
            "ruff check .",
            "python scripts/validate_metrics_schema.py",
            "python scripts/render_catalog.py --check",
            "python scripts/render_progress.py --check",
            'pytest -m "not live" -v',
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Render or verify JORNADA_IMPLEMENTACAO.md determinism."""
    parser = argparse.ArgumentParser(description="Render or check JORNADA_IMPLEMENTACAO.md.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository or worktree root directory.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify JORNADA_IMPLEMENTACAO.md is synchronized without modifying files.",
    )
    args = parser.parse_args(argv)

    root = args.repo_root.resolve()
    data = _load_progress(root / "state" / "project_progress.json")
    _validate_progress(data)

    rendered = render_markdown(data)
    if "\r" in rendered:
        raise ValueError("Rendered JORNADA_IMPLEMENTACAO.md contains CR characters.")
    expected_bytes = rendered.encode("utf-8")
    output_path = root / "JORNADA_IMPLEMENTACAO.md"

    if args.check:
        if not output_path.is_file():
            print(f"[FAIL] Missing {output_path}", file=sys.stderr)
            return 1
        actual_bytes = output_path.read_bytes()
        if actual_bytes != expected_bytes:
            diff = difflib.unified_diff(
                actual_bytes.decode("utf-8", errors="replace").splitlines(),
                rendered.splitlines(),
                fromfile="JORNADA_IMPLEMENTACAO.md (actual)",
                tofile="JORNADA_IMPLEMENTACAO.md (expected)",
                lineterm="",
            )
            print("[FAIL] JORNADA_IMPLEMENTACAO.md is out of sync:", file=sys.stderr)
            for line in diff:
                print(line, file=sys.stderr)
            return 1
        print("[PASS] JORNADA_IMPLEMENTACAO.md is 100% synchronized with state/project_progress.json.")
        return 0

    output_path.write_bytes(expected_bytes)
    print(f"[OK] Rendered {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
