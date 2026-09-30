#!/usr/bin/env python3
"""Get an access token for a demo persona (device-code sign-in, cached per persona).

  uv run --with msal scripts/get_token.py <persona> gateway|agent
Prints only the token, so it can be used as: T=$(scripts/get_token.py operator gateway)
"""

import sys
from pathlib import Path

import msal

ROOT = Path(__file__).resolve().parent.parent
env = dict(l.split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))

persona, api = sys.argv[1], sys.argv[2]
resource = {"gateway": env["GATEWAY_API_CLIENT_ID"], "agent": env["AGENT_API_CLIENT_ID"]}[api]
upn = env.get("DEMO_USER_" + persona.upper().replace("-", "_"))

cache_file = ROOT / ".run" / f"token-cache-{persona}.json"
cache_file.parent.mkdir(exist_ok=True)
cache = msal.SerializableTokenCache()
if cache_file.exists():
    cache.deserialize(cache_file.read_text())

app = msal.PublicClientApplication(
    env["CLI_CLIENT_ID"], authority=f"https://login.microsoftonline.com/{env['TENANT_ID']}", token_cache=cache
)
scopes = [f"api://{resource}/access_as_user"]
accounts = [a for a in app.get_accounts() if not upn or a["username"].lower() == upn.lower()]
result = app.acquire_token_silent(scopes, account=accounts[0]) if accounts else None
if not result:
    flow = app.initiate_device_flow(scopes=scopes)
    print(f"[{persona}] sign in as {upn or 'the persona'}: {flow['message']}", file=sys.stderr)
    result = app.acquire_token_by_device_flow(flow)
if "access_token" not in result:
    sys.exit(f"token error: {result.get('error_description', result)}")
if cache.has_state_changed:
    cache_file.write_text(cache.serialize())
    cache_file.chmod(0o600)
print(result["access_token"])
