import json
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

# FAKE DEMO DATA: every record in employees.json is synthetic.
app = FastMCP("HR Directory MCP Server (FAKE DEMO DATA)")

EMPLOYEES = json.loads((Path(__file__).parent / "employees.json").read_text())["employees"]


def _audit(tool: str, arg: str) -> None:
    # The MCP Gateway strips the caller's bearer token and injects the
    # authenticated identity as X-Mcp-UserId / X-Mcp-Roles.
    headers = get_http_headers(include_all=True)
    user = headers.get("x-mcp-userid", "unknown")
    roles = headers.get("x-mcp-roles", "")
    print(f"[hr-audit] user={user} roles={roles} tool={tool} arg={arg!r}", flush=True)


def _find(name: str) -> dict | None:
    needle = name.strip().lower()
    for e in EMPLOYEES:
        if e["name"].lower() == needle:
            return e
    matches = [e for e in EMPLOYEES if needle in e["name"].lower()]
    return matches[0] if len(matches) == 1 else None


@app.tool()
def lookup_employee(name: str) -> dict:
    """Look up an employee's profile (contact details, department, title, manager, salary band, home city)."""
    _audit("lookup_employee", name)
    employee = _find(name)
    if employee is None:
        return {"error": f"No unique employee matches '{name}'"}
    return employee


@app.tool()
def get_manager(name: str) -> dict:
    """Get the manager of an employee."""
    _audit("get_manager", name)
    employee = _find(name)
    if employee is None:
        return {"error": f"No unique employee matches '{name}'"}
    manager = _find(employee["manager"]) if employee["manager"] else None
    if manager is None:
        return {"employee": employee["name"], "manager": None}
    return {"employee": employee["name"], "manager": manager["name"], "manager_title": manager["title"], "manager_email": manager["email"]}


@app.tool()
def list_team(manager_name: str) -> dict:
    """List the direct reports of a manager."""
    _audit("list_team", manager_name)
    manager = _find(manager_name)
    if manager is None:
        return {"error": f"No unique employee matches '{manager_name}'"}
    reports = [{"name": e["name"], "title": e["title"]} for e in EMPLOYEES if e["manager"] == manager["name"]]
    return {"manager": manager["name"], "direct_reports": reports}
