#!/usr/bin/env bash
# Onboarding: two-browser Playwright e2e against the local stack (dev.sh: ISC stub + the scripted model, so it costs
# nothing; Constitution IV, research R28). REAL_MODEL=1 runs the same specs on real Claude Haiku on Bedrock (paid).
#   e2e.sh [playwright args...]     e.g. e2e.sh e2e/shared-session.spec.ts
# Each spec seeds its own users, tenant and session (smoke.py --seed). Leaves the stack running; dev.sh stop ends it.
source "$(dirname "$0")/lib.sh"
CALLS_PER_SPEC=40      # model calls per spec on the real model, measured on 2026-10-07 (about 9 k input tokens each)
USD_PER_CALL=0.004     # Claude Haiku 4.5 with prompt caching (research R27); about 0.01 uncached
if [[ "${REAL_MODEL:-}" == 1 ]]; then
  specs=$(ls "$ONB_ROOT"/web/e2e/*.spec.ts | wc -l | tr -d ' ')
  [[ $# -gt 0 ]] && specs=$(printf '%s\n' "$@" | grep -c '\.spec\.ts' || true)
  calls=$(( ${specs:-1} * CALLS_PER_SPEC ))
  warn "Real Claude Haiku on Bedrock: about $calls model calls, about \$$(echo "$calls * $USD_PER_CALL" | bc -l | xargs printf '%.2f')"
  export AGENT_MODEL=bedrock
else
  export AGENT_MODEL=fake
fi
"$ONB_ROOT/deploy/scripts/dev.sh" start
cd "$ONB_ROOT/web"
[[ -d node_modules ]] || npm ci
npx playwright install chromium >/dev/null
npx playwright test "$@"
step "leak scan"
"$ONB_ROOT/deploy/scripts/leak-scan.sh" dev
