#!/usr/bin/env python3
"""Entra ID (Free tier) setup for the MCP Gateway demo.

Uses the Azure CLI login (`az login --tenant ...`) to call Microsoft Graph.
Idempotent: re-running updates the existing apps instead of duplicating them.

  scripts/entra.py setup [--create-users]   create/update apps, consent, role assignments
  scripts/entra.py grant  <upn> <app>:<role> assign one role (app = chatbot|agent|gateway)
  scripts/entra.py revoke <upn> <app>:<role> remove one role
  scripts/entra.py show                     print every assignment
  scripts/entra.py teardown                 delete the demo apps (and their assignments)
"""

import json
import secrets
import string
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
ACCESS_FILE = ROOT / "entra" / "access.json"
PERSONA_FILE = ROOT / ".personas.local"  # gitignored; initial passwords for created users
GRAPH = "https://graph.microsoft.com/v1.0"
DEFAULT_ACCESS = "00000000-0000-0000-0000-000000000000"  # "default access" app role id

APPS = {
    "gateway": {
        "name": "mcpdemo-gateway-api",
        "roles": {
            "mcp.admin": "Manage MCP Gateway adapters (control plane).",
            "mcp.weather.user": "Use the weather MCP server through the gateway.",
            "mcp.hr.user": "Use the HR directory MCP server (PII) through the gateway.",
        },
    },
    "agent": {
        "name": "mcpdemo-agent-api",
        "roles": {"Agent.Invoke": "Invoke the AgentCore weather/HR agent."},
    },
    "chatbot": {"name": "mcpdemo-chatbot", "roles": {}},
    # Public client for the demo scripts (device-code sign-in as a persona).
    "cli": {"name": "mcpdemo-cli", "roles": {}},
}


def az(method: str, url: str, body: dict | None = None) -> dict:
    cmd = ["az", "rest", "--method", method, "--url", url if url.startswith("http") else GRAPH + url]
    if body is not None:
        cmd += ["--headers", "Content-Type=application/json", "--body", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"az rest {method} {url} failed:\n{out.stderr.strip()}")
    return json.loads(out.stdout) if out.stdout.strip() else {}


def read_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def write_env(updates: dict) -> None:
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            lines[i] = f"{key}={updates[key]}"
            seen.add(key)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    ENV_FILE.write_text("\n".join(lines) + "\n")


def find_app(name: str) -> dict | None:
    found = az("GET", f"/applications?$filter=displayName eq '{name}'")["value"]
    return found[0] if found else None


def ensure_app(key: str) -> tuple[dict, dict]:
    spec = APPS[key]
    app = find_app(spec["name"]) or az("POST", "/applications", {"displayName": spec["name"], "signInAudience": "AzureADMyOrg"})
    print(f"  app {spec['name']}: {app['appId']}")
    sps = az("GET", f"/servicePrincipals?$filter=appId eq '{app['appId']}'")["value"]
    sp = sps[0] if sps else az("POST", "/servicePrincipals", {"appId": app["appId"]})
    return app, sp


def expose_api(key: str, app: dict) -> None:
    """Identifier URI, v2 tokens, access_as_user scope and app roles (ids kept stable across runs)."""
    existing_scopes = {s["value"]: s for s in (app.get("api") or {}).get("oauth2PermissionScopes", [])}
    scope = existing_scopes.get("access_as_user") or {
        "id": str(uuid.uuid4()),
        "value": "access_as_user",
        "type": "User",
        "isEnabled": True,
        "adminConsentDisplayName": f"Access {APPS[key]['name']} as the signed-in user",
        "adminConsentDescription": f"Allows the app to call {APPS[key]['name']} on behalf of the signed-in user.",
    }
    existing_roles = {r["value"]: r for r in app.get("appRoles", [])}
    roles = [
        existing_roles.get(value) or {
            "id": str(uuid.uuid4()),
            "value": value,
            "displayName": value,
            "description": desc,
            "allowedMemberTypes": ["User"],
            "isEnabled": True,
        }
        for value, desc in APPS[key]["roles"].items()
    ]
    az("PATCH", f"/applications/{app['id']}", {
        "identifierUris": [f"api://{app['appId']}"],
        "api": {"requestedAccessTokenVersion": 2, "oauth2PermissionScopes": [scope]},
        "appRoles": roles,
    })


def scope_id(app_id: str, value: str = "access_as_user") -> str:
    app = az("GET", f"/applications?$filter=appId eq '{app_id}'")["value"][0]
    return next(s["id"] for s in app["api"]["oauth2PermissionScopes"] if s["value"] == value)


def require_scopes(client: dict, *resources: dict) -> None:
    az("PATCH", f"/applications/{client['id']}", {
        "requiredResourceAccess": [
            {"resourceAppId": r["appId"], "resourceAccess": [{"id": scope_id(r["appId"]), "type": "Scope"}]}
            for r in resources
        ]
    })


def grant_consent(client_sp: dict, resource_sp: dict) -> None:
    """Tenant-wide admin consent for client -> resource/access_as_user."""
    grants = az("GET", f"/oauth2PermissionGrants?$filter=clientId eq '{client_sp['id']}' and resourceId eq '{resource_sp['id']}'")["value"]
    if grants:
        az("PATCH", f"/oauth2PermissionGrants/{grants[0]['id']}", {"scope": "access_as_user"})
    else:
        az("POST", "/oauth2PermissionGrants", {
            "clientId": client_sp["id"], "consentType": "AllPrincipals",
            "resourceId": resource_sp["id"], "scope": "access_as_user",
        })


def role_id(key: str, role: str) -> str:
    if role == "default":
        return DEFAULT_ACCESS
    app = find_app(APPS[key]["name"])
    return next(r["id"] for r in app["appRoles"] if r["value"] == role)


def sp_for(key: str) -> dict:
    app = find_app(APPS[key]["name"])
    return az("GET", f"/servicePrincipals?$filter=appId eq '{app['appId']}'")["value"][0]


def user_id(upn: str) -> str:
    return az("GET", f"/users/{upn}?$select=id")["id"]


def grant(upn: str, target: str) -> None:
    key, role = target.split(":", 1)
    sp, uid, rid = sp_for(key), user_id(upn), role_id(key, role)
    current = az("GET", f"/servicePrincipals/{sp['id']}/appRoleAssignedTo")["value"]
    if any(a["principalId"] == uid and a["appRoleId"] == rid for a in current):
        print(f"  = {upn} already has {target}")
        return
    az("POST", f"/servicePrincipals/{sp['id']}/appRoleAssignedTo", {"principalId": uid, "resourceId": sp["id"], "appRoleId": rid})
    print(f"  + {upn} -> {target}")


def revoke(upn: str, target: str) -> None:
    key, role = target.split(":", 1)
    sp, uid, rid = sp_for(key), user_id(upn), role_id(key, role)
    for a in az("GET", f"/servicePrincipals/{sp['id']}/appRoleAssignedTo")["value"]:
        if a["principalId"] == uid and a["appRoleId"] == rid:
            az("DELETE", f"/servicePrincipals/{sp['id']}/appRoleAssignedTo/{a['id']}")
            print(f"  - {upn} -> {target}")
            return
    print(f"  = {upn} did not have {target}")


def show() -> None:
    for key in ("chatbot", "agent", "gateway"):
        sp = sp_for(key)
        names = {r["id"]: r["value"] for r in find_app(APPS[key]["name"])["appRoles"]} | {DEFAULT_ACCESS: "default"}
        for a in az("GET", f"/servicePrincipals/{sp['id']}/appRoleAssignedTo")["value"]:
            print(f"  {a['principalDisplayName']:<28} {key}:{names.get(a['appRoleId'], a['appRoleId'])}")


def create_users(env: dict) -> None:
    domain = next(d["id"] for d in az("GET", "/domains")["value"] if d.get("isInitial"))
    alphabet = string.ascii_letters + string.digits
    created = []
    for persona in ["operator", "full", "weather-only", "agent-only", "none"]:
        upn = f"mcpdemo-{persona}@{domain}"
        try:
            user_id(upn)
            print(f"  = user {upn} exists")
        except RuntimeError:
            password = "Mcp!" + "".join(secrets.choice(alphabet) for _ in range(16))
            az("POST", "/users", {
                "accountEnabled": True, "displayName": f"MCP Demo ({persona})", "mailNickname": f"mcpdemo-{persona}",
                "userPrincipalName": upn,
                "passwordProfile": {"forceChangePasswordNextSignIn": False, "password": password},
            })
            created.append(f"{upn}\t{password}")
            print(f"  + user {upn}")
        env_key = "DEMO_USER_" + persona.upper().replace("-", "_")
        env[env_key] = upn
        write_env({env_key: upn})
    if created:
        with PERSONA_FILE.open("a") as f:
            f.write("\n".join(created) + "\n")
        PERSONA_FILE.chmod(0o600)
        print(f"  initial passwords written to {PERSONA_FILE.name} (gitignored, mode 600)")


def setup(create: bool) -> None:
    env = read_env()
    tenant = az("GET", "/organization")["value"][0]["id"]
    print(f"tenant {tenant}")

    print("apps:")
    gw_app, gw_sp = ensure_app("gateway")
    ag_app, ag_sp = ensure_app("agent")
    cb_app, cb_sp = ensure_app("chatbot")
    cli_app, cli_sp = ensure_app("cli")

    print("exposing APIs, roles, SPA redirect, permissions:")
    expose_api("gateway", gw_app)
    expose_api("agent", ag_app)
    az("PATCH", f"/applications/{cb_app['id']}", {"spa": {"redirectUris": ["http://localhost:3000/"]}})
    az("PATCH", f"/applications/{cli_app['id']}", {
        "isFallbackPublicClient": True,
        "publicClient": {"redirectUris": ["http://localhost"]},
    })
    require_scopes(ag_app, gw_app)          # agent -> gateway (OBO)
    require_scopes(cb_app, ag_app)          # chatbot -> agent
    require_scopes(cli_app, gw_app, ag_app)  # demo scripts -> both APIs
    for sp in (gw_sp, ag_sp, cb_sp):
        az("PATCH", f"/servicePrincipals/{sp['id']}", {"appRoleAssignmentRequired": True})
    grant_consent(ag_sp, gw_sp)
    grant_consent(cb_sp, ag_sp)
    grant_consent(cli_sp, gw_sp)
    grant_consent(cli_sp, ag_sp)

    write_env({
        "TENANT_ID": tenant,
        "GATEWAY_API_CLIENT_ID": gw_app["appId"],
        "AGENT_API_CLIENT_ID": ag_app["appId"],
        "CHAT_CLIENT_ID": cb_app["appId"],
        "CLI_CLIENT_ID": cli_app["appId"],
    })
    env = read_env()

    if create:
        print("users:")
        create_users(env)
        env = read_env()

    print("role assignments (entra/access.json):")
    access = json.loads(ACCESS_FILE.read_text())
    for persona, targets in access.items():
        upn = env.get("DEMO_USER_" + persona.upper().replace("-", "_"))
        if not upn:
            print(f"  ! skip {persona}: set DEMO_USER_{persona.upper().replace('-', '_')} in .env")
            continue
        for target in targets:
            grant(upn, target)
    print("done. IDs written to .env")


def teardown() -> None:
    for spec in APPS.values():
        app = find_app(spec["name"])
        if app:
            az("DELETE", f"/applications/{app['id']}")
            print(f"  deleted {spec['name']}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd = args[0]
    if cmd == "setup":
        setup("--create-users" in args)
    elif cmd == "grant" and len(args) == 3:
        grant(args[1], args[2])
    elif cmd == "revoke" and len(args) == 3:
        revoke(args[1], args[2])
    elif cmd == "show":
        show()
    elif cmd == "teardown":
        teardown()
    else:
        sys.exit(__doc__)
