"""Offline foundation tests for packaging, catalog validation, renderers, CLI, and MCP."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from semantic_bi_gateway import __version__
from semantic_bi_gateway.cli.main import main as cli_main
from semantic_bi_gateway.mcp.server import handle_request

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_package_version() -> None:
    assert __version__ == "0.1.0"


def test_metrics_catalog_validation() -> None:
    scripts_dir = REPO_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import validate_metrics_schema

    ok, errors, stats = validate_metrics_schema.validate_catalog(REPO_ROOT)
    assert ok, f"Catalog validation failed: {errors}"
    assert stats == {
        "models": 6,
        "joins": 8,
        "metrics": 8,
        "blocked_sensitive_columns": 10,
    }


def test_renderers_check_mode_pass() -> None:
    for script_name in ("render_catalog.py", "render_progress.py"):
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / script_name),
                "--check",
                "--repo-root",
                str(REPO_ROOT),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"{script_name} --check failed:\n{proc.stdout}\n{proc.stderr}"


def test_cli_status_json(capsys: object) -> None:
    exit_code = cli_main(["status", "--repo-root", str(REPO_ROOT), "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["service"] == "semantic-bi-nl2sql-gateway"
    assert payload["version"] == "0.1.0"
    assert payload["default_mode"] == "deterministic"
    assert payload["semantic_models_count"] == 6
    assert payload["allowed_joins_count"] == 8
    assert payload["certified_metrics_count"] == 8
    assert payload["blocked_sensitive_columns_count"] == 10


def test_mcp_initialize_handshake() -> None:
    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response["id"] == 1
    assert response["result"]["protocolVersion"] == "2024-11-05"
    assert response["result"]["serverInfo"]["name"] == "semantic-bi-nl2sql-gateway-mcp"


def test_gitignore_has_satellite_shield() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "# --- AI SATELLITE SHIELD (DO NOT REMOVE) ---" in gitignore
