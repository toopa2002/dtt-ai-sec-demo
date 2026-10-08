#!/usr/bin/env bash
# COMMAND 1 — Entra side: everything the SailPoint ISC "Microsoft Entra SaaS" connector needs in the tenant, built
# with the Azure CLI from the templates in assets/entra/:
#   app registration (requiredResourceAccess from the selected permission profiles) -> service principal ->
#   admin consent (app role assignments / OAuth2 grants on the resource service principals) -> client secret
#   (written to a chmod 600 file, never printed) -> directory roles -> optional Exchange cert, Azure RBAC for Foundry.
#
# Permission GUIDs are resolved by NAME from the resource service principals at run time, never hard-coded.
# Consent is granted with explicit Graph calls instead of `az ad app permission admin-consent`, so each grant is
# visible in the dry-run, idempotent on rerun, and retried while a new service principal propagates.
#
# Needs: az (logged in to the target tenant: az login --tenant <tenant> --allow-no-subscriptions), jq; openssl for
# --exchange-cert. Read-only lookups always run; writes are dry-run unless --apply. --plan-only needs no az login.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

GRAPH=https://graph.microsoft.com/v1.0
TAG=sailpoint-isc-entra-connector
SECRET_NAME="SailPoint ISC"

usage() {
  cat <<'EOF'
Usage: entra-setup.sh [options] [--apply]

  --app-name NAME            App registration name (default "SailPoint ISC Entra Connector")
  --app-id APPID             Use this existing app registration (Application/client ID) instead of finding one by
                             name; implies --reuse, so its existing API permissions are kept and only added to
  --profiles P,P             readonly (always), provisioning, machine-identity, ai-agents, exchange, teams, pim
  --tenant ID|DOMAIN         Refuse to run unless az is logged in to this tenant
  --secret-years N           Client secret lifetime (default 1)
  --rotate-secret            Add a new secret even if the secret file already exists
  --no-secret                Don't create a secret (e.g. you'll use certificate auth)
  --secret-dir DIR           Where secret/cert files go (default ~/.config/sailpoint-entra, chmod 700)
  --manage-admin-users       Also assign Privileged Authentication Administrator (ISC may manage admin users)
  --skip-roles               Don't assign directory roles
  --exchange-cert            Generate a self-signed cert for Exchange Online app-only auth and add it to the app
  --foundry-subscriptions S,S  Subscriptions to grant azure-rbac.json roles on (ai-agents profile)
  --reuse                    Adopt an existing same-named app registration that this skill didn't create
  --out FILE                 Setup summary for isc-source.sh configure --from-setup (default ./sailpoint-entra-setup.json)
  --plan-only                No Azure calls at all: print the plan with <placeholder> ids
  --apply                    Perform writes (default: dry-run)
EOF
}

APP_NAME="SailPoint ISC Entra Connector" PROFILES="" EXPECT_TENANT="" SECRET_YEARS=1 ROTATE=0 NO_SECRET=0
SECRET_DIR="${SAILPOINT_ENTRA_SECRET_DIR:-$HOME/.config/sailpoint-entra}" ADMIN_USERS=0 SKIP_ROLES=0 EXO_CERT=0
FOUNDRY_SUBS="" REUSE=0 FOREIGN=0 WANT_APP_ID="" OUT=./sailpoint-entra-setup.json OFFLINE=0
while (($#)); do
  case "$1" in
    --app-name) APP_NAME="$2"; shift ;;
    --app-id) WANT_APP_ID="$2"; REUSE=1; shift ;;
    --profiles) PROFILES="$2"; shift ;;
    --tenant) EXPECT_TENANT="$2"; shift ;;
    --secret-years) SECRET_YEARS="$2"; shift ;;
    --rotate-secret) ROTATE=1 ;;
    --no-secret) NO_SECRET=1 ;;
    --secret-dir) SECRET_DIR="$2"; shift ;;
    --manage-admin-users) ADMIN_USERS=1 ;;
    --skip-roles) SKIP_ROLES=1 ;;
    --exchange-cert) EXO_CERT=1 ;;
    --foundry-subscriptions) FOUNDRY_SUBS="$2"; shift ;;
    --reuse) REUSE=1 ;;
    --out) OUT="$2"; shift ;;
    --plan-only) OFFLINE=1 ;;
    --apply) APPLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "unknown option: $1" ;;
  esac
  shift
done
(( OFFLINE && APPLY )) && die "--plan-only and --apply don't mix"
[[ "$SECRET_YEARS" =~ ^[1-9][0-9]?$ ]] || die "--secret-years must be a whole number of years"
[[ -z "$WANT_APP_ID" ]] || is_guid "$WANT_APP_ID" || die "--app-id must be the Application (client) ID GUID"
need_cmd jq
(( OFFLINE )) || need_cmd az

# --- Profiles ----------------------------------------------------------------------------------------
SEL=()
IFS=, read -ra _p <<<"readonly,${PROFILES}"
for p in "${_p[@]}"; do
  [[ -n "$p" ]] || continue
  [[ -f "$ASSETS/entra/permissions/$p.json" ]] \
    || die "unknown profile '$p' (choose from: $(cd "$ASSETS/entra/permissions" && ls ./*.json | sed 's#./##; s/\.json$//' | tr '\n' ' '))"
  [[ " ${SEL[*]-} " == *" $p "* ]] || SEL+=("$p")
done
has_profile() { [[ " ${SEL[*]} " == *" $1 "* ]]; }
SEL_JSON="$(printf '%s\n' "${SEL[@]}" | jq -R . | jq -sc .)"

# Merge the selected profiles into one list per resource API: [{api, appId, appRoles:[names], scopes:[names]}]
WANT="$(for p in "${SEL[@]}"; do cat "$ASSETS/entra/permissions/$p.json"; done | jq -sc '
  [ .[] | .resources[] | {api, appId, appRoles: [.appRoles[]?.name], scopes: [.delegated[]?.name]} ]
  | group_by(.appId)
  | map({api: .[0].api, appId: .[0].appId, appRoles: ([.[].appRoles[]] | unique), scopes: ([.[].scopes[]] | unique)})')"
ROLES="$(jq -c --argjson sel "$SEL_JSON" --argjson admin "$ADMIN_USERS" '
  [ .roles[] | select( any((.profiles // [])[]; . as $p | $sel | index($p) != null)
                       or ($admin == 1 and .optIn == "--manage-admin-users") ) | .name ]' "$ASSETS/entra/directory-roles.json")"
(( SKIP_ROLES )) && ROLES='[]'

# --- Azure CLI wrappers ------------------------------------------------------------------------------
TMPD="$(mktemp -d)"; trap 'rm -rf "$TMPD"' EXIT

graph_get() {   # URL -> JSON (read-only, always runs unless --plan-only)
  if (( OFFLINE )); then echo '{"value":[]}'; return; fi
  az rest --method GET --url "$1" -o json
}
# graph_try METHOD URL BODY — prints the response; returns non-zero on failure (message on stderr).
graph_try() {
  local method="$1" url="$2" body="${3:-}"
  if (( ! APPLY )); then
    printf '\033[2m[dry-run]\033[0m az rest --method %s --url %s%s\n' "$method" "$url" "${body:+ --body @-}" >&2
    if (( ${#body} > 300 )); then redact_json <<<"$body" | sed 's/^/    /' >&2
    elif [[ -n "$body" ]]; then printf '    %s\n' "$(redact_json <<<"$body" | jq -c .)" >&2; fi
    echo '{}'; return
  fi
  local f="$TMPD/body.json"; [[ -n "$body" ]] || body='{}'; printf '%s' "$body" >"$f"
  az rest --method "$method" --url "$url" --headers Content-Type=application/json --body "@$f" -o json
}
graph_write() { graph_try "$@" || die "$1 $2 failed"; }
# A new service principal takes a little while to be visible to every Graph replica; grants against it can
# fail with 400/404 for a minute. Retry those rather than fail the whole run.
graph_retry() {
  local i out
  (( APPLY )) || { graph_try "$@"; return; }
  for i in 1 2 3 4 5 6; do
    if out="$(graph_try "$@" 2>"$TMPD/err")"; then printf '%s\n' "$out"; return; fi
    grep -qiE 'already exists|Permission being assigned already exists' "$TMPD/err" && { echo '{}'; return; }
    grep -qiE 'Authorization_RequestDenied|Insufficient privileges' "$TMPD/err" \
      && die "$1 $2: $(head -c 400 "$TMPD/err")
   The signed-in account can't grant this. Run as Global Administrator or Privileged Role Administrator (activate the role first if it's PIM-eligible)."
    warn "attempt $i failed ($(head -c 160 "$TMPD/err" | tr '\n' ' ')); retrying in 10s"; sleep 10
  done
  die "$1 $2 kept failing: $(head -c 600 "$TMPD/err")"
}
az_run() {   # mutating az command: run under --apply, print otherwise
  if (( APPLY )); then "$@"; else printf '\033[2m[dry-run]\033[0m %s\n' "$(printf '%q ' "$@")" >&2; fi
}
enc() { jq -rn --arg s "$1" '$s|@uri'; }

# --- 1. Preflight (read-only) -------------------------------------------------------------------------
step "Preflight"
TENANT_ID="<tenant-id>" DOMAIN="<tenant>.onmicrosoft.com" APP_ID="" APP_OID="" SP_OID=""
if (( OFFLINE )); then
  [[ -n "$EXPECT_TENANT" ]] && { if is_guid "$EXPECT_TENANT"; then TENANT_ID="$EXPECT_TENANT"; else DOMAIN="$EXPECT_TENANT"; fi; }
  warn "plan-only: no Azure calls; ids are shown as <placeholders>"
  [[ -n "$WANT_APP_ID" ]] && { APP_ID="$WANT_APP_ID" APP_OID="<object id of app $WANT_APP_ID>" FOREIGN=1; }
else
  acct="$(az account show -o json 2>/dev/null)" \
    || die "az is not logged in. Run: az login --tenant <tenant-id-or-domain> --allow-no-subscriptions"
  TENANT_ID="$(jq -r .tenantId <<<"$acct")"
  ok "az signed in as $(jq -r .user.name <<<"$acct") ($(jq -r .user.type <<<"$acct")), tenant $TENANT_ID"
  org="$(graph_get "$GRAPH/organization?\$select=id,displayName,verifiedDomains")"
  DOMAIN="$(jq -r '[.value[0].verifiedDomains[] | select(.isInitial) | .name][0] // empty' <<<"$org")"
  [[ -n "$DOMAIN" ]] || die "couldn't read the tenant's initial domain from /organization"
  ok "tenant '$(jq -r '.value[0].displayName' <<<"$org")', initial domain $DOMAIN (use this as the ISC Domain Name)"
  if [[ -n "$EXPECT_TENANT" ]] && [[ "$EXPECT_TENANT" != "$TENANT_ID" ]] \
     && ! jq -e --arg d "$EXPECT_TENANT" 'any(.value[0].verifiedDomains[]; (.name|ascii_downcase) == ($d|ascii_downcase))' <<<"$org" >/dev/null; then
    die "az is logged in to tenant $TENANT_ID ($DOMAIN), not '$EXPECT_TENANT'. Run: az login --tenant $EXPECT_TENANT --allow-no-subscriptions"
  fi
  if [[ "$(jq -r .user.type <<<"$acct")" == user ]]; then
    myroles="$(graph_get "$GRAPH/me/memberOf/microsoft.graph.directoryRole?\$select=displayName" | jq -r '[.value[].displayName] | join(", ")')"
    ok "your active directory roles: ${myroles:-none}"
    [[ "$myroles" == *"Global Administrator"* || "$myroles" == *"Privileged Role Administrator"* ]] \
      || warn "granting application permissions on Microsoft Graph and assigning directory roles needs Global Administrator or Privileged Role Administrator (activate it in PIM first if it's eligible) — --apply will stop at the consent step otherwise"
  fi
  if [[ -n "$WANT_APP_ID" ]]; then
    apps="$(az ad app show --id "$WANT_APP_ID" --query '[{appId:appId,id:id,tags:tags,displayName:displayName}]' -o json 2>/dev/null)" \
      || die "no app registration with Application (client) ID $WANT_APP_ID in tenant $TENANT_ID"
    APP_NAME="$(jq -r '.[0].displayName' <<<"$apps")"
  else
    apps="$(az ad app list --display-name "$APP_NAME" --query '[].{appId:appId,id:id,tags:tags}' -o json)"
  fi
  case "$(jq length <<<"$apps")" in
    0) ok "no app registration named '$APP_NAME' yet — it will be created" ;;
    1) if jq -e --arg t "$TAG" '.[0].tags | index($t)' <<<"$apps" >/dev/null || (( REUSE )); then
         APP_ID="$(jq -r '.[0].appId' <<<"$apps")"; APP_OID="$(jq -r '.[0].id' <<<"$apps")"
         jq -e --arg t "$TAG" '.[0].tags | index($t)' <<<"$apps" >/dev/null || FOREIGN=1
         ok "reusing app registration $APP_ID$( ((FOREIGN)) && echo ' (not created by this skill: its existing API permissions are kept and added to)')"
       else
         die "an app registration named '$APP_NAME' already exists ($(jq -r '.[0].appId' <<<"$apps")) and wasn't created by this skill. Pick another --app-name, or pass --reuse if it really is the SailPoint app to update."
       fi ;;
    *) die "several app registrations are named '$APP_NAME'; pick a unique --app-name" ;;
  esac
fi

# --- 2. Resolve permission names to ids --------------------------------------------------------------
step "Permissions (profiles: ${SEL[*]})"
RESOLVED='[]'
for ((i = 0; i < $(jq length <<<"$WANT"); i++)); do
  r="$(jq -c ".[$i]" <<<"$WANT")"; api="$(jq -r .api <<<"$r")"; appid="$(jq -r .appId <<<"$r")"
  if (( OFFLINE )); then
    res="$(jq -c '{api, appId, resourceSpId: "<\(.api) service principal id>",
                   appRoles: [.appRoles[] | {name: ., id: "<id of \(.)>"}], scopes: [.scopes[] | {name: ., id: "<id of \(.)>"}]}' <<<"$r")"
  else
    sp="$(az ad sp show --id "$appid" -o json 2>/dev/null)" \
      || die "the '$api' service principal ($appid) isn't in this tenant$([[ "$appid" == 00000002-0000-0ff1-ce00-000000000000 ]] && echo ' — Exchange Online needs an Exchange licence in the tenant')"
    res="$(jq -c --argjson want "$r" '
      . as $sp | {api: $want.api, appId: $want.appId, resourceSpId: $sp.id,
        appRoles: [ $want.appRoles[] as $n | {name: $n, id: ([$sp.appRoles[] | select(.value == $n and (.allowedMemberTypes | index("Application")))][0].id)} ],
        scopes:   [ $want.scopes[]   as $n | {name: $n, id: ([$sp.oauth2PermissionScopes[] | select(.value == $n)][0].id)} ]}' <<<"$sp")"
    missing="$(jq -r '[.appRoles[], .scopes[] | select(.id == null) | .name] | join(", ")' <<<"$res")"
    [[ -z "$missing" ]] || die "$api has no permission named: $missing — fix the profile file in assets/entra/permissions/"
  fi
  RESOLVED="$(jq -c --argjson x "$res" '. + [$x]' <<<"$RESOLVED")"
  printf '  %s: %s%s\n' "$api" "$(jq -r '[.appRoles[].name] | join(", ")' <<<"$res")" \
    "$(jq -r 'if (.scopes|length) > 0 then " | delegated: " + ([.scopes[].name] | join(", ")) else "" end' <<<"$res")" >&2
done
RRA="$(jq -c 'map({resourceAppId: .appId, resourceAccess: ([.appRoles[] | {id, type: "Role"}] + [.scopes[] | {id, type: "Scope"}])})' <<<"$RESOLVED")"
echo "  directory roles: $(jq -r 'if length == 0 then "none" else join(", ") end' <<<"$ROLES")" >&2

# --- 3. App registration -----------------------------------------------------------------------------
step "App registration '$APP_NAME'"
body="$(render_tmpl "$ASSETS/entra/app-registration.tmpl.json" \
          "$(jq -nc --arg n "$APP_NAME" --argjson rra "$RRA" '{APP_NAME: $n, REQUIRED_RESOURCE_ACCESS: $rra}')")"
if [[ -z "$APP_ID" ]]; then
  out="$(graph_write POST "$GRAPH/applications" "$body")"
  if (( APPLY )); then
    APP_ID="$(jq -r '.appId // empty' <<<"$out")"; APP_OID="$(jq -r '.id // empty' <<<"$out")"
    [[ -n "$APP_ID" ]] || die "app creation returned no appId"
    ok "created app $APP_ID"
  else APP_ID="<new app (client) id>" APP_OID="<new app object id>"; fi
else
  rra="$(jq -c '.requiredResourceAccess' <<<"$body")"
  if (( FOREIGN && OFFLINE )); then
    warn "plan-only: at run time the app's existing API permissions are read and kept; the PATCH below then holds them plus these"
  elif (( FOREIGN )); then
    # An app adopted with --reuse may serve other purposes: add to its API permissions, never remove any.
    have="$(az ad app show --id "$APP_ID" --query requiredResourceAccess -o json)"
    rra="$(jq -c --argjson add "$rra" '(. + $add) | group_by(.resourceAppId)
             | map({resourceAppId: .[0].resourceAppId, resourceAccess: ([.[].resourceAccess[]] | unique_by(.id))})' <<<"$have")"
    kept="$(jq -r --argjson want "$(jq -c '.requiredResourceAccess' <<<"$body")" \
             '[.[] | .resourceAccess[] | .id] - [$want[] | .resourceAccess[] | .id] | length' <<<"$have")"
    ok "keeping $kept existing permission(s) on the app that the selected profiles don't need"
  fi
  # Own app: its API permissions become exactly the selected profiles (an adopted app: the union above).
  graph_write PATCH "$GRAPH/applications/$APP_OID" "$(jq -nc --argjson r "$rra" '{requiredResourceAccess: $r}')" >/dev/null
  (( APPLY )) && ok "API permissions updated on $APP_ID"
fi

# --- 4. Service principal ----------------------------------------------------------------------------
step "Service principal"
if (( ! OFFLINE )) && is_guid "$APP_ID" && sp="$(az ad sp show --id "$APP_ID" -o json 2>/dev/null)"; then
  SP_OID="$(jq -r .id <<<"$sp")"; ok "exists: $SP_OID"
else
  if (( APPLY )); then
    SP_OID="$(az ad sp create --id "$APP_ID" --query id -o tsv)"; ok "created $SP_OID"
  else
    az_run az ad sp create --id "$APP_ID"; SP_OID="<new service principal object id>"
  fi
fi

# --- 5. Admin consent --------------------------------------------------------------------------------
step "Admin consent (app role assignments + delegated grants)"
have_roles='[]' have_grants='[]'
if is_guid "$SP_OID"; then
  have_roles="$(graph_get "$GRAPH/servicePrincipals/$SP_OID/appRoleAssignments" | jq -c '[.value[] | {resourceId, appRoleId}]')"
  have_grants="$(graph_get "$GRAPH/servicePrincipals/$SP_OID/oauth2PermissionGrants" | jq -c '[.value[] | {id, resourceId, scope}]')"
fi
for ((i = 0; i < $(jq length <<<"$RESOLVED"); i++)); do
  r="$(jq -c ".[$i]" <<<"$RESOLVED")"; res_sp="$(jq -r .resourceSpId <<<"$r")"
  for ((j = 0; j < $(jq '.appRoles | length' <<<"$r"); j++)); do
    name="$(jq -r ".appRoles[$j].name" <<<"$r")"; rid="$(jq -r ".appRoles[$j].id" <<<"$r")"
    if jq -e --arg s "$res_sp" --arg r "$rid" 'any(.[]; .resourceId == $s and .appRoleId == $r)' <<<"$have_roles" >/dev/null; then
      ok "$name: already granted"; continue
    fi
    graph_retry POST "$GRAPH/servicePrincipals/$SP_OID/appRoleAssignments" \
      "$(render_tmpl "$ASSETS/entra/app-role-assignment.tmpl.json" \
           "$(jq -nc --arg sp "$SP_OID" --arg res "$res_sp" --arg r "$rid" '{SP_OBJECT_ID: $sp, RESOURCE_SP_ID: $res, APP_ROLE_ID: $r}')")" >/dev/null
    (( APPLY )) && ok "$name: granted"
  done
  scopes="$(jq -r '[.scopes[].name] | join(" ")' <<<"$r")"
  if [[ -n "$scopes" ]]; then
    existing="$(jq -c --arg s "$res_sp" '[.[] | select(.resourceId == $s)][0] // empty' <<<"$have_grants")"
    if [[ -n "$existing" ]]; then
      merged="$(jq -rn --arg a "$(jq -r .scope <<<"$existing")" --arg b "$scopes" '($a + " " + $b) | split(" ") | map(select(. != "")) | unique | join(" ")')"
      graph_retry PATCH "$GRAPH/oauth2PermissionGrants/$(jq -r .id <<<"$existing")" "$(jq -nc --arg s "$merged" '{scope: $s}')" >/dev/null
    else
      graph_retry POST "$GRAPH/oauth2PermissionGrants" \
        "$(render_tmpl "$ASSETS/entra/oauth2-permission-grant.tmpl.json" \
             "$(jq -nc --arg sp "$SP_OID" --arg res "$res_sp" --arg s "$scopes" '{SP_OBJECT_ID: $sp, RESOURCE_SP_ID: $res, SCOPES: $s}')")" >/dev/null
    fi
    (( APPLY )) && ok "delegated ($(jq -r .api <<<"$r")): $scopes granted tenant-wide"
  fi
done

# --- 6. Client secret --------------------------------------------------------------------------------
step "Client secret"
SECRET_FILE="$SECRET_DIR/$(slug "${DOMAIN%%.*}")-$(slug "$APP_NAME").secret" SECRET_EXPIRES=""
if (( NO_SECRET )); then
  warn "skipped (--no-secret): configure the source with certificate credentials instead"; SECRET_FILE=""
elif [[ -s "$SECRET_FILE" && $ROTATE -eq 0 ]]; then
  ok "keeping existing secret file $SECRET_FILE (--rotate-secret adds a new one)"
elif (( APPLY )); then
  ( umask 077; mkdir -p "$SECRET_DIR" )
  # --append keeps any other credentials on the app; the value is written straight to the file.
  ( umask 077; az ad app credential reset --id "$APP_ID" --append --display-name "$SECRET_NAME" \
      --years "$SECRET_YEARS" --query password -o tsv --only-show-errors >"$SECRET_FILE" )
  chmod 600 "$SECRET_FILE"
  [[ -s "$SECRET_FILE" ]] || die "no secret returned"
  ok "secret value written to $SECRET_FILE (not shown). Paste it into ISC as Client Secret, or let isc-source.sh read it."
else
  az_run az ad app credential reset --id "$APP_ID" --append --display-name "$SECRET_NAME" --years "$SECRET_YEARS" --query password -o tsv
  echo "    -> value would be written to $SECRET_FILE (chmod 600)" >&2
fi
if (( APPLY )) && [[ -n "$SECRET_FILE" ]]; then
  SECRET_EXPIRES="$(az ad app credential list --id "$APP_ID" \
                      --query "max_by([?displayName=='$SECRET_NAME'], &endDateTime).endDateTime" -o tsv 2>/dev/null || true)"
  [[ -n "$SECRET_EXPIRES" ]] && ok "newest '$SECRET_NAME' secret expires $SECRET_EXPIRES — rotate before then or aggregation stops (AADSTS7000222)"
fi

# --- 7. Directory roles ------------------------------------------------------------------------------
step "Directory roles"
[[ "$(jq length <<<"$ROLES")" -gt 0 ]] || ok "none needed for these profiles"
have_dir='[]'
is_guid "$SP_OID" && have_dir="$(graph_get "$GRAPH/roleManagement/directory/roleAssignments?\$filter=$(enc "principalId eq '$SP_OID'")" | jq -c '[.value[].roleDefinitionId]')"
for ((i = 0; i < $(jq length <<<"$ROLES"); i++)); do
  rn="$(jq -r ".[$i]" <<<"$ROLES")"
  if (( OFFLINE )); then rdid="<role definition id of $rn>"; else
    rdid="$(graph_get "$GRAPH/roleManagement/directory/roleDefinitions?\$filter=$(enc "displayName eq '$rn'")" | jq -r '.value[0].id // empty')"
    [[ -n "$rdid" ]] || die "no directory role named '$rn'"
  fi
  if jq -e --arg r "$rdid" 'index($r)' <<<"$have_dir" >/dev/null; then ok "$rn: already assigned"; continue; fi
  graph_retry POST "$GRAPH/roleManagement/directory/roleAssignments" \
    "$(render_tmpl "$ASSETS/entra/role-assignment.tmpl.json" "$(jq -nc --arg sp "$SP_OID" --arg r "$rdid" '{SP_OBJECT_ID: $sp, ROLE_DEFINITION_ID: $r}')")" >/dev/null
  (( APPLY )) && ok "$rn: assigned"
done

# --- 8. Exchange Online certificate -------------------------------------------------------------------
EXO_JSON='null'
if has_profile exchange; then
  step "Exchange Online certificate"
  base="$SECRET_DIR/$(slug "${DOMAIN%%.*}")-$(slug "$APP_NAME")-exo"
  if (( EXO_CERT )); then
    if (( APPLY )); then
      need_cmd openssl
      ( umask 077; mkdir -p "$SECRET_DIR"
        openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days $((365 * SECRET_YEARS)) -subj "/CN=$APP_NAME Exchange" \
          -keyout "$base.key" -out "$base.crt" 2>/dev/null
        openssl rand -base64 24 | tr -d '\n' >"$base.pfx.pass"
        openssl pkcs12 -export -inkey "$base.key" -in "$base.crt" -out "$base.pfx" -passout "file:$base.pfx.pass"
        base64 <"$base.pfx" | tr -d '\n' >"$base.pfx.b64" )
      az ad app credential reset --id "$APP_ID" --cert "@$base.crt" --append -o none
      thumb="$(openssl x509 -in "$base.crt" -noout -fingerprint -sha1 | sed 's/.*=//; s/://g')"
      ok "certificate $thumb added to the app; pfx (base64) $base.pfx.b64, password $base.pfx.pass"
    else
      az_run openssl req -x509 -newkey rsa:2048 -nodes -days $((365 * SECRET_YEARS)) -subj "/CN=$APP_NAME Exchange" -keyout "$base.key" -out "$base.crt"
      az_run openssl pkcs12 -export -inkey "$base.key" -in "$base.crt" -out "$base.pfx" -passout "file:$base.pfx.pass"
      az_run az ad app credential reset --id "$APP_ID" --cert "@$base.crt" --append
      thumb="<thumbprint>"
    fi
    EXO_JSON="$(jq -nc --arg t "$thumb" --arg b "$base.pfx.b64" --arg p "$base.pfx.pass" '{certThumbprint: $t, pfxBase64File: $b, pfxPasswordFile: $p}')"
  else
    warn "Exchange Online uses certificate auth: rerun with --exchange-cert, or add your own: az ad app credential reset --id $APP_ID --cert @cert.pem --append"
  fi
fi

# --- 9. Azure RBAC for AI Foundry agents --------------------------------------------------------------
if has_profile ai-agents; then
  step "Azure RBAC (AI Foundry agent discovery)"
  if [[ -z "$FOUNDRY_SUBS" ]]; then
    warn "no --foundry-subscriptions: Foundry discovery needs $(jq -r '[.roles[].role] | join(" + ")' "$ASSETS/entra/azure-rbac.json") on each subscription with agents"
  else
    IFS=, read -ra _subs <<<"$FOUNDRY_SUBS"
    for sub in "${_subs[@]}"; do
      scope="$(jq -r --arg s "$sub" '.scope | sub("\\$\\{SUBSCRIPTION_ID\\}"; $s)' "$ASSETS/entra/azure-rbac.json")"
      while IFS= read -r role; do
        if (( ! OFFLINE )); then
          az role definition list --name "$role" --scope "$scope" --query '[0].id' -o tsv 2>/dev/null | grep -q . \
            || { warn "role '$role' not found at $scope — check its current name in Azure and edit azure-rbac.json"; continue; }
          if is_guid "$SP_OID" && [[ "$(az role assignment list --assignee "$SP_OID" --role "$role" --scope "$scope" --query 'length(@)' -o tsv)" != 0 ]]; then
            ok "$role @ $scope: already assigned"; continue
          fi
        fi
        az_run az role assignment create --assignee-object-id "$SP_OID" --assignee-principal-type ServicePrincipal --role "$role" --scope "$scope" -o none
        (( APPLY )) && ok "$role @ $scope: assigned"
      done < <(jq -r '.roles[].role' "$ASSETS/entra/azure-rbac.json")
    done
  fi
  warn "Copilot Studio and Agent 365 discovery need manual steps (Power Platform application user + roles; refresh-token grant) — see references/entra-permissions.md"
fi
if has_profile pim; then
  warn "PIM for Azure resources (RBAC) also needs Owner or User Access Administrator for the app at Tenant Root Group/subscription — not granted by this script"
fi

# --- 10. Summary -------------------------------------------------------------------------------------
summary="$(jq -n --arg tenant "$TENANT_ID" --arg domain "$DOMAIN" --arg app "$APP_NAME" --arg appId "$APP_ID" \
  --arg appOid "$APP_OID" --arg sp "$SP_OID" --argjson profiles "$SEL_JSON" --argjson res "$RESOLVED" \
  --argjson roles "$ROLES" --arg sf "$SECRET_FILE" --arg se "$SECRET_EXPIRES" --argjson exo "$EXO_JSON" \
  --arg subs "$FOUNDRY_SUBS" --arg applied "$APPLY" '
  {tenantId: $tenant, domainName: $domain, appName: $app, clientId: $appId, appObjectId: $appOid,
   servicePrincipalId: $sp, profiles: $profiles,
   permissions: [ $res[] | {api, application: [.appRoles[].name], delegated: [.scopes[].name]} ],
   directoryRoles: $roles, clientSecretFile: (if $sf == "" then null else $sf end),
   clientSecretExpires: (if $se == "" then null else $se end), exchange: $exo,
   foundrySubscriptions: (if $subs == "" then [] else ($subs | split(",")) end),
   applied: ($applied == "1"), generatedAt: (now | todate)}')"
step "Summary"
if (( APPLY )); then
  printf '%s\n' "$summary" >"$OUT"; ok "written to $OUT (no secrets in it)"
else
  warn "dry-run: nothing was changed. Review the plan above, then rerun with --apply"
fi
printf '%s\n' "$summary"
