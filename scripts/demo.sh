#!/usr/bin/env bash
# Scripted part of the demo: gateway state + the direct-call authorization matrix.
# Writes a transcript to out/demo-run.md.
source "$(dirname "$0")/lib.sh"
mkdir -p "$REPO_ROOT/out"; OUT="$REPO_ROOT/out/demo-run.md"
exec > >(tee "$OUT") 2>&1
H=(-H 'ngrok-skip-browser-warning: true' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')
INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"demo-curl","version":"1"}}}'

mcp_status() { # token-or-empty adapter -> HTTP status of an MCP initialize through the public gateway
  local auth=(); [[ -n "$1" ]] && auth=(-H "Authorization: Bearer $1")
  curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "${auth[@]}" -X POST "$GATEWAY_PUBLIC/adapters/$2/mcp" --data "$INIT"
}
row() { printf '| %-13s | %-12s | %-8s | %s |\n' "$1" "$2" "$3" "$4"; }

echo "# MCP Gateway demo run — $(date -Is)"
step "1. Gateway workloads (k3s, namespace $NS)"
kubectl -n "$NS" get pods -o wide
step "2. MCP servers exist only as gateway-managed adapters"
kubectl -n "$NS" get pods -l adapter/type=mcp -L adapter/name

step "3. Direct calls through the public gateway ($GATEWAY_PUBLIC)"
row caller adapter expected got; row --- --- --- ---
for a in weather hr-directory; do row "no token" "$a" 401 "$(mcp_status "" "$a")"; done
for p in operator full weather-only; do
  T=$(token "$p" gateway)
  for a in weather hr-directory; do
    exp=200; [[ "$p" == weather-only && "$a" == hr-directory ]] && exp=403
    row "$p" "$a" "$exp" "$(mcp_status "$T" "$a")"
  done
done
echo
step "4. Now open http://localhost:3000 and chat as each persona (see README)"
