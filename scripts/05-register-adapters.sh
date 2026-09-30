#!/usr/bin/env bash
# The operator (mcp.admin) deploys both MCP servers THROUGH the gateway management API.
source "$(dirname "$0")/lib.sh"
T=$(token operator gateway)
for a in weather hr-directory; do
  step "Registering adapter '$a'"
  code=$(curl -s -o "$RUN_DIR/register-$a.json" -w '%{http_code}' -X POST "$GATEWAY_LOCAL/adapters" \
    -H "Authorization: Bearer $T" -H 'Content-Type: application/json' --data @"$REPO_ROOT/demo/adapter-$a.json")
  case "$code" in
    200|201) ok "created" ;;
    409) warn "already exists; updating"
         curl -sf -X PUT "$GATEWAY_LOCAL/adapters/$a" -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
           --data @"$REPO_ROOT/demo/adapter-$a.json" >/dev/null ;;
    *) cat "$RUN_DIR/register-$a.json"; die "HTTP $code" ;;
  esac
done
for a in weather hr-directory; do
  kubectl -n "$NS" rollout status "statefulset/$a" --timeout=180s
done
kubectl -n "$NS" get statefulsets,pods -o wide
