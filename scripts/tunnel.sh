#!/usr/bin/env bash
# Start/stop the gateway port-forward and the ngrok tunnel (gateway only).
source "$(dirname "$0")/lib.sh"
stop_pid() { # name, expected command substring; only kill if the PID still belongs to that process
  local f="$RUN_DIR/$1.pid" pid
  if [[ -f "$f" ]]; then
    pid=$(cat "$f")
    # Each process runs in its own session (setsid), so kill the whole process group.
    if ps -p "$pid" -o args= 2>/dev/null | grep -qF -- "$2"; then kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true; fi
    rm -f "$f"
  fi
}
case "${1:-start}" in
  start)
    need NGROK_DOMAIN
    stop_pid port-forward "port-forward svc/mcpgateway-service"; stop_pid ngrok "ngrok http"
    # kubectl port-forward pins one pod and exits when that pod goes away; keep re-attaching.
    setsid nohup bash -c "while true; do kubectl -n $NS port-forward svc/mcpgateway-service 8000:8000; sleep 1; done" >"$RUN_DIR/port-forward.log" 2>&1 &
    echo $! >"$RUN_DIR/port-forward.pid"
    setsid nohup ngrok http 8000 --url="https://$NGROK_DOMAIN" --log=stdout >"$RUN_DIR/ngrok.log" 2>&1 &
    echo $! >"$RUN_DIR/ngrok.pid"
    for _ in $(seq 30); do
      code=$(curl -s -o /dev/null -w '%{http_code}' -H 'ngrok-skip-browser-warning: true' "$GATEWAY_PUBLIC/adapters" || true)
      [[ "$code" == 401 ]] && { ok "gateway public at $GATEWAY_PUBLIC (unauthenticated -> 401)"; exit 0; }
      sleep 2
    done
    die "gateway not reachable through ngrok (last HTTP $code); see .run/ngrok.log and .run/port-forward.log" ;;
  stop)
    stop_pid ngrok "ngrok http"; stop_pid port-forward "port-forward svc/mcpgateway-service"; ok "tunnel and port-forward stopped" ;;
  *) die "usage: tunnel.sh start|stop" ;;
esac
