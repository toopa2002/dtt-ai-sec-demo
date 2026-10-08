#!/usr/bin/env python3
"""Print the MCP tools each demo server exposes, read from servers/<name>-mcp/src/main.py (no imports, just ast).

    python3 scripts/agent_capabilities.py weather hr-directory
    -> {"weather": [{"name": "get_current_weather", "description": "...", "params": {"city": "string"}}, ...], ...}

Used by scripts/agentcore-gateway.sh to declare, in AWS, which tools the agent uses (OpenAPI schema of the inbound
gateway target + runtime tags), so governance tools can see them without calling the MCP servers.
"""

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TYPES = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}


def tools(server: str) -> list[dict]:
    tree = ast.parse((ROOT / "servers" / f"{server}-mcp" / "src" / "main.py").read_text())
    found = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        # @app.tool() / @mcp.tool / @server.tool(...)
        if not any("tool" in ast.unparse(d) for d in node.decorator_list):
            continue
        params = {a.arg: TYPES.get(ast.unparse(a.annotation), "string") if a.annotation else "string"
                  for a in node.args.args}
        found.append({"name": node.name, "description": (ast.get_docstring(node) or "").strip(), "params": params})
    return found


if __name__ == "__main__":
    print(json.dumps({server: tools(server) for server in sys.argv[1:]}))
