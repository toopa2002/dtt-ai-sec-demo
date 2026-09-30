#!/usr/bin/env bash
# Deploy the gateway to k3s in Entra mode, lock down adapters, start port-forward + ngrok.
source "$(dirname "$0")/lib.sh"
need TENANT_ID GATEWAY_API_CLIENT_ID NGROK_DOMAIN

step "Applying upstream manifest (namespace, RBAC, redis, gateway, tool router)"
kubectl apply -f "$REPO_ROOT/mcp-gateway/deployment/k8s/local-deployment.yml"

step "Replacing the upstream local-dev secrets with random values"
if ! kubectl -n "$NS" get secret redis-secret -o jsonpath='{.metadata.annotations.mcpdemo/rotated}' | grep -q true; then
  pw=$(openssl rand -hex 24); gs=$(openssl rand -hex 32)
  kubectl -n "$NS" create secret generic redis-secret \
    --from-literal=password="$pw" \
    --from-literal=connectionString="redis-service:6379,password=$pw" \
    --from-literal=gatewaySecret="$gs" --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n "$NS" annotate secret redis-secret mcpdemo/rotated=true --overwrite
fi

step "Switching the gateway to Entra auth"
python3 -c 'import os,sys; sys.stdout.write(os.path.expandvars(open(sys.argv[1]).read()))' \
  "$REPO_ROOT/deployment/entra-patch.yml.tmpl" > "$REPO_ROOT/deployment/entra-patch.rendered.yml"
kubectl -n "$NS" patch deployment mcpgateway --patch-file "$REPO_ROOT/deployment/entra-patch.rendered.yml"

step "Redis persistence + gateway waits for Redis (survive node/WSL restarts)"
kubectl apply -f "$REPO_ROOT/deployment/redis-persistence.yml"
kubectl -n "$NS" patch deployment redis --patch-file "$REPO_ROOT/deployment/redis-persistence-patch.yml"
kubectl -n "$NS" patch deployment mcpgateway --patch-file "$REPO_ROOT/deployment/gateway-wait-for-redis-patch.yml"

step "Network policies: adapters reachable only from the gateway; egress limits"
kubectl apply -f "$REPO_ROOT/deployment/adapters-netpol.yml" -f "$REPO_ROOT/deployment/adapters-egress-netpol.yml"

step "Restarting workloads to pick up secrets/config"
# Redis first: the gateway falls back to in-memory storage if Redis isn't reachable at startup.
kubectl -n "$NS" rollout restart deployment/redis
kubectl -n "$NS" rollout status deployment/redis --timeout=180s
kubectl -n "$NS" wait --for=condition=Ready pod -l app=redis --timeout=120s
kubectl -n "$NS" rollout restart deployment/mcpgateway statefulset/toolgateway
kubectl -n "$NS" rollout status deployment/mcpgateway --timeout=300s
kubectl -n "$NS" rollout status statefulset/toolgateway --timeout=300s

"$REPO_ROOT/scripts/tunnel.sh" start
