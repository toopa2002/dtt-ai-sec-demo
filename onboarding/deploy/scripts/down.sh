#!/usr/bin/env bash
# Delete the onboarding namespace (MongoDB PVC included) and stop the port-forward.
source "$(dirname "$0")/lib.sh"
f="$RUN_DIR/onboarding-pf.pid"; [[ -f "$f" ]] && { kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; }
pkill -f "port-forward svc/onboarding-(api|web) 8091" 2>/dev/null || true
kubectl delete namespace "$ONB_NS" --ignore-not-found --wait=true >/dev/null
ok "namespace $ONB_NS deleted"
