#!/usr/bin/env bash
# COMMAND 2 — ISC side: create, configure, verify and aggregate a Microsoft Entra (SaaS) source through the ISC REST
# API, using the templates in assets/isc/. Every call uses the v2026 API
# (https://developer.sailpoint.com/redoc/sailpoint-api-v2026-light.html) — no /beta/, no mixing of versions.
# All v2026 endpoints used are GA except POST /sources/{id}/aggregate-agents (dataset aggregation) and
# GET /machine-identities, which require X-SailPoint-Experimental: true.
#
# Verified on a live tenant (2026-10-08): the SaaS connector is "Microsoft Entra" (scriptName "Microsoft-Entra", spec
# id b7e9374a-…), distinct from the VA-based "Azure Active Directory". Its source-config form has domainName, clientID,
# clientSecret, grantType (CLIENT_CREDENTIALS | REFRESH_TOKEN | CERTIFICATE_CREDENTIALS) and the toggles in
# assets/isc/feature-toggles.json. Datasets: azure:foundry, microsoft:copilot, microsoft:agent365. v2026 /accounts
# filters on source.id (sourceId is rejected there, despite the spec text).
#
# Env: ISC_TENANT = full API host (acme.api.identitynow.com, acme-demo.api.identitynow-demo.com) or ISC_BASE_URL,
#      ISC_CLIENT_ID, ISC_CLIENT_SECRET (PAT with ORG_ADMIN or SOURCE_ADMIN).
# Read-only calls always run; writes are dry-run unless --apply. --plan-only makes no calls at all (no token needed).
set -euo pipefail
source "$(dirname "$0")/lib.sh"

usage() {
  cat <<'EOF'
Usage: isc-source.sh <subcommand> [options] [--apply | --plan-only]

  discover  [--connector SCRIPT] [--from-source ID|NAME]
            Find the Microsoft Entra (SaaS) connector, list existing sources on it, build the attribute key map.
  create    --name NAME --owner ALIAS|EMAIL|ID|me [--description TEXT] [--reuse]
            Create the source. An existing source with that name is reused only if you own it (or with --reuse).
  configure --source ID|NAME [--from-setup sailpoint-entra-setup.json]
            [--domain D] [--client-id ID] [--client-secret-file F] [--grant-type VALUE]
            [--profiles P,P] [--exchange-pfx-file F --exchange-pfx-password-file F]
            Connection settings + the feature toggles for the profiles (assets/isc/feature-toggles.json).
  verify    --source ID|NAME [--only entitlement,account,dataset] [--datasets ID,ID]
            Prove it works: peek -> test -> aggregate, stopping at the first failure.
  peek      --source ID|NAME          Read a few accounts through the connector.
  test      --source ID|NAME          Run "Test Connection" (test-configuration).
  aggregate --source ID|NAME [--only entitlement,account,dataset] [--datasets ID,ID] [--full] [--no-wait]
            Entitlement, then account, then dataset (machine identity / AI agent) aggregation. Datasets default to
            the ones switched on on the source (enableAIFoundryAgent / enableCopilotAIAgent / enableMicrosoftAgent365).
            --full: with Delta Aggregation on, run the account aggregation with delta switched off for that run (needed
            after changing what is aggregated, e.g. service principals) and switch it back on afterwards. verify does this.
  datasets  --source ID|NAME          List the source's datasets (informational: this endpoint isn't in the
                                      published v2026 spec; aggregation uses the source toggles instead).
  dataset-schedule --source ID|NAME --on|--off [--datasets ID,ID]
            Turn a dataset's scheduled aggregation (UI "Enable Schedule") on or off. Default datasets: those switched
            on on the source. Uses PUT /v2026/sources/{id}/datasets/{datasetId} — the call the UI makes; it is not in
            the published v2026 spec, so treat it as undocumented (the schedule frequency is ISC's default).
  schema-spn --source ID|NAME   Add the service-principal attributes (assets/isc/account-schema-spn-attributes.json)
            to the account schema; configure runs it for the machine-identity profile.
  provisioning-policy --source ID|NAME --upn-domain D [--usage-location "United States;US"] [--replace]
            Create (or with --replace, overwrite) the CREATE provisioning policy for joiners.
  correlation --source ID|NAME        Set account correlation from assets/isc/correlation-config.tmpl.json.
  show      --source ID|NAME          Print the source (secrets redacted).
  schedule  --source ID|NAME [--cron "0 0 */4 * * ?"] [--types account,group] [--clear]
            Show, set or remove scheduled aggregations (Quartz cron, 6-7 fields, seconds first).

Common: --keymap FILE, --apply (perform writes; default dry-run), --plan-only (no API calls, placeholders for ids)
EOF
}

(($#)) || { usage; exit 1; }
CMD="$1"; shift
[[ "$CMD" =~ ^(-h|--help|help)$ ]] && { usage; exit 0; }
CONNECTOR="" FROM_SOURCE="" NAME="" OWNER="" DESCRIPTION="Microsoft Entra source — managed via sailpoint-isc-entra-connector skill"
SOURCE="" FROM_SETUP="" DOMAIN="" CLIENT_ID="" SECRET_FILE="" GRANT_TYPE="" PROFILES="" EXO_PFX="" EXO_PASS=""
WAIT=1 CRON="" SCHED_TYPES="account,group" CLEAR=0 KEYMAP="" REUSE=0 OFFLINE=0
ONLY="entitlement,account,dataset" DATASETS="" SCHED_ON="" FULL=0 UPN_DOMAIN="" USAGE_LOCATION="United States;US" REPLACE=0
while (($#)); do
  case "$1" in
    --connector) CONNECTOR="$2"; shift ;;
    --from-source) FROM_SOURCE="$2"; shift ;;
    --name) NAME="$2"; shift ;;
    --owner) OWNER="$2"; shift ;;
    --description) DESCRIPTION="$2"; shift ;;
    --reuse) REUSE=1 ;;
    --source) SOURCE="$2"; shift ;;
    --from-setup) FROM_SETUP="$2"; shift ;;
    --domain) DOMAIN="$2"; shift ;;
    --client-id) CLIENT_ID="$2"; shift ;;
    --client-secret-file) SECRET_FILE="$2"; shift ;;
    --client-secret) die "don't pass the secret on the command line (it lands in shell history); put it in a chmod 600 file and use --client-secret-file" ;;
    --grant-type) GRANT_TYPE="$2"; shift ;;
    --profiles) PROFILES="$2"; shift ;;
    --exchange-pfx-file) EXO_PFX="$2"; shift ;;
    --exchange-pfx-password-file) EXO_PASS="$2"; shift ;;
    --only) ONLY="$2"; shift ;;
    --entitlements) ;;   # accepted for compatibility: entitlement aggregation is now part of the default
    --datasets) DATASETS="$2"; shift ;;
    --upn-domain) UPN_DOMAIN="$2"; shift ;;
    --on) SCHED_ON=true ;;
    --off) SCHED_ON=false ;;
    --usage-location) USAGE_LOCATION="$2"; shift ;;
    --replace) REPLACE=1 ;;
    --no-wait) WAIT=0 ;;
    --full) FULL=1 ;;
    --cron) CRON="$2"; shift ;;
    --types) SCHED_TYPES="$2"; shift ;;
    --clear) CLEAR=1 ;;
    --keymap) KEYMAP="$2"; shift ;;
    --apply) APPLY=1 ;;
    --plan-only) OFFLINE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "unknown option: $1" ;;
  esac
  shift
done
(( OFFLINE && APPLY )) && die "--plan-only and --apply don't mix"
for o in ${ONLY//,/ }; do [[ "$o" =~ ^(entitlement|account|dataset)$ ]] || die "--only takes entitlement, account and/or dataset"; done

need_cmd jq
(( OFFLINE )) || need_cmd curl
if [[ -z "${ISC_BASE_URL:-}" ]]; then
  if [[ -n "${ISC_TENANT:-}" ]]; then ISC_BASE_URL="$(isc_base_url "$ISC_TENANT")"
  elif (( OFFLINE )); then ISC_BASE_URL="https://<tenant>.api.identitynow.com"
  else die "set ISC_TENANT to the tenant's full API host (e.g. acme.api.identitynow.com)"; fi
fi
ISC_BASE_URL="${ISC_BASE_URL%/}"
TENANT_TAG="$(sed -E 's#^https?://##; s#[^A-Za-z0-9.-]#_#g' <<<"$ISC_BASE_URL")"
KEYMAP="${KEYMAP:-${XDG_CACHE_HOME:-$HOME/.cache}/sailpoint-isc-entra/${TENANT_TAG}-keymap.json}"
(( OFFLINE )) && warn "plan-only: no API calls; reads return nothing and ids are <placeholders>"

# --- HTTP ------------------------------------------------------------------------------------------
TOKEN="" TOKEN_IDENTITY=""
token() {
  (( OFFLINE )) && { TOKEN_IDENTITY="<your identity id>"; return; }
  [[ -n "$TOKEN" ]] && return
  [[ -n "${ISC_CLIENT_ID:-}" && -n "${ISC_CLIENT_SECRET:-}" ]] || die "set ISC_CLIENT_ID and ISC_CLIENT_SECRET (a personal access token)"
  local resp
  resp="$(curl -sS --fail-with-body -X POST "$ISC_BASE_URL/oauth/token" \
          --data-urlencode grant_type=client_credentials \
          --data-urlencode "client_id=$ISC_CLIENT_ID" \
          --data-urlencode "client_secret=$ISC_CLIENT_SECRET")" || {
    # A PAT's Client ID is 32 hex chars and its Secret 64; pasting them the wrong way round is an easy mistake.
    if [[ "${#ISC_CLIENT_ID}" == 64 && "${#ISC_CLIENT_SECRET}" == 32 ]]; then
      die "token request failed — ISC_CLIENT_ID is 64 chars and ISC_CLIENT_SECRET 32: they look swapped (the ID is the 32-char one)"
    fi
    die "token request failed: $(redact <<<"${resp:-}")"; }
  TOKEN="$(jq -r '.access_token // empty' <<<"$resp")"
  TOKEN_IDENTITY="$(jq -r '.identity_id // empty' <<<"$resp")"   # set for personal access tokens
  [[ -n "$TOKEN" ]] || die "no access_token in token response"
}

API=/v2026
# Only experimental endpoints get the experimental header; everything else used here is GA in v2026.
# (sources/{id}/datasets[/{datasetId}] is not in the published v2026 spec; only `datasets` and `dataset-schedule` use it.)
EXPERIMENTAL_RE='/aggregate-agents$|/datasets$|/datasets/|/machine-identities\?'

# api METHOD PATH [BODY] [CONTENT_TYPE] — prints the response body. Writes are skipped in dry-run.
# CONTENT_TYPE multipart/form-data takes BODY as k=v&k=v and sends each pair as a form field.
api() {
  local method="$1" path="$2" body="${3:-}" ctype="${4:-application/json}"
  if [[ "$method" == GET ]] && (( OFFLINE )); then
    if [[ "$path" == *\?* || "$path" =~ /(schedules|datasets|provisioning-policies)$ ]]; then echo '[]'; else echo '{}'; fi
    return
  fi
  if [[ "$method" != GET ]] && (( ! APPLY )); then
    printf '\033[2m[dry-run]\033[0m %s %s%s\n' "$method" "$ISC_BASE_URL$path" \
      "$([[ "$path" =~ $EXPERIMENTAL_RE ]] && echo '  (X-SailPoint-Experimental: true)')" >&2
    if [[ "$ctype" == multipart/form-data ]]; then
      [[ -n "$body" ]] && printf '    multipart: %s\n' "$(sed 's/&/ /g; s/\([^ ]*\)/-F \1/g' <<<"$body")" >&2
    elif [[ -n "$body" ]]; then
      if (( ${#body} > 300 )); then redact_json <<<"$body" | sed 's/^/    /' >&2
      else printf '    %s\n' "$(redact_json <<<"$body" | jq -c . 2>/dev/null || redact <<<"$body")" >&2; fi
    fi
    echo '{}'; return
  fi
  token
  local args=(-sS -X "$method" -H "Authorization: Bearer $TOKEN" -H "Accept: application/json, */*" -w '\n%{http_code}')
  [[ "$path" =~ $EXPERIMENTAL_RE ]] && args+=(-H "X-SailPoint-Experimental: true")
  if [[ "$ctype" == multipart/form-data ]]; then
    local kv; IFS='&' read -ra _kvs <<<"$body"; for kv in "${_kvs[@]}"; do [[ -n "$kv" ]] && args+=(-F "$kv"); done
  elif [[ -n "$body" ]]; then
    args+=(-H "Content-Type: $ctype" --data-binary "$body")
  fi
  local out code
  out="$(curl "${args[@]}" "$ISC_BASE_URL$path")" || die "$method $path: request failed"
  code="${out##*$'\n'}"; out="${out%$'\n'*}"
  [[ "$code" =~ ^2 ]] || die "$method $path -> HTTP $code: $(redact <<<"$out" | head -c 1500)"
  printf '%s\n' "$out"
}
uri() { jq -rn --arg s "$1" '$s|@uri'; }
total() {   # PATH (with filters) [extra curl args] -> X-Total-Count
  (( OFFLINE )) && { echo "?"; return; }
  token
  local path="$1"; shift
  curl -sS -o /dev/null -D - -H "Authorization: Bearer $TOKEN" "$@" "$ISC_BASE_URL$path&count=true&limit=1" \
    | tr -d '\r' | awk -F': ' 'tolower($1)=="x-total-count"{print $2}'
}

# --- Helpers ---------------------------------------------------------------------------------------
load_keymap() {
  if [[ ! -f "$KEYMAP" ]]; then
    (( OFFLINE )) || die "no key map at $KEYMAP — run: isc-source.sh discover"
    KEYMAP="$ASSETS/isc/keymap.default.json"; warn "plan-only: using the built-in key map ($KEYMAP)"
  fi
  jq -e .keys "$KEYMAP" >/dev/null || die "key map $KEYMAP has no .keys"
}

source_id() {   # accept an id or a name
  local s="${1:?--source is required}"
  if [[ "$s" =~ ^[0-9a-f]{32}$ ]]; then echo "$s"; return; fi
  (( OFFLINE )) && { echo "<id of source '$s'>"; return; }
  local id
  id="$(api GET "$API/sources?filters=$(uri "name eq \"$s\"")" | jq -r '.[0].id // empty')"
  [[ -n "$id" ]] || die "no source named '$s'"
  echo "$id"
}

# Canonical setting -> field name on this tenant's form: exact name first, then case-insensitive. All canonical
# names were seen on a live form; the key map makes a renamed field visible instead of silently sending a dead key.
CANONICAL='["domainName","clientID","clientSecret","grantType",
  "manageExchangeOnline","exoAuthenticationType","exchangeCertificate","exchangeCertificatePassword",
  "aggregateAllGroups","aggregateGroupHierarchy","enablePIM","spnManageAzurePIM","spnManageAzureADPIM",
  "manageAzureServicePrincipalAsAccount","enableManagedIdentityManagement","enableSystemAssignedManagedIdentity",
  "enableAIFoundryAgent","enableCopilotAIAgent","enableMicrosoftAgent365","enableTeamsGovernance","enableCIEM",
  "deltaAggregationEnabled","pageSize","manageO365Groups","enableAccessPackageManagement","aggregateGroupHierarchy",
  "spnAccountFilter","spnManageDirectoryRole","spnManageAppRoles","spnManageGroups","spnManageRBACRoles",
  "manageAdminConsentedPermissions","manageCustomSecurityAttributesForServicePrincipals","foundryAggregateLatestVersionOnly"]'
build_keymap() {  # stdin: JSON array of field names; $1 script, $2 connector name, $3 discovered via, $4 grantType values
  jq --arg script "$1" --arg cname "$2" --arg via "$3" --argjson gt "$4" --argjson canon "$CANONICAL" '
    def pick(f): (map(select(f)) | .[0]) // null;
    def exact($k): pick(. == $k) // pick(ascii_downcase == ($k | ascii_downcase));
    unique as $f | {
      connector: $script, connectorName: $cname, discoveredVia: $via, grantTypeValues: $gt, allFields: $f,
      keys: ($canon | map(. as $k | {key: $k, value: ($f | exact($k))}) | from_entries)
    }'
}

# --- Subcommands -------------------------------------------------------------------------------------
cmd_discover() {
  (( OFFLINE )) && die "discover reads your tenant; it has no plan-only mode"
  step "Finding the Microsoft Entra (SaaS) connector"
  local list cname
  list="$( { api GET "$API/connectors?filters=$(uri 'name co "Entra"')&limit=250"
             api GET "$API/connectors?filters=$(uri 'name co "Azure"')&limit=250"; } | jq -s 'add | unique_by(.scriptName)')"
  jq -r '.[] | "  \(.name)\t\(.scriptName)"' <<<"$list" >&2
  if [[ -z "$CONNECTOR" ]]; then
    # The SaaS connector is displayed as "Microsoft Entra" (older docs: "Microsoft Entra SaaS"). Match the whole
    # name: "Entra" also occurs inside other names ("Business Central", "Microsoft Entra SSO", "Activity Insights -
    # Microsoft Entra ID"), and the VA connector is "Azure Active Directory".
    CONNECTOR="$(jq -r '( [.[] | select(.scriptName == "Microsoft-Entra")]
                        + [.[] | select(.name | test("^Microsoft Entra( SaaS)?$"; "i"))] )[0].scriptName // empty' <<<"$list")"
    [[ -n "$CONNECTOR" ]] || die "no 'Microsoft Entra' SaaS connector in the list above; rerun with --connector <scriptName>"
  fi
  cname="$(jq -r --arg s "$CONNECTOR" '[.[] | select(.scriptName == $s)][0].name // $s' <<<"$list")"
  ok "connector: $cname (scriptName $CONNECTOR)"
  # For SaaS connectors .type is the connector spec id; create must send it or no connector instance is made.
  local ctype; ctype="$(api GET "$API/connectors/$(uri "$CONNECTOR")" | jq -r '.type // empty')"

  step "Existing sources on this connector"
  local existing
  existing="$(api GET "$API/sources?filters=$(uri "connectorName eq \"$cname\"")&limit=250" | jq -c --arg s "$CONNECTOR" '[.[] | select(.connector == $s)]')"
  if [[ "$(jq length <<<"$existing")" -eq 0 ]]; then ok "none"; else
    jq -r '.[] | "  \(.name)\t\(.id)\towner: \(.owner.name // "?")\tdomain: \(.connectorAttributes.domainName // "-")"' <<<"$existing" >&2
    warn "check none of these already covers the same Entra tenant before creating another"
  fi

  local fields via gt='[]'
  if [[ -n "$FROM_SOURCE" ]]; then
    via="source $FROM_SOURCE"
    fields="$(api GET "$API/sources/$(source_id "$FROM_SOURCE")" | jq '.connectorAttributes | keys')"
  else
    via="source-config form"
    local form
    form="$(api GET "$API/connectors/$(uri "$CONNECTOR")/source-config")"
    # XML form: each <Field name="…"> is a connectorAttributes key (Menu/Section names are not).
    fields="$(grep -oE '<Field[^>]*[[:space:]]name="[A-Za-z0-9_.-]+"' <<<"$form" \
              | sed -E 's/.*[[:space:]]name="([^"]+)"$/\1/' | sort -u | jq -R . | jq -s .)"
    # Allowed values of the authentication-type field, so configure sends the exact Client Credentials value.
    gt="$(awk '/<Field[^>]*name="grantType"/,/<\/Field>/' <<<"$form" | grep -oE 'value="[^"]+"' \
          | sed -E 's/value="([^"]+)"/\1/' | sort -u | jq -R . | jq -sc . || echo '[]')"
    if [[ "$(jq length <<<"$fields")" -eq 0 ]]; then   # JSON-shaped form fallback
      fields="$(jq '[.. | objects | (.key? // .name?) | strings] | unique' <<<"$form" 2>/dev/null || echo '[]')"
    fi
  fi
  [[ "$(jq length <<<"$fields")" -gt 0 ]] || die "no field names found via $via. Create the source in the UI, then: discover --from-source <name>"

  mkdir -p "$(dirname "$KEYMAP")"
  build_keymap "$CONNECTOR" "$cname" "$via" "$gt" <<<"$fields" | jq --arg t "$ctype" '.connectorType = $t' >"$KEYMAP"
  ok "key map written to $KEYMAP"
  jq '{connector, connectorType, grantTypeValues, keys}' "$KEYMAP"
  local missing
  missing="$(jq -r '.keys | to_entries[] | select(.value == null and (.key | IN("domainName","clientID","clientSecret","grantType"))) | .key' "$KEYMAP")"
  [[ -z "$missing" ]] || warn "required settings not found on the form: $(tr '\n' ' ' <<<"$missing")— pick the right field from .allFields in $KEYMAP"
  local opt; opt="$(jq -r '[.keys | to_entries[] | select(.value == null) | .key] | join(" ")' "$KEYMAP")"
  [[ -z "$opt" ]] || warn "not on this tenant's form (toggles for them will be skipped): $opt"
}

cmd_create() {
  [[ -n "$NAME" && -n "$OWNER" ]] || die "create needs --name and --owner"
  load_keymap
  step "Source '$NAME'"
  local existing id
  existing="$(api GET "$API/sources?filters=$(uri "name eq \"$NAME\"")")"
  id="$(jq -r '.[0].id // empty' <<<"$existing")"
  if [[ -n "$id" ]]; then
    local owner_id owner_name
    owner_id="$(jq -r '.[0].owner.id // empty' <<<"$existing")"; owner_name="$(jq -r '.[0].owner.name // "?"' <<<"$existing")"
    [[ "$(jq -r '.[0].connector' <<<"$existing")" == "$(jq -r .connector "$KEYMAP")" ]] \
      || die "a source named '$NAME' exists but uses connector $(jq -r '.[0].connector' <<<"$existing"), not $(jq -r .connector "$KEYMAP") — pick another --name"
    if [[ "$owner_id" == "$TOKEN_IDENTITY" || $REUSE -eq 1 ]]; then
      ok "already exists ($id, owner $owner_name); reusing it"
    else
      die "a source named '$NAME' already exists ($id) and belongs to $owner_name. Pick another --name, or pass --reuse if it really is yours to change."
    fi
  else
    local owner_id="$OWNER"
    if [[ "$OWNER" == me ]]; then
      owner_id="$TOKEN_IDENTITY"
      [[ -n "$owner_id" ]] || die "the token response has no identity_id; pass --owner <alias|email|id>"
      ok "owner = token owner $owner_id"
    elif ! [[ "$OWNER" =~ ^[0-9a-f]{32}$ ]]; then
      local field=alias; [[ "$OWNER" == *@* ]] && field=email
      if (( OFFLINE )); then owner_id="<identity id of $OWNER>"; else
        owner_id="$(api GET "$API/public-identities?filters=$(uri "$field eq \"$OWNER\"")" | jq -r '.[0].id // empty')"
        [[ -n "$owner_id" ]] || die "no identity with $field '$OWNER'"
      fi
      ok "owner $OWNER -> $owner_id"
    fi
    # SaaS connectors need spConnectorSpecId + idnProxyType at creation: ISC then creates and links the connector
    # instance (spConnectorInstanceId). They are stripped from later PATCHes, so a source created without them
    # has to be deleted and recreated.
    local spec body
    spec="$(jq -r '.connectorType // empty' "$KEYMAP")"
    [[ -n "$spec" ]] || die "key map has no connectorType — rerun: isc-source.sh discover"
    body="$(render_tmpl "$ASSETS/isc/source-create.tmpl.json" "$(jq -nc --arg n "$NAME" --arg d "$DESCRIPTION" \
              --arg o "$owner_id" --arg c "$(jq -r .connector "$KEYMAP")" --arg s "$spec" \
              '{SOURCE_NAME: $n, SOURCE_DESCRIPTION: $d, OWNER_ID: $o, CONNECTOR_SCRIPT: $c, CONNECTOR_SPEC_ID: $s}')")"
    id="$(api POST "$API/sources" "$body" | jq -r '.id // empty')"
    if (( ! APPLY )); then warn "dry-run: source not created"; return; fi
    [[ -n "$id" ]] || die "create returned no id"
    ok "created source $id"
    local inst; inst="$(api GET "$API/sources/$id" | jq -r '.connectorAttributes.spConnectorInstanceId // empty')"
    [[ -n "$inst" ]] && ok "connector instance $inst" \
      || warn "no spConnectorInstanceId on the new source — test/peek will fail; see references/troubleshooting.md"
  fi
  jq -n --arg id "$id" --arg name "$NAME" '{sourceId: $id, name: $name}'
}

cmd_configure() {
  if [[ -n "$FROM_SETUP" ]]; then
    [[ -f "$FROM_SETUP" ]] || die "no such file: $FROM_SETUP"
    jq -e '.applied == true' "$FROM_SETUP" >/dev/null || (( OFFLINE )) \
      || die "$FROM_SETUP is from a dry-run; run entra-setup.sh --apply first"
    DOMAIN="${DOMAIN:-$(jq -r '.domainName // empty' "$FROM_SETUP")}"
    CLIENT_ID="${CLIENT_ID:-$(jq -r '.clientId // empty' "$FROM_SETUP")}"
    SECRET_FILE="${SECRET_FILE:-$(jq -r '.clientSecretFile // empty' "$FROM_SETUP")}"
    PROFILES="${PROFILES:-$(jq -r '.profiles | join(",")' "$FROM_SETUP")}"
    EXO_PFX="${EXO_PFX:-$(jq -r '.exchange.pfxBase64File // empty' "$FROM_SETUP")}"
    EXO_PASS="${EXO_PASS:-$(jq -r '.exchange.pfxPasswordFile // empty' "$FROM_SETUP")}"
  fi
  [[ -n "$DOMAIN" && -n "$CLIENT_ID" && -n "$SECRET_FILE" ]] \
    || die "configure needs --domain, --client-id and --client-secret-file (or --from-setup)"
  [[ "$DOMAIN" == *.* ]] || is_guid "$DOMAIN" || die "--domain is the tenant's domain, e.g. contoso.onmicrosoft.com"
  [[ "$DOMAIN" =~ \.onmicrosoft\.(com|us)$ ]] || warn "SailPoint recommends the initial *.onmicrosoft.com domain (custom domains aren't supported for CIEM)"
  is_guid "$CLIENT_ID" || (( OFFLINE )) || die "--client-id must be the app's Application (client) ID GUID"
  local secret
  if [[ -s "$SECRET_FILE" ]]; then
    secret="$(tr -d '\r\n' <"$SECRET_FILE")"
    is_guid "$secret" && die "the secret file holds a GUID — that's the secret's ID, not its Value (gives AADSTS7000215). Create a new secret and save its Value."
  elif (( OFFLINE )); then secret="<value from $SECRET_FILE>"
  else die "secret file $SECRET_FILE is missing or empty"; fi
  load_keymap
  local id; id="$(source_id "$SOURCE")"

  if [[ -z "$GRANT_TYPE" ]]; then
    GRANT_TYPE="$(jq -r '[(.grantTypeValues // [])[] | select(test("client.?cred"; "i"))][0] // empty' "$KEYMAP")"
    if [[ -z "$GRANT_TYPE" ]]; then
      local gk cur; gk="$(jq -r '.keys.grantType // "grantType"' "$KEYMAP")"
      cur="$(api GET "$API/sources/$id" | jq -r --arg k "$gk" '.connectorAttributes[$k] // empty')"
      [[ -n "$cur" ]] || die "couldn't tell which grantType value means Client Credentials (form values: $(jq -c '.grantTypeValues' "$KEYMAP")). Pass --grant-type VALUE"
      GRANT_TYPE="$cur"; warn "keeping the source's current grantType '$cur' (pass --grant-type to change it)"
    fi
  fi

  # Connection settings from the template, then the feature toggles for the selected profiles.
  local ops toggles
  ops="$(render_tmpl "$ASSETS/isc/source-configure.patch.tmpl.json" "$(jq -nc --arg d "$DOMAIN" --arg c "$CLIENT_ID" \
           --arg s "$secret" --arg g "$GRANT_TYPE" '{DOMAIN_NAME: $d, CLIENT_ID: $c, CLIENT_SECRET: $s, GRANT_TYPE: $g}')")"
  unset secret
  # readonly (base settings) first, then the profiles in the order given; later profiles win on a shared key.
  toggles="$(jq -c --arg p "readonly,$PROFILES" '. as $t | reduce ($p | split(",")[] | select(. != "")) as $n ([]; if index($n) then . else . + [$n] end)
                | map(. as $n | if ($t | has($n)) then $t[$n] else error("unknown profile \($n)") end) | add // {}' \
               "$ASSETS/isc/feature-toggles.json")"
  ops="$(jq -c --argjson t "$toggles" '. + [$t | to_entries[] | {op: "add", path: "/connectorAttributes/\(.key)", value: .value}]' <<<"$ops")"
  if [[ -n "$EXO_PFX" ]]; then
    if [[ -s "$EXO_PFX" && -s "$EXO_PASS" ]]; then
      ops="$(jq -c --rawfile pfx "$EXO_PFX" --rawfile pw "$EXO_PASS" \
               '. + [{op: "add", path: "/connectorAttributes/exchangeCertificate", value: ($pfx | gsub("\\s"; ""))},
                     {op: "add", path: "/connectorAttributes/exchangeCertificatePassword", value: ($pw | rtrimstr("\n"))}]' <<<"$ops")"
    elif (( OFFLINE )); then
      ops="$(jq -c '. + [{op: "add", path: "/connectorAttributes/exchangeCertificate", value: "<base64 pfx>"},
                         {op: "add", path: "/connectorAttributes/exchangeCertificatePassword", value: "<pfx password>"}]' <<<"$ops")"
    else die "--exchange-pfx-file and --exchange-pfx-password-file must both point at non-empty files"; fi
  fi
  # Map canonical names to this tenant's field names; drop (and report) settings its form doesn't have.
  ops="$(jq -c --slurpfile km "$KEYMAP" '
          map((.path | ltrimstr("/connectorAttributes/")) as $k | ($km[0].keys[$k] // null) as $real
              | if $real == null then {skip: $k} else .path = "/connectorAttributes/\($real)" end)' <<<"$ops")"
  local skipped; skipped="$(jq -r '[.[] | .skip // empty] | join(", ")' <<<"$ops")"
  jq -e '[.[] | .skip // empty] | any(IN("domainName","clientID","clientSecret","grantType"))' <<<"$ops" >/dev/null \
    && die "the key map has no field for a required setting ($skipped) — run discover or edit $KEYMAP"
  [[ -z "$skipped" ]] || warn "not on this tenant's form, set in the UI if needed: $skipped"
  ops="$(jq -c 'map(select(has("skip") | not))' <<<"$ops")"

  step "Configuring source $id (domain $DOMAIN, client $CLIENT_ID, grantType $GRANT_TYPE)"
  api PATCH "$API/sources/$id" "$ops" application/json-patch+json >/dev/null
  if (( APPLY )); then ok "source updated"; else warn "dry-run only; rerun with --apply"; fi
  # The UI extends the account schema when service principals are switched on; the API doesn't, so do it here.
  [[ ",$PROFILES," == *",machine-identity,"* ]] && cmd_schema_spn "$id"
  (( APPLY )) && warn "settings changed: run verify (or aggregate --full) so the next account aggregation reads everything now in scope"
  if [[ ",$PROFILES," == *",ai-agents,"* ]]; then
    warn "ai-agents turns on Copilot Studio agents too: the microsoft:copilot dataset only works once the app is an application user in each Power Platform environment (references/entra-permissions.md). Agent 365 stays off (needs a user refresh token)."
  fi
  return 0
}

cmd_show() { api GET "$API/sources/$(source_id "$SOURCE")" | redact_json; }

# Delta Aggregation (deltaAggregationEnabled) makes the connector return only changes since the last run: peek
# then returns nothing, and objects newly in scope (e.g. service principals after switching them on) are never
# read. Seen live. delta_off/delta_restore wrap a call that needs the full set.
DELTA_WAS_ON=0
delta_off() {   # source id
  (( APPLY )) || return 0
  [[ "$(api GET "$API/sources/$1" | jq -r '.connectorAttributes.deltaAggregationEnabled // false | tostring')" == true ]] || return 0
  DELTA_WAS_ON=1
  api PATCH "$API/sources/$1" '[{"op":"replace","path":"/connectorAttributes/deltaAggregationEnabled","value":false}]' application/json-patch+json >/dev/null
  warn "Delta Aggregation switched off for this run (it only returns changes); it is switched back on afterwards"
}
delta_restore() {
  (( DELTA_WAS_ON )) || return 0
  api PATCH "$API/sources/$1" '[{"op":"replace","path":"/connectorAttributes/deltaAggregationEnabled","value":true}]' application/json-patch+json >/dev/null
  DELTA_WAS_ON=0; ok "Delta Aggregation switched back on"
}

cmd_peek() {
  local id; id="$(source_id "$SOURCE")"
  step "Peek accounts ($id)"
  delta_off "$id"
  local r; r="$(api POST "$API/sources/$id/connector/peek-resource-objects" '{"objectType":"account","maxCount":5}')" || { delta_restore "$id"; exit 1; }
  delta_restore "$id"
  (( APPLY )) || { warn "dry-run: peek not run (it's a POST, but read-only; add --apply)"; return 0; }
  local n; n="$(jq '.resourceObjects // [] | length' <<<"$r")"
  jq -r '.resourceObjects // [] | .[] | "  " + (.identity // .name)' <<<"$r" >&2
  (( n > 0 )) || die "connector returned no accounts: $(jq -c '.details // .' <<<"$r" | head -c 800) — see references/troubleshooting.md"
  ok "peek: connector read $n account(s) from Entra"
}

cmd_test() {
  local id; id="$(source_id "$SOURCE")"
  step "Test connection ($id)"
  local r; r="$(api POST "$API/sources/$id/connector/test-configuration")"
  (( APPLY )) || { warn "dry-run: test not run (it's a POST; add --apply)"; return 0; }
  jq '{status, elapsedMillis, details}' <<<"$r" >&2
  if [[ "$(jq -r '.status // ""' <<<"$r")" == SUCCESS ]]; then ok "test: connection OK"; return 0; fi
  if grep -q 'configuration already exists' <<<"$r"; then
    die "known Entra connector issue ('Provided source configuration already exists'): on the source's CIEM Settings turn CIEM on, save, turn it off, save — then test again."
  fi
  die "test failed — see references/troubleshooting.md (match the AADSTS / HTTP code in details)"
}

wait_task() {  # id label -> returns non-zero unless SUCCESS/WARNING
  local t s
  for _ in $(seq 1 90); do
    t="$(api GET "$API/task-status/$1")"; s="$(jq -r '.completionStatus // empty' <<<"$t")"
    [[ -n "$s" ]] && break; sleep 10
  done
  [[ -n "$s" ]] || { warn "$2 still running after 15 min (task $1)"; return 1; }
  local msgs; msgs="$(jq -r '[.messages[]? | .localizedText // .message // .key // empty] | select(length > 0) | join(" | ")' <<<"$t" | head -c 1200)"
  case "$s" in
    SUCCESS) ok "$2: $s" ;;
    WARNING) warn "$2: $s ${msgs}" ;;
    *) warn "$2: $s ${msgs}"; return 1 ;;
  esac
}

# Dataset id <- source toggle that enables it (field names and ids verified on a live tenant).
DATASET_TOGGLES='{"azure:foundry":"enableAIFoundryAgent","microsoft:copilot":"enableCopilotAIAgent","microsoft:agent365":"enableMicrosoftAgent365"}'
dataset_ids() {   # source id -> comma list of datasets to aggregate: --datasets, else those switched on on the source
  if [[ -n "$DATASETS" ]]; then echo "$DATASETS"; return; fi
  if (( OFFLINE )); then
    warn "plan-only: which datasets run is read from the source's toggles at run time — pass --datasets ID,ID to show them in the plan"
    return
  fi
  api GET "$API/sources/$1" | jq -r --argjson m "$DATASET_TOGGLES" '
    .connectorAttributes as $a | [$m | to_entries[] | select(($a[.value] | tostring) == "true") | .key] | join(",")'
}

cmd_aggregate() {
  local id rc=0 t; id="$(source_id "$SOURCE")"
  # Entitlements first: account aggregation then links memberships to entitlements that already exist, which
  # is SailPoint's recommended order for a first load. Datasets (machine identities / AI agents) are separate.
  for t in entitlement account dataset; do
    [[ ",$ONLY," == *",$t,"* ]] || continue
    case "$t" in
      entitlement)
        step "Entitlement aggregation ($id)"
        local ent; ent="$(api POST "$API/sources/$id/load-entitlements" "" multipart/form-data | jq -r '.id // .task.id // empty')"
        if (( APPLY )); then ok "entitlement aggregation task ${ent:-?}"; (( WAIT )) && [[ -n "$ent" ]] && { wait_task "$ent" "entitlement aggregation" || rc=1; }; fi ;;
      account)
        step "Account aggregation ($id)"
        if (( FULL )); then delta_off "$id"
        elif (( APPLY )) && [[ "$(api GET "$API/sources/$id" | jq -r '.connectorAttributes.deltaAggregationEnabled // false | tostring')" == true ]]; then
          warn "Delta Aggregation is on: this run only picks up changes. After changing what is aggregated, use --full"
        fi
        local acct; acct="$(api POST "$API/sources/$id/load-accounts" "disableOptimization=true" multipart/form-data | jq -r '.task.id // .id // empty')"
        if (( APPLY )); then ok "account aggregation task ${acct:-?}"; (( WAIT )) && [[ -n "$acct" ]] && { wait_task "$acct" "account aggregation" || rc=1; }; fi
        # Restore only after the task has finished (or immediately with --no-wait, which can't wait for it).
        delta_restore "$id" ;;
      dataset)
        step "Dataset aggregation ($id)"
        local ds; ds="$(dataset_ids "$id")"
        if [[ -z "$ds" ]]; then
          warn "no dataset is switched on for this source — datasets: azure:foundry (enableAIFoundryAgent), microsoft:copilot (enableCopilotAIAgent), microsoft:agent365 (enableMicrosoftAgent365). Enable the matching profile/toggle and its Azure-side setup, or pass --datasets"
          continue
        fi
        local body; body="$(render_tmpl "$ASSETS/isc/aggregate-datasets.tmpl.json" "$(jq -nc --arg d "$ds" '{DATASET_IDS: ($d | split(","))}')")"
        local dt dterr; dterr="$(mktemp)"
        if ! dt="$(api POST "$API/sources/$id/aggregate-agents" "$body" 2>"$dterr" | jq -r '.id // empty')" || [[ -s "$dterr" && $APPLY -eq 1 && -z "$dt" ]]; then
          if grep -q 'endpoint is unavailable' "$dterr"; then
            warn "the dataset aggregation API isn't exposed on this tenant (aggregate-agents -> 404 'endpoint is unavailable'). The UI can still run it (seen live): source -> Dataset Management -> Datasets -> <dataset> -> Dataset Aggregations. Ask SailPoint to enable the API for scripted runs."
          else
            warn "dataset aggregation failed: $(sed 's/\x1b\[[0-9;]*m//g' "$dterr" | head -c 600)"
          fi
          # An API the tenant doesn't expose isn't a broken integration: warn, don't fail the run.
          grep -q 'endpoint is unavailable' "$dterr" || rc=1
          rm -f "$dterr"; continue
        fi
        rm -f "$dterr"
        if (( APPLY )); then ok "dataset aggregation task ${dt:-?} ($ds)"; (( WAIT )) && [[ -n "$dt" ]] && { wait_task "$dt" "dataset aggregation" || rc=1; }
          (( WAIT )) && ok "$(total "$API/machine-identities?filters=$(uri "source.id eq \"$id\"")" -H "X-SailPoint-Experimental: true") machine identit(ies) from this source"; fi ;;
    esac
  done
  if (( APPLY )) && (( WAIT )); then
    ok "source now has $(total "$API/accounts?filters=$(uri "source.id eq \"$id\"")") account(s) and $(total "$API/entitlements?filters=$(uri "source.id eq \"$id\"")") entitlement(s)"
  fi
  (( APPLY )) || warn "dry-run only; rerun with --apply"
  return $rc
}

cmd_verify() {
  # The proof that the integration works, cheapest first: peek reads live data, test validates the whole source
  # configuration, aggregation loads it. Each step only runs if the previous one passed.
  cmd_peek
  cmd_test
  FULL=1   # the proof needs the full set, not just changes since the last delta run
  cmd_aggregate || die "aggregation reported a failure — check the task messages above"
  (( APPLY )) && ok "verified: peek, test and aggregation all succeeded"
  return 0
}

cmd_datasets() {
  local id; id="$(source_id "$SOURCE")"
  (( OFFLINE )) && { warn "plan-only: would GET /v2026/sources/$id/datasets"; return; }
  api GET "$API/sources/$id/datasets" | jq -r '.[] | "  \(.id)\tschedule \(if .aggregationEnabled then "ON" else "off" end)\t\(.name): \([.resources[].name] | join(", "))"'
}

cmd_dataset_schedule() {
  [[ -n "$SCHED_ON" ]] || die "dataset-schedule needs --on or --off"
  local id ds d cur want; id="$(source_id "$SOURCE")"
  ds="$(dataset_ids "$id")"
  [[ -n "$ds" ]] || die "no datasets given and none switched on on the source — pass --datasets ID,ID"
  for d in ${ds//,/ }; do
    step "Dataset schedule $d ($id)"
    if (( OFFLINE )); then cur='{"id":"'"$d"'","aggregationEnabled":false}'; else
      cur="$(api GET "$API/sources/$id/datasets/$d")"; fi
    if [[ "$(jq -r '.aggregationEnabled' <<<"$cur")" == "$SCHED_ON" ]]; then ok "already $( [[ $SCHED_ON == true ]] && echo on || echo off)"; continue; fi
    want="$(jq -c --argjson v "$SCHED_ON" '.aggregationEnabled = $v' <<<"$cur")"
    # Send the object back unchanged except for the flag, exactly like the UI does.
    api PUT "$API/sources/$id/datasets/$d" "$want" >/dev/null
    if (( APPLY )); then ok "scheduled aggregation $( [[ $SCHED_ON == true ]] && echo enabled || echo disabled)"; else warn "dry-run only; rerun with --apply"; fi
  done
}

cmd_schema_spn() {
  local id="${1:-}"; [[ -n "$id" ]] || id="$(source_id "$SOURCE")"
  step "Account schema: service-principal attributes ($id)"
  if (( OFFLINE )); then
    warn "plan-only: would add the missing attributes of assets/isc/account-schema-spn-attributes.json via PATCH $API/sources/$id/schemas/<account schema id>"
    return 0
  fi
  local schemas acct ops
  schemas="$(api GET "$API/sources/$id/schemas")"
  acct="$(jq -c '[.[] | select(.name == "account")][0] // empty' <<<"$schemas")"
  [[ -n "$acct" ]] || die "source $id has no account schema"
  ops="$(jq -c --argjson acct "$acct" --argjson schemas "$schemas" '
          ([$acct.attributes[].name]) as $have
          | [ .attributes[] | select(.name as $n | $have | index($n) | not)
              | . as $a
              | (if $a.schema then ([$schemas[] | select(.name == $a.schema)][0]) else null end) as $s
              | ($a | del(.schema)) + (if $s then {schema: {type: "CONNECTOR_SCHEMA", id: $s.id, name: $s.name}} else {} end)
              | {op: "add", path: "/attributes/-", value: .} ]' "$ASSETS/isc/account-schema-spn-attributes.json")"
  local n; n="$(jq length <<<"$ops")"
  if (( n == 0 )); then ok "all service-principal attributes already present"; return 0; fi
  local nosch; nosch="$(jq -r '[.[] | .value | select(.isEntitlement and (.schema | not) and .name != "spn_userConsentedPermissions") | .name] | join(", ")' <<<"$ops")"
  [[ -z "$nosch" ]] || warn "entitlement schema missing on the source for: $nosch (added without a schema link)"
  echo "  adding $n attribute(s): $(jq -r '[.[].value.name] | join(", ")' <<<"$ops")" >&2
  api PATCH "$API/sources/$id/schemas/$(jq -r .id <<<"$acct")" "$ops" application/json-patch+json >/dev/null
  if (( APPLY )); then ok "account schema updated (+$n)"; else warn "dry-run only; rerun with --apply"; fi
}

cmd_provisioning_policy() {
  [[ -n "$UPN_DOMAIN" ]] || die "provisioning-policy needs --upn-domain (a verified domain of the Entra tenant, e.g. contoso.com)"
  local id body existing; id="$(source_id "$SOURCE")"
  body="$(render_tmpl "$ASSETS/isc/provisioning-policy-create.tmpl.json" \
            "$(jq -nc --arg d "$UPN_DOMAIN" --arg u "$USAGE_LOCATION" '{UPN_DOMAIN: $d, USAGE_LOCATION: $u}')" \
          | jq -c 'with_entries(select(.key | startswith("_") | not))')"
  existing="$(api GET "$API/sources/$id/provisioning-policies" | jq -c '[.[] | select(.usageType == "CREATE")][0] // empty')"
  step "CREATE provisioning policy ($id)"
  if [[ -n "$existing" ]]; then
    if (( ! REPLACE )); then
      ok "the source already has a CREATE policy ($(jq '.fields | length' <<<"$existing") fields) — keeping it; --replace overwrites it with the template"
      return 0
    fi
    api PUT "$API/sources/$id/provisioning-policies/CREATE" "$body" >/dev/null
  else
    api POST "$API/sources/$id/provisioning-policies" "$body" >/dev/null
  fi
  if (( APPLY )); then ok "CREATE policy set (userPrincipalName @$UPN_DOMAIN)"; else warn "dry-run only; rerun with --apply"; fi
}

cmd_correlation() {
  local id cur body; id="$(source_id "$SOURCE")"
  cur="$(api GET "$API/sources/$id/correlation-config")"
  step "Account correlation ($id)"
  [[ "$(jq '.attributeAssignments // [] | length' <<<"$cur")" -gt 0 ]] \
    && echo "  current: $(jq -c '[.attributeAssignments[] | "\(.property) \(.operation) \(.value)"]' <<<"$cur")" >&2
  body="$(render_tmpl "$ASSETS/isc/correlation-config.tmpl.json" \
            "$(jq -nc --arg i "$(jq -r '.id // "<config id>"' <<<"$cur")" --arg n "$(jq -r '.name // "<config name>"' <<<"$cur")" \
                 '{CORRELATION_CONFIG_ID: $i, CORRELATION_CONFIG_NAME: $n}')" \
          | jq -c 'with_entries(select(.key | startswith("_") | not))')"
  local norm='[.attributeAssignments[]? | {property, operation, value, ignoreCase}]'
  if [[ "$(jq -c "$norm" <<<"$cur")" == "$(jq -c "$norm" <<<"$body")" ]]; then ok "already matches the template"; return 0; fi
  api PUT "$API/sources/$id/correlation-config" "$body" >/dev/null
  if (( APPLY )); then ok "correlation config set"; else warn "dry-run only; rerun with --apply"; fi
}

cmd_schedule() {
  local id t type existing; id="$(source_id "$SOURCE")"
  existing="$(api GET "$API/sources/$id/schedules")"
  for t in ${SCHED_TYPES//,/ }; do
    case "$t" in account) type=ACCOUNT_AGGREGATION ;; group) type=GROUP_AGGREGATION ;; *) die "--types: account and/or group. For datasets use: isc-source.sh dataset-schedule --on" ;; esac
    if (( CLEAR )); then
      jq -e --arg ty "$type" 'any(.[]; .type == $ty)' <<<"$existing" >/dev/null || { ok "$type: no schedule"; continue; }
      api DELETE "$API/sources/$id/schedules/$type" >/dev/null; (( APPLY )) && ok "$type: schedule removed"
    elif [[ -n "$CRON" ]]; then
      [[ "$(wc -w <<<"$CRON")" -ge 6 ]] || die "--cron is Quartz cron (6-7 fields, seconds first), e.g. \"0 0 */4 * * ?\""
      if jq -e --arg ty "$type" 'any(.[]; .type == $ty)' <<<"$existing" >/dev/null; then
        api PATCH "$API/sources/$id/schedules/$type" \
          "$(jq -cn --arg c "$CRON" '[{op:"replace", path:"/cronExpression", value:$c}]')" application/json-patch+json >/dev/null
      else
        api POST "$API/sources/$id/schedules" \
          "$(render_tmpl "$ASSETS/isc/schedule.tmpl.json" "$(jq -nc --arg ty "$type" --arg c "$CRON" '{SCHEDULE_TYPE: $ty, CRON: $c}')")" >/dev/null
      fi
      (( APPLY )) && ok "$type: $CRON"
    fi
  done
  (( APPLY )) || [[ -z "$CRON" && $CLEAR -eq 0 ]] || warn "dry-run only; rerun with --apply"
  api GET "$API/sources/$id/schedules" | jq -c '.[] | {type, cronExpression}'
}

# Get the token here, in the main shell: api() runs inside $(...) subshells, so a token fetched there is lost.
SUBCOMMANDS='^(discover|create|configure|show|verify|peek|test|aggregate|datasets|dataset-schedule|schema-spn|provisioning-policy|correlation|schedule)$'
[[ "$CMD" =~ $SUBCOMMANDS ]] && token

case "$CMD" in
  discover) cmd_discover ;;
  create) cmd_create ;;
  configure) cmd_configure ;;
  show) cmd_show ;;
  verify) cmd_verify ;;
  peek) cmd_peek ;;
  test) cmd_test ;;
  aggregate) cmd_aggregate ;;
  datasets) cmd_datasets ;;
  dataset-schedule) cmd_dataset_schedule ;;
  schema-spn) cmd_schema_spn ;;
  provisioning-policy) cmd_provisioning_policy ;;
  correlation) cmd_correlation ;;
  schedule) cmd_schedule ;;
  -h|--help|help) usage ;;
  *) usage; die "unknown subcommand: $CMD" ;;
esac
