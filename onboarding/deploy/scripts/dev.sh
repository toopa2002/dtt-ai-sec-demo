#!/usr/bin/env bash
# Run the whole onboarding stack locally against the ISC stub (no cluster, no AgentCore deploy):
#   MongoDB (docker, :27018) · ISC stub (:8099) · agent (:8092: AGENT_MODEL=bedrock (default) = real Claude Haiku on
#   Bedrock with your AWS credentials, paid per message; AGENT_MODEL=fake = the scripted model, free, for tests)
#   · session API (:8080) · web dev server (:4300 -> http://127.0.0.1:4300/onboarding/)
#   dev.sh start | public | stop | status     (public: build the web app, which the API then serves, and forward
#   :8091 -> the API for the shared edge / ngrok /onboarding/)
# Used by `make onboarding-e2e`. The agent gets a static stub token; the API discards tenant secrets (stub mode).
source "$(dirname "$0")/lib.sh"
LOGS="$RUN_DIR/onboarding-dev"; mkdir -p "$LOGS"

start_bg() {  # name, dir, command...
  local name=$1 dir=$2; shift 2
  # Detach fully: a caller piping our output (e.g. `dev.sh start | tail`) must not wait on the services' fds.
  (cd "$dir" && nohup "$@" >"$LOGS/$name.log" 2>&1 </dev/null & echo $! >"$LOGS/$name.pid") >/dev/null 2>&1 </dev/null
}
stop_bg() { local f="$LOGS/$1.pid"; [[ -f "$f" ]] && { pkill -P "$(cat "$f")" 2>/dev/null; kill "$(cat "$f")" 2>/dev/null; rm -f "$f"; } || true; }
wait_http() { for _ in $(seq 60); do curl -fsS -o /dev/null "$1" 2>/dev/null && return 0; sleep 1; done; die "$1 did not come up (see $LOGS)"; }

case "${1:-start}" in
  start)
    "$0" stop >/dev/null 2>&1 || true
    step "MongoDB (docker, 127.0.0.1:27018)"
    docker image inspect "$REGISTRY/onboarding-mongo:8.0" >/dev/null 2>&1 \
      || docker build -q -t "$REGISTRY/onboarding-mongo:8.0" "$ONB_ROOT/deploy/mongo" >/dev/null
    docker start onb-mongo-test >/dev/null 2>&1 || {
      docker run -d --name onb-mongo-test -p 127.0.0.1:27018:27017 "$REGISTRY/onboarding-mongo:8.0" \
        --replSet rs0 --bind_ip_all >/dev/null
      for _ in $(seq 30); do
        docker exec onb-mongo-test mongosh --quiet --eval 'try{rs.status().ok}catch(e){rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:27017"}]}).ok}' 2>/dev/null | grep -q 1 && break
        sleep 2
      done; }
    ok "mongodb"
    step "ISC stub :8099"
    start_bg stub "$ONB_ROOT/deploy/stub-isc" uv run --project "$ONB_ROOT/api" -q uvicorn stub_isc:app --port 8099
    wait_http http://127.0.0.1:8099/_stub/state; ok "stub"
    if [[ "${AGENT_MODEL:-bedrock}" == fake ]]; then step "Agent :8092 (scripted model: no Bedrock calls)"
    else step "Agent :8092 (Claude Haiku on Bedrock, model ${BEDROCK_MODEL_ID:-default}: paid per message)"; fi
    start_bg agent "$ONB_ROOT/agent" env PORT=8092 AWS_REGION="${AWS_REGION:-ap-southeast-1}" AGENT_MODEL="${AGENT_MODEL:-bedrock}" \
      BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-}" ONBOARDING_ISC_BASE_URL=http://127.0.0.1:8099 ONBOARDING_ISC_TOKEN=stub-token \
      CATALOG_DIR="$ONB_ROOT/catalog" uv run -q python -m onboarding_agent.main
    wait_http http://127.0.0.1:8092/ping; ok "agent"
    step "Session API :8080"
    start_bg api "$ONB_ROOT/api" env MONGO_URI="mongodb://127.0.0.1:27018/?replicaSet=rs0&directConnection=true" \
      MONGO_DB="${ONB_DEV_DB:-onboarding_dev}" AGENT_ENDPOINT=http://127.0.0.1:8092 CREDENTIAL_STORE=discard \
      ISC_BASE_URL=http://127.0.0.1:8099 COOKIE_SECURE=false CATALOG_DIR="$ONB_ROOT/catalog" \
      WEB_DIR="$ONB_ROOT/web/dist/onboarding-web/browser" \
      uv run -q uvicorn onboarding_api.main:app --host 127.0.0.1 --port 8080
    wait_http http://127.0.0.1:8080/onboarding/api/healthz; ok "api"
    if [[ "${NO_WEB:-}" != 1 ]]; then
      step "Web :4300"
      start_bg web "$ONB_ROOT/web" npx ng serve --port 4300
      wait_http http://127.0.0.1:4300/onboarding/; ok "http://127.0.0.1:4300/onboarding/"
    fi ;;
  public)
    # The API serves the production web build from WEB_DIR, as in the cluster's single container. :8091 is the shared
    # edge's /onboarding/ upstream (normally the cluster port-forward from up.sh); a forwarder sends it to this API.
    lsof -iTCP:8091 -sTCP:LISTEN >/dev/null 2>&1 && ! docker ps -q -f name=^onb-web-public$ | grep -q . \
      && die ":8091 is taken (cluster port-forward from up.sh?); stop it first"
    curl -fsS -o /dev/null http://127.0.0.1:8080/onboarding/api/healthz || die "API is down: dev.sh start first"
    step "Production web build"
    (cd "$ONB_ROOT/web" && npx ng build --configuration production >/dev/null) && ok "built"
    docker rm -f onb-web-public >/dev/null 2>&1 || true
    docker run -d --name onb-web-public -p 127.0.0.1:8091:8091 alpine/socat \
      tcp-listen:8091,fork,reuseaddr tcp:host.docker.internal:8080 >/dev/null
    wait_http http://127.0.0.1:8091/onboarding/
    ok "http://127.0.0.1:8091/onboarding/ (via the edge: https://${NGROK_DOMAIN:-<ngrok domain>}/onboarding/)" ;;
  stop)
    docker rm -f onb-web-public >/dev/null 2>&1 || true
    for n in web api agent stub; do stop_bg "$n"; done
    ok "stopped (MongoDB container left running: docker stop onb-mongo-test)" ;;
  status)
    for n in stub agent api web; do f="$LOGS/$n.pid"; [[ -f "$f" ]] && kill -0 "$(cat "$f")" 2>/dev/null && ok "$n up" || warn "$n down"; done ;;
  *) die "usage: dev.sh start|public|stop|status" ;;
esac
