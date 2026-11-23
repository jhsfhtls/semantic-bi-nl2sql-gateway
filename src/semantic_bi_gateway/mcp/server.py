"""JSON-RPC 2.0 stdio MCP server entrypoint (`sbg-mcp`, expanded in Phase 07)."""

from __future__ import annotations

import json
import sys
from typing import Any

from semantic_bi_gateway import __version__

MCP_PROTOCOL_VERSION = "2024-11-05"


def handle_request(request: dict[str, Any]) -> dict[str, Any]:
    """Handle minimal JSON-RPC 2.0 MCP handshake requests."""
    req_id = request.get("id")
    method = request.get("method")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "semantic-bi-nl2sql-gateway-mcp",
                    "version": __version__,
                },
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": []}}
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> int:
    """Run stdio JSON-RPC 2.0 loop until EOF."""
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        else:
            resp = handle_request(payload)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
