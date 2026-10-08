#!/usr/bin/env bash
# Start/stop the gateway port-forward and the ngrok tunnel.
#
# One public domain ($NGROK_DOMAIN), shared with other apps; this demo only uses $PUBLIC_PATH (/mcp):
#   /mcp, /mcp/..., /.well-known/oauth-protected-resource/mcp/...  -> local edge (nginx in Docker, :8090), which serves
#        /mcp/gw/... -> MCP Gateway (port-forward :8000, prefix stripped) and /mcp/... -> chatbot dev server (:3000)
#        (deployment/edge-nginx.conf; ngrok's free plan routes by path but cannot rewrite paths, hence the edge)
#   /onboarding/... -> the same edge -> ISC Onboarding Agent web (onboarding/, port-forward :8091)
#   everything else -> NGROK_ROOT_UPSTREAM (another app's local port, e.g. 8080), or 404 if unset
# Works on Linux/WSL and macOS (no setsid needed).
source "$(dirname "$0")/lib.sh"
CHAT_PORT=${CHAT_PORT:-3000}
NGROK_CONF="$RUN_DIR/ngrok-mcpdemo.yml"

# Run a command detached in its own process group, so stop can kill the whole group.
spawn() {  # spawn <name> <logfile> <command...>
  local name=$1 log=$2; shift 2
  if command -v setsid >/dev/null; then setsid nohup "$@" >"$log" 2>&1 &
  else nohup perl -e 'use POSIX; POSIX::setsid(); exec @ARGV' "$@" >"$log" 2>&1 &
  fi
  echo $! >"$RUN_DIR/$name.pid"
}
stop_pid() { # name, expected command substring; only kill if the PID still belongs to that process
  local f="$RUN_DIR/$1.pid" pid
  if [[ -f "$f" ]]; then
    pid=$(cat "$f")
    if ps -p "$pid" -o args= 2>/dev/null | grep -qF -- "$2"; then kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true; fi
    rm -f "$f"
  fi
}
ngrok_default_config() { ngrok config check 2>/dev/null | sed -nE 's/.*file at (.*)$/\1/p'; }

case "${1:-start}" in
  start)
    need NGROK_DOMAIN
    stop_pid port-forward "port-forward svc/mcpgateway-service"; stop_pid ngrok "ngrok"
    # kubectl port-forward pins one pod and exits when that pod goes away; keep re-attaching.
    spawn port-forward "$RUN_DIR/port-forward.log" \
      bash -c "while true; do kubectl -n $NS port-forward svc/mcpgateway-service 8000:8000; sleep 1; done"
    EDGE_PORT=${EDGE_PORT:-8090}
    docker rm -f mcpdemo-edge >/dev/null 2>&1 || true
    docker run -d --name mcpdemo-edge --restart unless-stopped -p "127.0.0.1:$EDGE_PORT:8090" \
      -v "$REPO_ROOT/deployment/edge-nginx.conf:/etc/nginx/conf.d/default.conf:ro" nginx:alpine >/dev/null
    if [[ -n "${NGROK_ROOT_UPSTREAM:-}" ]]; then
      ROOT_RULE="" ROOT_UPSTREAM=$NGROK_ROOT_UPSTREAM
    else  # nothing else shares the domain yet: answer other paths with 404
      ROOT_RULE='        - actions:
            - type: custom-response
              config: {status_code: 404, content: "not found"}'
      ROOT_UPSTREAM=$EDGE_PORT
    fi
    cat >"$NGROK_CONF" <<YAML
version: "3"
endpoints:
  - name: mcpdemo-public
    url: https://$NGROK_DOMAIN
    upstream:
      url: $ROOT_UPSTREAM
    traffic_policy:
      on_http_request:
        - expressions:
            - "req.url.path == '$PUBLIC_PATH' || req.url.path.startsWith('$PUBLIC_PATH/') || req.url.path.startsWith('/.well-known/oauth-protected-resource$PUBLIC_PATH/') || req.url.path == '/onboarding' || req.url.path.startsWith('/onboarding/')"
          actions:
            - type: forward-internal
              config: {url: "https://mcpdemo-edge.internal"}
$ROOT_RULE
  - name: mcpdemo-edge
    url: https://mcpdemo-edge.internal
    upstream:
      url: $EDGE_PORT
YAML
    spawn ngrok "$RUN_DIR/ngrok.log" ngrok start mcpdemo-public mcpdemo-edge \
      --config "$(ngrok_default_config)" --config "$NGROK_CONF" --log=stdout
    for _ in $(seq 30); do
      code=$(curl -s -o /dev/null -w '%{http_code}' -H 'ngrok-skip-browser-warning: true' "$GATEWAY_PUBLIC/adapters" || true)
      [[ "$code" == 401 ]] && { ok "gateway public at $GATEWAY_PUBLIC/adapters (unauthenticated -> 401); chatbot at $CHAT_PUBLIC"; exit 0; }
      sleep 2
    done
    die "gateway not reachable through ngrok (last HTTP $code); see .run/ngrok.log and .run/port-forward.log" ;;
  stop)
    stop_pid ngrok "ngrok"; stop_pid port-forward "port-forward svc/mcpgateway-service"
    docker rm -f mcpdemo-edge >/dev/null 2>&1 || true; ok "tunnel, edge and port-forward stopped" ;;
  *) die "usage: tunnel.sh start|stop" ;;
esac
