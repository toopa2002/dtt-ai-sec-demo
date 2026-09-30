#!/usr/bin/env bash
# Prove MCP servers are reachable ONLY through the gateway.
source "$(dirname "$0")/lib.sh"
fail=0
probe() { # namespace url [labels] -> prints HTTP code, or 000 when the connection is blocked
  # Short sleep: k3s' policy controller needs a moment to learn a new pod's IP.
  kubectl run "probe-$RANDOM" -n "$1" ${3:+--labels=$3} --rm -i --restart=Never --quiet --image=curlimages/curl:8.10.1 --command -- \
    sh -c "sleep 6; curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' '$2' --data '{}'" 2>/dev/null || true
}
step "Positive control: a pod with the gateway's label can reach the MCP servers"
r=$(probe "$NS" "http://weather-0.weather-service.$NS.svc.cluster.local:8000/mcp" app=mcpgateway)
if [[ "$r" =~ ^[1-5][0-9][0-9]$ ]]; then ok "gateway-labelled pod -> weather: HTTP $r (reachable)"; else warn "control failed ($r); results below are not meaningful"; fail=1; fi
step "Direct pod/service access must be blocked"
for ns in default "$NS"; do
  for target in weather-0.weather-service hr-directory-0.hr-directory-service; do
    r=$(probe "$ns" "http://$target.$NS.svc.cluster.local:8000/mcp")
    if [[ -z "$r" || "$r" == 000* ]]; then ok "from ns/$ns -> $target: blocked"; else warn "from ns/$ns -> $target: HTTP $r (NOT blocked)"; fail=1; fi
  done
done
step "No NodePort / LoadBalancer services"
if kubectl get svc -A -o jsonpath='{range .items[*]}{.spec.type}{"\n"}{end}' | grep -Eq 'NodePort|LoadBalancer'; then warn "found exposed service"; fail=1; else ok "none"; fi
step "Every MCP server is a gateway-registered adapter"
T=$(token operator gateway)
registered=$(curl -s -H "Authorization: Bearer $T" "$GATEWAY_LOCAL/adapters" | python3 -c 'import json,sys; print(" ".join(sorted(a["name"] for a in json.load(sys.stdin))))')
running=$(kubectl -n "$NS" get sts -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep -v '^toolgateway$' | sort | xargs)
[[ "$registered" == "$running" ]] && ok "registered=[$registered] running=[$running]" || { warn "registered=[$registered] running=[$running]"; fail=1; }
step "Clients only know the gateway URL"
if grep -rEn 'svc\.cluster\.local|weather-service|hr-directory-service' "$REPO_ROOT/agent" "$REPO_ROOT/chatbot/src" 2>/dev/null; then warn "adapter hostnames found in client code"; fail=1; else ok "no adapter hostnames in agent/ or chatbot/"; fi
exit $fail
