#!/usr/bin/env bash
# Deploy BookStack + MariaDB to namespace bookstack; expose /bookstack/ on the shared edge (sample app for the ISC
# Web Services connector). The UI only works at https://$NGROK_DOMAIN/bookstack/ (APP_URL); the API also on :8093.
source "$(cd "$(dirname "$0")/../.." && pwd)/scripts/lib.sh"
need NGROK_DOMAIN
APP_URL="https://$NGROK_DOMAIN/bookstack"
NS=bookstack
step "BookStack + MariaDB"
kubectl get ns "$NS" >/dev/null 2>&1 || kubectl create ns "$NS" >/dev/null
# Created once: re-runs keep the same APP_KEY and DB passwords (the MariaDB PVC was initialised with them).
if ! kubectl -n "$NS" get secret bookstack-secrets >/dev/null 2>&1; then
  kubectl -n "$NS" create secret generic bookstack-secrets \
    --from-literal=APP_KEY="base64:$(openssl rand -base64 32)" \
    --from-literal=DB_PASSWORD="$(openssl rand -hex 16)" \
    --from-literal=DB_ROOT_PASSWORD="$(openssl rand -hex 16)" >/dev/null
fi
sed "s|__APP_URL__|$APP_URL|" "$(dirname "$0")/k8s/bookstack.yaml" | kubectl apply -f - >/dev/null
kubectl -n "$NS" rollout status deploy/mariadb --timeout=300s
kubectl -n "$NS" rollout status deploy/bookstack --timeout=600s
ok "bookstack ready"
step "Port-forward for the edge (localhost:8093 -> bookstack)"
f="$RUN_DIR/bookstack-pf.pid"
[[ -f "$f" ]] && kill "$(cat "$f")" 2>/dev/null || true
nohup bash -c "while true; do kubectl -n $NS port-forward svc/bookstack 8093:80; sleep 1; done" \
  >"$RUN_DIR/bookstack-pf.log" 2>&1 & echo $! >"$f"
for _ in $(seq 20); do curl -fs -o /dev/null http://127.0.0.1:8093/login && break; sleep 1; done
ok "API: http://127.0.0.1:8093/api/  UI: $APP_URL/ (admin@admin.com / password on first login)"
if docker ps --format '{{.Names}}' | grep -qx mcpdemo-edge; then
  docker restart mcpdemo-edge >/dev/null && ok "edge reloaded"
  warn "the ngrok traffic policy must route /bookstack to the edge too: make tunnel"
fi
