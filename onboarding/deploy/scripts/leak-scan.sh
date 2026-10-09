#!/usr/bin/env bash
# Onboarding: scan stored conversations and logs for leaked secrets (SC-004). Exit 1 on any hit.
#   leak-scan.sh [dev|cluster]     dev = dev.sh stack (docker onb-mongo-test, .run/onboarding-dev/*.log, default)
#                                  cluster = MongoDB pod and API pod logs in namespace onboarding
#   ONB_AGENT_LOG_GROUP=/aws/bedrock-agentcore/runtimes/<id>-DEFAULT also scans the AgentCore runtime's CloudWatch logs.
#   ONB_LEAK_LITERALS="value1 value2" also searches for known test secrets (spec 002 SC-102: the e2e run passes the
#                                    application secret it submitted); each is matched as a fixed string.
# Hits are reported by location and pattern only; the matched value is never printed.
source "$(dirname "$0")/lib.sh"
TARGET="${1:-dev}"
# AWS access key ids, JWTs / bearer tokens, 64-hex strings (tenant client secrets, AWS secret-like material), and
# Microsoft Entra client secret Values (spec 002 research R3: three characters, a digit, `Q~`, 31-34 more).
PATTERNS=('(AKIA|ASIA)[0-9A-Z]{16}' 'eyJ[A-Za-z0-9_-]{10,}' '\b[0-9a-fA-F]{64}\b'
          '[A-Za-z0-9_.-]{3}[0-9]Q~[A-Za-z0-9_.~-]{31,34}')
for lit in ${ONB_LEAK_LITERALS:-}; do
  PATTERNS+=("$(python3 -c 'import re,sys; print(re.escape(sys.argv[1]))' "$lit")")
done
COLLECTIONS='["messages","events","actions","audit","sessions"]'  # sessions: plan step titles and reasons (R22)
hits=0

MONGO_JS='
const pats = '"$(printf '%s\n' "${PATTERNS[@]}" | python3 -c 'import json,sys; print(json.dumps([l.rstrip("\n") for l in sys.stdin]))')"';
let n = 0;
for (const c of '"$COLLECTIONS"') {
  db.getCollection(c).find().forEach(d => {
    const s = EJSON.stringify(d);
    pats.forEach(p => { if (new RegExp(p).test(s)) { print(`HIT ${c} _id=${d._id} pattern=${p}`); n++; } });
  });
}
print(`SCANNED ${n}`);'

scan_mongo() {  # mongosh command prefix...
  local out; out=$("$@" --quiet --eval "$MONGO_JS") || die "MongoDB scan failed"
  grep '^HIT' <<<"$out" || true
  local n; n=$(sed -n 's/^SCANNED //p' <<<"$out"); hits=$((hits + ${n:-0}))
}

scan_text() {  # label (text on stdin)
  local label=$1 text; text=$(cat)
  for p in "${PATTERNS[@]}"; do
    local n; n=$(grep -Ec -- "$p" <<<"$text" || true)
    if (( n > 0 )); then echo "HIT $label pattern=$p lines=$n"; hits=$((hits + n)); fi
  done
}

case "$TARGET" in
  dev)
    step "MongoDB onboarding_dev (docker onb-mongo-test)"
    scan_mongo docker exec onb-mongo-test mongosh "${ONB_DEV_DB:-onboarding_dev}"
    step "dev logs ($RUN_DIR/onboarding-dev)"
    for f in "$RUN_DIR"/onboarding-dev/{api,agent}.log; do [[ -f "$f" ]] && scan_text "$(basename "$f")" <"$f"; done
    ;;
  cluster)
    step "MongoDB onboarding (pod mongodb-0)"
    scan_mongo kubectl -n "$ONB_NS" exec mongodb-0 -- mongosh onboarding
    step "API logs"
    kubectl -n "$ONB_NS" logs deploy/onboarding-api --all-containers --since=720h 2>/dev/null | scan_text "onboarding-api"
    ;;
  *) die "usage: leak-scan.sh [dev|cluster]" ;;
esac

if [[ -n "${ONB_AGENT_LOG_GROUP:-}" ]]; then
  step "AgentCore logs ($ONB_AGENT_LOG_GROUP, last 7 days)"
  start=$(( ($(date +%s) - 7 * 86400) * 1000 ))
  aws logs filter-log-events --log-group-name "$ONB_AGENT_LOG_GROUP" --start-time "$start" \
    --query 'events[].message' --output text 2>/dev/null | scan_text "agentcore"
fi

if (( hits > 0 )); then die "leak scan: $hits hit(s)"; fi
ok "leak scan: no secrets found"
