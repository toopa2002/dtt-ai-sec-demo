# Shared helpers for the sailpoint-isc-entra-connector scripts. Source, don't execute.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASSETS="$SKILL_DIR/assets"
APPLY=0   # scripts default to dry-run; --apply flips this

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*" >&2; }
ok()   { printf '\033[32m✔ %s\033[0m\n' "$*" >&2; }
warn() { printf '\033[33m! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

need_cmd() { for c in "$@"; do command -v "$c" >/dev/null || die "'$c' is required but not installed"; done; }

# Hide anything that looks like a secret before printing JSON or text: any key containing secret/password/token/
# private key/certificate, plus JSON-Patch ops whose path names such a field (their value sits under "value").
redact() {
  sed -E 's/("[A-Za-z0-9_]*([Ss]ecret|SECRET|[Pp]assword|PASSWORD|[Tt]oken|[Pp]rivate_?[Kk]ey|[Cc]ertificate|[Pp]fx)[A-Za-z0-9_]*"[[:space:]]*:[[:space:]]*")[^"]*"/\1***"/g;
          s/(client_secret=)[^&[:space:]]*/\1***/g'
}
redact_json() {   # stdin JSON -> pretty JSON with secret values masked (handles JSON-Patch arrays too)
  jq 'def secretish: test("secret|password|token|private|certificate|pfx|thumbprint"; "i");
      if type == "array" then map(if (type == "object" and ((.path? // "") | secretish)) then .value = "***" else . end)
      else . end' 2>/dev/null | redact
}

# render_tmpl FILE VARS_JSON — fill ${VAR} placeholders in a JSON template. A string that is exactly "${VAR}" is
# replaced by the variable's JSON value (so arrays/objects/booleans keep their type); placeholders inside longer
# strings are substituted as text. An unset variable is an error, so a template can't go out half-filled.
render_tmpl() {
  jq --argjson v "$2" '
    def val($k): if ($v | has($k)) then $v[$k] else error("template variable \($k) is not set") end;
    def fill: if test("^\\$\\{[A-Z0-9_]+\\}$") then val(.[2:-1])
              else gsub("\\$\\{(?<k>[A-Z0-9_]+)\\}"; val(.k) | tostring) end;
    walk(if type == "string" then fill else . end)' "$1"
}

is_guid() { [[ "$1" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; }
slug()    { tr '[:upper:]' '[:lower:]' <<<"$1" | sed -E 's/[^a-z0-9]+/-/g; s/^-|-$//g'; }

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
