#!/usr/bin/env bash
# COMMAND 2 — ISC side: create and configure an "AWS SaaS" source through the ISC REST API.
#
# Verified against a live tenant (2026-10): connector name "AWS SaaS", scriptName "awssaas"; source-config form is
# XML with <Field name="…"> = connectorAttributes keys (roleName, region, externalId, managementAccountId,
# assumeRoleSessionName, cloudScope, …). The External ID is tenant-wide (GET /beta/tenant), not per source.
# `discover` still reads the form so a connector change shows up instead of silently sending wrong keys.
#
# Env: ISC_TENANT = full API host (acme.api.identitynow.com, acme-demo.api.identitynow-demo.com) or ISC_BASE_URL,
#      ISC_CLIENT_ID, ISC_CLIENT_SECRET (PAT).
# Read-only calls always run; writes are dry-run unless --apply is given.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

usage() {
  cat <<'EOF'
Usage: isc-source.sh <subcommand> [options] [--apply]

  external-id                         Print the tenant's External ID (what the AWS role must trust).
  discover  [--connector SCRIPT] [--from-source ID|NAME]
            Find the AWS SaaS connector and build the attribute key map.
  create    --name NAME --owner ALIAS|EMAIL|ID|me [--description TEXT] [--reuse]
            Create the source. An existing source with that name is reused only if you own it
            (or with --reuse); the tenant may be shared.
  configure --source ID|NAME --role-name NAME --mgmt-account-id ID [--region R]
            [--session-name NAME] [--accounts ID,ID,...] [--change-password-policy-arn ARN]
            [--bedrock-regions R,R | --no-bedrock] [--agentcore-regions R,R | --no-agentcore]
            Bedrock/AgentCore options turn on agent discovery (Machine Identity Governance) in those regions;
            the AWS role needs aws-setup.sh --bedrock / --agentcore.
            Set connection settings; --accounts sets the "AWS Accounts" selection (cloudScope).
  show      --source ID|NAME          Print the source (secrets redacted).
  test      --source ID|NAME          Run "Test Connection".
  peek      --source ID|NAME          Read a few accounts through the connector — the real end-to-end check
                                      (Test Connection is broken for AWS SaaS on some tenants; see troubleshooting).
  aggregate --source ID|NAME [--entitlements] [--no-wait]
            Run account (and entitlement) aggregation and wait for the result.
  schedule  --source ID|NAME [--cron "0 */5 * * * ?"] [--types account,group] [--clear]
            Show, set (create or update) or remove scheduled aggregations (Quartz cron, 6-7 fields).

Common: --keymap FILE (override the cached key map), --apply (perform writes; default is dry-run)
EOF
}

(($#)) || { usage; exit 1; }
CMD="$1"; shift
CONNECTOR="" FROM_SOURCE="" NAME="" OWNER="" DESCRIPTION="AWS SaaS source — managed via sailpoint-isc-aws-connector skill"
SOURCE="" ROLE_NAME="" MGMT_ID="" REGION="us-east-1" SESSION_NAME="SailPointISC" ACCOUNTS="" CHPW_ARN="" WAIT=1 CRON="" SCHED_TYPES="account,group" CLEAR=0 BEDROCK_REGIONS="" AGENTCORE_REGIONS="" BEDROCK="" AGENTCORE="" ENTITLEMENTS=0
KEYMAP="" REUSE=0
while (($#)); do
  case "$1" in
    --connector) CONNECTOR="$2"; shift ;;
    --from-source) FROM_SOURCE="$2"; shift ;;
    --name) NAME="$2"; shift ;;
    --owner) OWNER="$2"; shift ;;
    --description) DESCRIPTION="$2"; shift ;;
    --reuse) REUSE=1 ;;
    --source) SOURCE="$2"; shift ;;
    --role-name) ROLE_NAME="$2"; shift ;;
    --mgmt-account-id) MGMT_ID="$2"; shift ;;
    --region) REGION="$2"; shift ;;
    --session-name) SESSION_NAME="$2"; shift ;;
    --accounts) ACCOUNTS="$2"; shift ;;
    --change-password-policy-arn) CHPW_ARN="$2"; shift ;;
    --bedrock-regions) BEDROCK=true BEDROCK_REGIONS="$2"; shift ;;
    --agentcore-regions) AGENTCORE=true AGENTCORE_REGIONS="$2"; shift ;;
    --no-bedrock) BEDROCK=false ;;
    --no-agentcore) AGENTCORE=false ;;
    --manage-all) die "this connector has no 'manage all accounts' setting; pass --accounts ID,ID,... (or pick accounts in the UI)" ;;
    --entitlements) ENTITLEMENTS=1 ;;
    --no-wait) WAIT=0 ;;
    --cron) CRON="$2"; shift ;;
    --types) SCHED_TYPES="$2"; shift ;;
    --clear) CLEAR=1 ;;
    --keymap) KEYMAP="$2"; shift ;;
    --apply) APPLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "unknown option: $1" ;;
  esac
  shift
done

need_cmd curl jq
if [[ -z "${ISC_BASE_URL:-}" ]]; then
  [[ -n "${ISC_TENANT:-}" ]] || die "set ISC_TENANT to the tenant's full API host (e.g. acme.api.identitynow.com)"
  ISC_BASE_URL="$(isc_base_url "$ISC_TENANT")"
fi
ISC_BASE_URL="${ISC_BASE_URL%/}"
TENANT_TAG="$(sed -E 's#^https?://##; s#[^A-Za-z0-9.-]#_#g' <<<"$ISC_BASE_URL")"
KEYMAP="${KEYMAP:-${XDG_CACHE_HOME:-$HOME/.cache}/sailpoint-isc-aws/${TENANT_TAG}-keymap.json}"

# --- HTTP ------------------------------------------------------------------------------------------
TOKEN="" TOKEN_IDENTITY=""
token() {
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

# api METHOD PATH [BODY] [CONTENT_TYPE] — prints the response body. Writes are skipped in dry-run.
api() {
  local method="$1" path="$2" body="${3:-}" ctype="${4:-application/json}"
  if [[ "$method" != GET ]] && (( ! APPLY )); then
    printf '\033[2m[dry-run]\033[0m %s %s\n' "$method" "$ISC_BASE_URL$path" >&2
    [[ -n "$body" ]] && { jq . <<<"$body" 2>/dev/null || printf '%s\n' "$body"; } | redact >&2
    echo '{}'; return
  fi
  token
  # v2025 endpoints (e.g. schedules) are flagged experimental and need this header; other versions ignore it.
  local args=(-sS -X "$method" -H "Authorization: Bearer $TOKEN" -H "Accept: application/json, */*"
              -H "X-SailPoint-Experimental: true" -w '\n%{http_code}')
  [[ -n "$body" ]] && args+=(-H "Content-Type: $ctype" --data-binary "$body")
  local out code
  out="$(curl "${args[@]}" "$ISC_BASE_URL$path")" || die "$method $path: request failed"
  code="${out##*$'\n'}"; out="${out%$'\n'*}"
  [[ "$code" =~ ^2 ]] || die "$method $path -> HTTP $code: $(redact <<<"$out" | head -c 1500)"
  printf '%s\n' "$out"
}
uri() { jq -rn --arg s "$1" '$s|@uri'; }

# --- Helpers ---------------------------------------------------------------------------------------
load_keymap() {
  [[ -f "$KEYMAP" ]] || die "no key map at $KEYMAP — run: isc-source.sh discover"
  jq -e .keys "$KEYMAP" >/dev/null || die "key map $KEYMAP has no .keys"
}

source_id() {   # accept an id or a name
  local s="${1:?--source is required}"
  if [[ "$s" =~ ^[0-9a-f]{32}$ ]]; then echo "$s"; return; fi
  local id
  id="$(api GET "/v3/sources?filters=$(uri "name eq \"$s\"")" | jq -r '.[0].id // empty')"
  [[ -n "$id" ]] || die "no source named '$s'"
  echo "$id"
}

tenant_external_id() {
  api GET /beta/tenant | jq -r '[.products[]? | select(.productName=="idn") | .attributes.externalId // empty][0] // empty'
}

# Map form field names to the settings this skill sets. Exact names seen on a live tenant come first;
# the looser patterns are a fallback in case SailPoint renames fields. Edit the file if a mapping is wrong.
build_keymap() {  # stdin: JSON array of field names
  jq --arg script "$1" --arg via "$2" '
    def pick(f): (map(select(f)) | .[0]) // null;
    unique as $f | {
      connector: $script, discoveredVia: $via, allFields: $f,
      keys: {
        roleName:            ($f|pick(. == "roleName") // pick(test("role.?name";"i") and (test("session";"i")|not))),
        region:              ($f|pick(. == "region") // pick(test("region";"i") and (test("regions$";"i")|not))),
        externalId:          ($f|pick(test("external.?id";"i"))),
        managementAccountId: ($f|pick(test("(management|master|mgmt).*account";"i"))),
        roleSessionName:     ($f|pick(test("session.?name";"i"))),
        changePasswordPolicyArn: ($f|pick(test("change.?password.?policy";"i"))),
        bedrockEnabled:      ($f|pick(. == "enableDiscoverBedrockAgent")),
        bedrockRegions:      ($f|pick(. == "AgentAwsRegion")),
        agentCoreEnabled:    ($f|pick(. == "enableDiscoverBedrockAgentCore")),
        agentCoreRegions:    ($f|pick(. == "AgentCoreAwsRegion")),
        accounts:            ($f|pick(. == "cloudScope") // pick(test("^(aws)?accounts?(ids?|list)?$";"i")))
      }
    }'
}

# --- Subcommands -------------------------------------------------------------------------------------
cmd_external_id() {
  local ext; ext="$(tenant_external_id)"
  [[ -n "$ext" ]] || die "no externalId on GET /beta/tenant; copy it from a source's Connection Settings page in the UI"
  echo "$ext"
}

cmd_discover() {
  step "Finding the AWS SaaS connector"
  if [[ -z "$CONNECTOR" ]]; then
    local list
    # The display name is "AWS SaaS" on current tenants ("Amazon Web Services SaaS" in the docs) — search both.
    list="$( { api GET "/v3/connectors?filters=$(uri 'name co "AWS"')&limit=250"
               api GET "/v3/connectors?filters=$(uri 'name co "Amazon"')&limit=250"; } | jq -s 'add | unique_by(.scriptName)')"
    jq -r '.[] | "  \(.name)\t\(.scriptName)"' <<<"$list" >&2
    CONNECTOR="$(jq -r '( [.[] | select(.scriptName=="awssaas")]
                        + [.[] | select((.name|test("SaaS";"i")) and (.name|test("Amazon|AWS";"i"))
                                        and (.name|test("CIEM|Secrets|Identity Center|SSO";"i")|not))] )[0].scriptName // empty' <<<"$list")"
    [[ -n "$CONNECTOR" ]] || die "couldn't pick the AWS SaaS connector from the list above; rerun with --connector <scriptName>"
  fi
  ok "connector scriptName: $CONNECTOR"
  # For SaaS connectors .type is the connector spec id; create must send it or no connector instance is made.
  local ctype; ctype="$(api GET "/v3/connectors/$(uri "$CONNECTOR")" | jq -r '.type // empty')"

  local fields via
  if [[ -n "$FROM_SOURCE" ]]; then
    via="source $FROM_SOURCE"
    fields="$(api GET "/v3/sources/$(source_id "$FROM_SOURCE")" | jq '.connectorAttributes | keys')"
  else
    via="source-config form"
    local form
    form="$(api GET "/v3/connectors/$(uri "$CONNECTOR")/source-config")"
    # XML form: each <Field name="…"> is a connectorAttributes key (Menu/Section names are not).
    fields="$(grep -oE '<Field[^>]*[[:space:]]name="[A-Za-z0-9_.-]+"' <<<"$form" \
              | sed -E 's/.*[[:space:]]name="([^"]+)"$/\1/' | sort -u | jq -R . | jq -s .)"
    if [[ "$(jq length <<<"$fields")" -eq 0 ]]; then   # JSON-shaped form fallback
      fields="$(jq '[.. | objects | (.key? // .name?) | strings] | unique' <<<"$form" 2>/dev/null || echo '[]')"
    fi
  fi
  [[ "$(jq length <<<"$fields")" -gt 0 ]] || die "no field names found via $via. Create the source in the UI, then: discover --from-source <name>"

  mkdir -p "$(dirname "$KEYMAP")"
  build_keymap "$CONNECTOR" "$via" <<<"$fields" | jq --arg t "$ctype" '.connectorType = $t' >"$KEYMAP"
  ok "key map written to $KEYMAP"
  jq .keys "$KEYMAP"
  local missing
  missing="$(jq -r '.keys | to_entries[] | select(.value==null) | .key' "$KEYMAP")"
  [[ -z "$missing" ]] || warn "unmapped: $(tr '\n' ' ' <<<"$missing")— pick the right field from .allFields in $KEYMAP and fill it in"
}

cmd_create() {
  [[ -n "$NAME" && -n "$OWNER" ]] || die "create needs --name and --owner"
  load_keymap
  step "Source '$NAME'"
  local existing id
  existing="$(api GET "/v3/sources?filters=$(uri "name eq \"$NAME\"")")"
  id="$(jq -r '.[0].id // empty' <<<"$existing")"
  if [[ -n "$id" ]]; then
    local owner_id owner_name
    owner_id="$(jq -r '.[0].owner.id // empty' <<<"$existing")"; owner_name="$(jq -r '.[0].owner.name // "?"' <<<"$existing")"
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
      owner_id="$(api GET "/v3/public-identities?filters=$(uri "$field eq \"$OWNER\"")" | jq -r '.[0].id // empty')"
      [[ -n "$owner_id" ]] || die "no identity with $field '$OWNER'"
      ok "owner $OWNER -> $owner_id"
    fi
    # SaaS connectors need spConnectorSpecId + idnProxyType at creation: ISC then creates and links the connector
    # instance (spConnectorInstanceId). Without them test-connection dies with a NullPointerException, and these
    # attributes are stripped from later PATCHes, so they can't be added afterwards.
    local body spec ext
    spec="$(jq -r '.connectorType // empty' "$KEYMAP")"
    [[ -n "$spec" ]] || die "key map has no connectorType — rerun: isc-source.sh discover"
    ext="$(tenant_external_id)"
    body="$(jq -n --arg n "$NAME" --arg d "$DESCRIPTION" --arg o "$owner_id" --arg c "$(jq -r .connector "$KEYMAP")" \
                  --arg spec "$spec" --arg ext "$ext" --arg extKey "$(jq -r '.keys.externalId // "externalId"' "$KEYMAP")" '
            {name:$n, description:$d, owner:{type:"IDENTITY", id:$o}, connector:$c,
             connectorAttributes: ({spConnectorSpecId:$spec, idnProxyType:"sp-connect", connectionType:"direct",
                                    # Defaults the UI wizard sets. spConnectorSupportsCustomSchemas in particular:
                                    # without it ISC does not pass the source schemas to the connector, and peek
                                    # and aggregation fail with: no schema provided for account list.
                                    spConnectorSupportsCustomSchemas:true, templateApplication:"AWS SaaS",
                                    EnableAccessKeys:true, EnableHTTPSCredentials:true, EnableSSHKeys:true,
                                    healthCheckTimeout:60, assumeRoleDurationInSeconds:3600, pageSize:10}
                                   + (if $ext != "" then {($extKey):$ext} else {} end))}')"
    id="$(api POST /v3/sources "$body" | jq -r '.id // empty')"
    if (( ! APPLY )); then
      warn "dry-run: source not created"
      jq -n --arg name "$NAME" --arg ext "$(tenant_external_id)" '{sourceId:null, name:$name, externalId:$ext}'
      return
    fi
    [[ -n "$id" ]] || die "create returned no id"
    ok "created source $id"
    local inst; inst="$(api GET "/v3/sources/$id" | jq -r '.connectorAttributes.spConnectorInstanceId // empty')"
    [[ -n "$inst" ]] && ok "connector instance $inst" \
      || warn "no spConnectorInstanceId on the new source — test-connection will fail; see references/troubleshooting.md"
  fi
  local ext; ext="$(api GET "/v3/sources/$id" | jq -r '.connectorAttributes.externalId // empty')"
  [[ -n "$ext" ]] || ext="$(tenant_external_id)"
  ok "External ID: $ext"
  jq -n --arg id "$id" --arg name "$NAME" --arg ext "$ext" '{sourceId:$id, name:$name, externalId:$ext}'
}

cmd_configure() {
  [[ -n "$ROLE_NAME" && -n "$MGMT_ID" ]] || die "configure needs --role-name and --mgmt-account-id"
  [[ "$ROLE_NAME" != arn:* && "$ROLE_NAME" != */* ]] \
    || die "--role-name takes the role NAME (e.g. SailPointAWSRole), not an ARN — ISC fails with 'AWS Client creation failed' otherwise"
  is_account_id "$MGMT_ID" || die "--mgmt-account-id must be 12 digits"
  if [[ -n "$ACCOUNTS" ]]; then
    IFS=, read -ra _ids <<<"$ACCOUNTS"
    for a in "${_ids[@]}"; do is_account_id "$a" || die "bad account id in --accounts: $a"; done
  fi
  for r in ${BEDROCK_REGIONS//,/ } ${AGENTCORE_REGIONS//,/ }; do
    [[ "$r" =~ ^[a-z]{2}(-gov)?-[a-z]+-[0-9]$ ]] || die "not an AWS region code: $r"
  done
  load_keymap
  local id; id="$(source_id "$SOURCE")"
  # The form marks "Change Password Policy ARN" required (the UI won't save without it). Default to AWS's managed
  # IAMUserChangePassword policy in the role's partition: it lets IAM users change only their own password.
  if [[ -z "$CHPW_ARN" ]]; then
    local part=aws; [[ "$REGION" == us-gov-* ]] && part=aws-us-gov
    CHPW_ARN="arn:$part:iam::aws:policy/IAMUserChangePassword"
  fi

  local vals ops
  vals="$(jq -n --arg role "$ROLE_NAME" --arg mgmt "$MGMT_ID" --arg region "$REGION" --arg sess "$SESSION_NAME" \
            --arg accts "$ACCOUNTS" --arg chpw "$CHPW_ARN" --arg ext "$(tenant_external_id)" \
            --arg bedrock "$BEDROCK" --arg bregions "$BEDROCK_REGIONS" \
            --arg agentcore "$AGENTCORE" --arg acregions "$AGENTCORE_REGIONS" '
          {roleName:$role, managementAccountId:$mgmt, region:$region, roleSessionName:$sess, changePasswordPolicyArn:$chpw}
          + (if $ext != "" then {externalId:$ext} else {} end)
          + (if $accts != "" then {accounts: ($accts|split(","))} else {} end)
          + (if $bedrock != "" then {bedrockEnabled: ($bedrock=="true")} else {} end)
          + (if $bregions != "" then {bedrockRegions: ($bregions|split(","))} else {} end)
          + (if $agentcore != "" then {agentCoreEnabled: ($agentcore=="true")} else {} end)
          + (if $acregions != "" then {agentCoreRegions: ($acregions|split(","))} else {} end)')"
  ops="$(jq --slurpfile km "$KEYMAP" '
          to_entries | map(. as $e | ($km[0].keys[$e.key]) as $k
            | if $k == null then error("no ISC field mapped for \($e.key); edit the key map") else
              {op:"add", path:"/connectorAttributes/\($k)", value:$e.value} end)' <<<"$vals")" \
    || die "key map incomplete — run discover or edit $KEYMAP"
  step "Configuring source $id"
  api PATCH "/v3/sources/$id" "$ops" application/json-patch+json >/dev/null
  if (( APPLY )); then ok "source updated"; else warn "dry-run only; rerun with --apply"; fi
  [[ -n "$ACCOUNTS" ]] || warn "no --accounts given: AWS Accounts (cloudScope) is left as is — after a successful test, pick accounts in the UI or rerun with --accounts"
}

cmd_show() { api GET "/v3/sources/$(source_id "$SOURCE")" | redact | jq .; }

cmd_test() {
  local id; id="$(source_id "$SOURCE")"
  step "Test connection ($id)"
  local r; r="$(api POST "/beta/sources/$id/connector/test-configuration")"
  (( APPLY )) || { warn "dry-run: test not run (it's a POST; add --apply)"; return; }
  jq '{status, elapsedMillis, details}' <<<"$r"
  if [[ "$(jq -r '.status // ""' <<<"$r")" =~ SUCCESS ]]; then ok "connection OK"; return; fi
  if grep -q 'req.input' <<<"$r"; then
    die "test fails like this until the source's first aggregation; it says nothing about your config.
   Run: isc-source.sh peek --source \"$SOURCE\" --apply, then aggregate, then test again."
  fi
  die "test failed — see references/troubleshooting.md"
}

cmd_peek() {
  local id; id="$(source_id "$SOURCE")"
  step "Peek accounts ($id)"
  local r; r="$(api POST "/beta/sources/$id/connector/peek-resource-objects" '{"objectType":"account","maxCount":5}')"
  (( APPLY )) || { warn "dry-run: peek not run (it's a POST, but read-only; add --apply)"; return; }
  local n; n="$(jq '.resourceObjects // [] | length' <<<"$r")"
  jq -r '.resourceObjects // [] | .[] | "  " + (.identity // .name)' <<<"$r"
  (( n > 0 )) || die "connector returned no accounts: $(jq -c '.details // .' <<<"$r" | head -c 800)"
  ok "connector read $n account(s) from AWS — the connection works"
}

wait_task() {  # id label
  local t s
  for _ in $(seq 1 60); do
    t="$(api GET "/beta/task-status/$1")"; s="$(jq -r '.completionStatus // empty' <<<"$t")"
    [[ -n "$s" ]] && break; sleep 10
  done
  [[ -n "$s" ]] || { warn "$2 still running after 10 min (task $1)"; return; }
  [[ "$s" == SUCCESS ]] && ok "$2: $s" || warn "$2: $s $(jq -c '[.messages[]? | .localizedText // .key]' <<<"$t")"
}

cmd_aggregate() {
  local id; id="$(source_id "$SOURCE")"
  step "Aggregation ($id)"
  local acct ent=""
  acct="$(api POST "/beta/sources/$id/load-accounts" "disableOptimization=true" application/x-www-form-urlencoded | jq -r '.task.id // empty')"
  (( ENTITLEMENTS )) && ent="$(api POST "/beta/sources/$id/load-entitlements" "" | jq -r '.task.id // .id // empty')"
  (( APPLY )) || return 0
  ok "account aggregation task ${acct:-?}${ent:+, entitlement aggregation task $ent}"
  (( WAIT )) || return 0
  [[ -n "$acct" ]] && wait_task "$acct" "account aggregation"
  [[ -n "$ent" ]] && wait_task "$ent" "entitlement aggregation"
  local n; n="$(api GET "/v3/accounts?filters=$(uri "sourceId eq \"$id\"")&limit=250" | jq length)"
  ok "$n account(s) on the source"
}

cmd_schedule() {
  local id t type existing; id="$(source_id "$SOURCE")"
  existing="$(api GET "/v2025/sources/$id/schedules")"
  for t in ${SCHED_TYPES//,/ }; do
    case "$t" in account) type=ACCOUNT_AGGREGATION ;; group) type=GROUP_AGGREGATION ;; *) die "--types: account and/or group" ;; esac
    if (( CLEAR )); then
      jq -e --arg ty "$type" 'any(.[]; .type == $ty)' <<<"$existing" >/dev/null || { ok "$type: no schedule"; continue; }
      api DELETE "/v2025/sources/$id/schedules/$type" >/dev/null; (( APPLY )) && ok "$type: schedule removed"
    elif [[ -n "$CRON" ]]; then
      [[ "$(wc -w <<<"$CRON")" -ge 6 ]] || die "--cron is Quartz cron (6-7 fields, seconds first), e.g. \"0 */5 * * * ?\""
      if jq -e --arg ty "$type" 'any(.[]; .type == $ty)' <<<"$existing" >/dev/null; then
        api PATCH "/v2025/sources/$id/schedules/$type" \
          "$(jq -cn --arg c "$CRON" '[{op:"replace", path:"/cronExpression", value:$c}]')" application/json-patch+json >/dev/null
      else
        api POST "/v2025/sources/$id/schedules" "$(jq -cn --arg ty "$type" --arg c "$CRON" '{type:$ty, cronExpression:$c}')" >/dev/null
      fi
      (( APPLY )) && ok "$type: $CRON"
    fi
  done
  (( APPLY )) || [[ -z "$CRON" && $CLEAR -eq 0 ]] || warn "dry-run only; rerun with --apply"
  api GET "/v2025/sources/$id/schedules" | jq -c '.[] | {type, cronExpression}'
}

# Get the token here, in the main shell: api() runs inside $(...) subshells, so a token fetched there is lost.
[[ "$CMD" =~ ^(external-id|discover|create|configure|show|test|peek|aggregate|schedule)$ ]] && token

case "$CMD" in
  external-id) cmd_external_id ;;
  discover) cmd_discover ;;
  create) cmd_create ;;
  configure) cmd_configure ;;
  show) cmd_show ;;
  test) cmd_test ;;
  peek) cmd_peek ;;
  aggregate) cmd_aggregate ;;
  schedule) cmd_schedule ;;
  -h|--help|help) usage ;;
  *) usage; die "unknown subcommand: $CMD" ;;
esac
