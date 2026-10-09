#!/usr/bin/env bash
# Delete the bookstack namespace (MariaDB PVC and secrets included) and stop the port-forward.
source "$(cd "$(dirname "$0")/../.." && pwd)/scripts/lib.sh"
f="$RUN_DIR/bookstack-pf.pid"; [[ -f "$f" ]] && { kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; }
pkill -f "port-forward svc/bookstack 8093" 2>/dev/null || true
kubectl delete namespace bookstack --ignore-not-found --wait=true >/dev/null
ok "namespace bookstack deleted"
