#!/usr/bin/env bash
# Deploy MongoDB and the onboarding app (API + web build in one container) to namespace onboarding; expose
# /onboarding/ on the shared edge.
source "$(dirname "$0")/lib.sh"
K="$ONB_ROOT/deploy/k8s"
need AWS_REGION
step "MongoDB (1-member replica set)"
# A Job's pod template is immutable: drop the finished init job so a changed image applies (it is idempotent).
kubectl -n "$ONB_NS" delete job mongodb-rs-init --ignore-not-found >/dev/null 2>&1 || true
kubectl apply -f "$K/mongodb.yaml" >/dev/null
kubectl -n "$ONB_NS" rollout status statefulset/mongodb --timeout=300s
kubectl -n "$ONB_NS" wait --for=condition=complete job/mongodb-rs-init --timeout=300s
ok "mongodb ready"
step "Onboarding app (API + web)"
ARN="${ONBOARDING_AGENT_RUNTIME_ARN:-}"
[[ -n "$ARN" ]] || warn "ONBOARDING_AGENT_RUNTIME_ARN is not set (run make onboarding-agent): the agent can't be reached yet"
sed -e "s|__AGENT_RUNTIME_ARN__|$ARN|" -e "s|AWS_REGION: ap-southeast-1|AWS_REGION: $AWS_REGION|" "$K/api.yaml" | kubectl apply -f - >/dev/null
kubectl apply -f "$K/netpol.yaml" >/dev/null
# Before the single container: a separate nginx web deployment and the policy that let it reach the API.
kubectl -n "$ONB_NS" delete deploy/onboarding-web svc/onboarding-web configmap/onboarding-web-nginx \
  networkpolicy/api-from-web-only --ignore-not-found >/dev/null
kubectl -n "$ONB_NS" rollout restart deploy/onboarding-api >/dev/null
kubectl -n "$ONB_NS" rollout status deploy/onboarding-api --timeout=300s
ok "app ready"
step "Port-forward for the edge (localhost:8091 -> onboarding-api)"
pf() { local f="$RUN_DIR/onboarding-pf.pid"
  [[ -f "$f" ]] && kill "$(cat "$f")" 2>/dev/null || true
  nohup bash -c "while true; do kubectl -n $ONB_NS port-forward svc/onboarding-api 8091:8080; sleep 1; done" \
    >"$RUN_DIR/onboarding-pf.log" 2>&1 & echo $! >"$f"; }
pf
for _ in $(seq 20); do curl -fsS -o /dev/null http://127.0.0.1:8091/onboarding/ && break; sleep 1; done
ok "http://127.0.0.1:8091/onboarding/"
if docker ps --format '{{.Names}}' | grep -qx mcpdemo-edge; then
  docker restart mcpdemo-edge >/dev/null && ok "edge reloaded: https://${NGROK_DOMAIN:-<ngrok>}/onboarding/"
  warn "the ngrok traffic policy must route /onboarding to the edge too: make tunnel"
fi
