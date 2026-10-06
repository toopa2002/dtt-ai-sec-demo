# Shared helpers for the sailpoint-isc-aws-connector scripts. Source, don't execute.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASSETS="$SKILL_DIR/assets"
APPLY=0   # scripts default to dry-run; --apply flips this

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*" >&2; }
ok()   { printf '\033[32m✔ %s\033[0m\n' "$*" >&2; }
warn() { printf '\033[33m! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

need_cmd() { for c in "$@"; do command -v "$c" >/dev/null || die "'$c' is required but not installed"; done; }

# Run a mutating command, or just print it in dry-run mode.
run() {
  if (( APPLY )); then "$@"; else printf '\033[2m[dry-run]\033[0m %s\n' "$(printf '%q ' "$@")" >&2; fi
}

# Hide anything that looks like a secret before printing JSON or text.
redact() {
  sed -E 's/("(client_secret|secret|password|access_token|token|secretKey|accessKeySecret)"[[:space:]]*:[[:space:]]*")[^"]*"/\1***"/Ig;
          s/(client_secret=)[^&[:space:]]*/\1***/g'
}

is_account_id() { [[ "$1" =~ ^[0-9]{12}$ ]]; }

# ISC_TENANT is the tenant's full API host, so production and demo tenants both work:
#   acme.api.identitynow.com, acme-demo.api.identitynow-demo.com
# A pasted URL or the UI host (acme.identitynow-demo.com) is normalised to the API host.
isc_base_url() {
  local h="${1:-}"
  h="${h#http://}"; h="${h#https://}"; h="${h%%/*}"; h="$(tr '[:upper:]' '[:lower:]' <<<"$h")"
  [[ "$h" == *.* ]] || die "ISC_TENANT must be the full host, e.g. acme.api.identitynow.com or acme-demo.api.identitynow-demo.com (got '${1:-}')"
  if [[ "$h" =~ ^([a-z0-9-]+)\.(identitynow[a-z0-9-]*\.com)$ ]]; then
    h="${BASH_REMATCH[1]}.api.${BASH_REMATCH[2]}"   # UI host -> API host
  fi
  printf 'https://%s\n' "$h"
}
