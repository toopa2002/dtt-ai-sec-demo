#!/usr/bin/env bash
# Onboarding: two-browser Playwright e2e against the local stack (dev.sh: ISC stub, real Claude Haiku on Bedrock).
#   e2e.sh [playwright args...]     e.g. e2e.sh e2e/shared-session.spec.ts
# Each spec seeds its own users, tenant and session (smoke.py --seed). Leaves the stack running; dev.sh stop ends it.
source "$(dirname "$0")/lib.sh"
"$ONB_ROOT/deploy/scripts/dev.sh" start
cd "$ONB_ROOT/web"
[[ -d node_modules ]] || npm ci
npx playwright install chromium >/dev/null
npx playwright test "$@"
step "leak scan"
"$ONB_ROOT/deploy/scripts/leak-scan.sh" dev
