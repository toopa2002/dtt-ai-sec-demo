#!/usr/bin/env bash
# Put each MCP adapter behind its own AWS Bedrock AgentCore Gateway (TOOLS_VIA=agentcore-gateway).
#
#   agent ──token #1──► AgentCore Gateway mcpdemo-gw-<adapter> (Entra JWT, aud = mcpdemo-agent-api)
#                          └─ target <adapter>: on-behalf-of exchange (Entra credential provider) → token #2
#                             ──► Microsoft MCP Gateway  $GATEWAY_PUBLIC/adapters/<adapter>/mcp
#
# The Microsoft gateway still sees a per-user token with the user's mcp.* roles, so gate 3 is unchanged.
# AWS (and governance tools such as SailPoint) now see the gateways and their MCP targets.
#
# Plus one inbound gateway in front of the agent itself (mcpdemo-gw-agent, target "mcpdemo-agent"): the chatbot can
# call the agent through it (AGENT_VIA=agentcore-gateway). It has no protocol type (runtime targets can't sit on MCP
# gateways), checks the same Entra token, and passes it through unchanged to the runtime, whose JWT authorizer and
# azp/role checks still apply. Requests and SSE responses are forwarded as-is.
#
# Why one gateway per adapter: a gateway lists its targets one per tools/list page, ordered by (random) target id,
# and a target the user may not use answers its page with an error and no nextCursor - so with several targets
# behind one gateway, one denied adapter would hide every adapter listed after it.
#
#   scripts/agentcore-gateway.sh            create or update everything (idempotent)
#   scripts/agentcore-gateway.sh --delete   remove targets, gateways, credential provider and role
source "$(dirname "$0")/lib.sh"
need AWS_REGION TENANT_ID AGENT_API_CLIENT_ID GATEWAY_API_CLIENT_ID NGROK_DOMAIN
GW_PREFIX=mcpdemo-gw-
LEGACY_GW=mcpdemo-gateway   # single-gateway layout from before; removed on the next run
AGENT_GW=${GW_PREFIX}agent   # inbound gateway in front of the agent runtime
AGENT_TARGET=mcpdemo-agent
PROVIDER=mcpdemo-entra-obo
ROLE=mcpdemo-agentcore-gateway-role
PARAM=/mcpdemo/agent-obo-secret
ADAPTERS=${MCP_ADAPTERS:-weather hr-directory}
ACC=(bedrock-agentcore-control --region "$AWS_REGION" --output json)
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

gateway_id() { aws "${ACC[@]}" list-gateways | jq -r --arg n "$1" '.items[] | select(.name == $n) | .gatewayId' | head -1; }
delete_gateway() {  # delete_gateway <name>: its targets first, then the gateway
  local gw t; gw=$(gateway_id "$1"); [[ -n "$gw" ]] || return 0
  for t in $(aws "${ACC[@]}" list-gateway-targets --gateway-identifier "$gw" | jq -r '.items[].targetId'); do
    aws "${ACC[@]}" delete-gateway-target --gateway-identifier "$gw" --target-id "$t" >/dev/null
  done
  for _ in $(seq 1 30); do [[ -z $(aws "${ACC[@]}" list-gateway-targets --gateway-identifier "$gw" | jq -r '.items[]') ]] && break; sleep 5; done
  aws "${ACC[@]}" delete-gateway --gateway-identifier "$gw" >/dev/null && ok "gateway $1 ($gw) deleted"
}
target_id()  { aws "${ACC[@]}" list-gateway-targets --gateway-identifier "$1" | jq -r --arg n "$2" '.items[] | select(.name == $n) | .targetId' | head -1; }
wait_ready() {  # wait_ready <label> <command...> — polls .status until READY (or a *FAILED* status)
  local label=$1 s; shift
  for _ in $(seq 1 60); do
    s=$("$@" | jq -r .status)
    case "$s" in READY) ok "$label READY"; return ;; *FAIL*) die "$label: $s — $("$@" | jq -c '.statusReasons // .')";; esac
    sleep 5
  done
  die "$label still $s after 5 minutes"
}

if [[ "${1:-}" == --delete ]]; then
  step "Deleting the AgentCore Gateway setup"
  for adapter in ${ADAPTERS//,/ }; do delete_gateway "$GW_PREFIX$adapter"; done
  delete_gateway "$AGENT_GW"
  delete_gateway "$LEGACY_GW"
  aws "${ACC[@]}" delete-oauth2-credential-provider --name "$PROVIDER" >/dev/null 2>&1 && ok "credential provider deleted" || true
  aws iam delete-role-policy --role-name "$ROLE" --policy-name gateway-outbound-auth 2>/dev/null || true
  aws iam delete-role --role-name "$ROLE" 2>/dev/null && ok "role deleted" || true
  unset_env AGENTCORE_GATEWAY_URLS; unset_env AGENT_GATEWAY_URL; unset_env AGENTCORE_GATEWAY_ID; unset_env AGENTCORE_GATEWAY_URL
  exit 0
fi

step "Entra on-behalf-of credential provider '$PROVIDER'"
# Same Entra app and secret the agent uses for its own OBO exchange (mcpdemo-agent-api), so the gateway can
# exchange token #1 (aud = agent-api) for a gateway-api token on behalf of the user.
# The secret goes from SSM into a 0600 temp file and straight to the API; it is never printed.
umask 077; INPUT=$(mktemp); trap 'rm -f "$INPUT"' EXIT
aws ssm get-parameter --region "$AWS_REGION" --name "$PARAM" --with-decryption --output json |
  jq --arg id "$AGENT_API_CLIENT_ID" --arg tenant "$TENANT_ID" \
    '{microsoftOauth2ProviderConfig: {clientId: $id, clientSecret: .Parameter.Value, tenantId: $tenant}}' >"$INPUT"
jq -e '.microsoftOauth2ProviderConfig.clientSecret | length > 0' "$INPUT" >/dev/null \
  || die "could not read the OBO secret from SSM $PARAM — run scripts/agent-deploy.sh first"
# The CLI echoes the input JSON on validation errors: never let its stderr through unredacted.
provider_call() {
  local err out; err=$(mktemp)
  if out=$(aws "${ACC[@]}" "$1-oauth2-credential-provider" --name "$PROVIDER" \
             --credential-provider-vendor MicrosoftOauth2 --oauth2-provider-config-input "file://$INPUT" 2>"$err"); then
    rm -f "$err"; printf '%s' "$out"
  else
    sed -E 's/("clientSecret"[[:space:]]*:[[:space:]]*")[^"]*"/\1***"/g' "$err" >&2; rm -f "$err"; return 1
  fi
}
if aws "${ACC[@]}" get-oauth2-credential-provider --name "$PROVIDER" >/dev/null 2>&1; then
  PROV=$(provider_call update) || die "updating the credential provider failed"
  ok "updated (secret refreshed from SSM)"
else
  PROV=$(provider_call create) || die "creating the credential provider failed"
  ok "created"
fi
rm -f "$INPUT"
PROVIDER_ARN=$(jq -r .credentialProviderArn <<<"$PROV")
SECRET_ARN=$(jq -r '.clientSecretArn.secretArn // ([.. | strings | select(test("^arn:aws:secretsmanager:"))][0]) // empty' <<<"$PROV")
[[ -n "$PROVIDER_ARN" && -n "$SECRET_ARN" ]] || die "unexpected provider response: $(jq -c 'del(.. | .clientSecret?)' <<<"$PROV")"

step "Gateway execution role '$ROLE'"
TRUST=$(jq -n --arg acct "$ACCOUNT" --arg arn "arn:aws:bedrock-agentcore:$AWS_REGION:$ACCOUNT:gateway/$GW_PREFIX*" '
  {Version:"2012-10-17", Statement:[{Effect:"Allow", Principal:{Service:"bedrock-agentcore.amazonaws.com"},
   Action:"sts:AssumeRole", Condition:{StringEquals:{"aws:SourceAccount":$acct}, ArnLike:{"aws:SourceArn":$arn}}}]}')
if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$ROLE" --policy-document "$TRUST"
else
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document "$TRUST" \
    --description "AgentCore Gateways $GW_PREFIX*: Entra OBO to the MCP adapters" --tags Key=ManagedBy,Value=mcpdemo >/dev/null
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name gateway-outbound-auth --policy-document "$(jq -n \
  --arg wid "arn:aws:bedrock-agentcore:$AWS_REGION:$ACCOUNT:workload-identity-directory/default" \
  --arg prov "$PROVIDER_ARN" --arg vault "arn:aws:bedrock-agentcore:$AWS_REGION:$ACCOUNT:token-vault/default" \
  --arg secret "$SECRET_ARN" '
  {Version:"2012-10-17", Statement:[
    {Effect:"Allow", Action:["bedrock-agentcore:GetWorkloadAccessToken","bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                              "bedrock-agentcore:GetWorkloadAccessTokenForUserId"],
     Resource:[$wid, ($wid + "/workload-identity/*")]},
    {Effect:"Allow", Action:"bedrock-agentcore:GetResourceOauth2Token",
     Resource:[$prov, $vault, ($wid), ($wid + "/workload-identity/*")]},
    {Effect:"Allow", Action:"secretsmanager:GetSecretValue", Resource:$secret}]}')"
ROLE_ARN=$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)
ok "$ROLE_ARN"

AUTHZ=$(jq -n --arg t "$TENANT_ID" --arg a "$AGENT_API_CLIENT_ID" '{customJWTAuthorizer:{
  discoveryUrl:"https://login.microsoftonline.com/\($t)/v2.0/.well-known/openid-configuration",
  allowedAudience:[$a, "api://\($a)"]}}')
TAGS=$(jq -n --arg rt "${AGENT_RUNTIME_ARN:-}" '{ManagedBy:"mcpdemo", UsedByAgentRuntime:$rt}')
# DYNAMIC listing: tools are listed at request time with the *user's* exchanged token. The DEFAULT mode syncs with
# an app-only token, which the Microsoft gateway rejects (its roles are user roles only).
CRED=$(jq -n --arg p "$PROVIDER_ARN" --arg s "api://$GATEWAY_API_CLIENT_ID/access_as_user" \
  '[{credentialProviderType:"OAUTH", credentialProvider:{oauthCredentialProvider:{
      providerArn:$p, grantType:"TOKEN_EXCHANGE", scopes:[$s]}}}]')
URLS=()
GW_ARNS=()
# The tools each MCP server exposes, read from its source; declared in AWS below (gateway + runtime tags, schema).
CAPS=$(python3 "$REPO_ROOT/scripts/agent_capabilities.py" ${ADAPTERS//,/ })
for adapter in ${ADAPTERS//,/ }; do
  name="$GW_PREFIX$adapter"
  step "AgentCore Gateway '$name' -> $GATEWAY_PUBLIC/adapters/$adapter/mcp"
  GW=$(gateway_id "$name")
  if [[ -z "$GW" ]]; then
    sleep 10   # give IAM a moment so a new role can be assumed by the service
    PROTO=$(jq -n --arg a "$adapter" '{mcp:{supportedVersions:["2025-06-18","2025-03-26"],
      instructions:"\($a) tools. Access is authorized per user by the Microsoft MCP Gateway."}}')
    GW=$(aws "${ACC[@]}" create-gateway --name "$name" --role-arn "$ROLE_ARN" --protocol-type MCP \
          --protocol-configuration "$PROTO" --authorizer-type CUSTOM_JWT --authorizer-configuration "$AUTHZ" \
          --description "MCP adapter '$adapter' via the Microsoft MCP Gateway, per-user Entra OBO" \
          --tags "$TAGS" | jq -r .gatewayId)
    ok "created $GW"
  else
    ok "exists: $GW"
  fi
  wait_ready "gateway $name" aws "${ACC[@]}" get-gateway --gateway-identifier "$GW"
  CFG=$(jq -n --arg e "$GATEWAY_PUBLIC/adapters/$adapter/mcp" '{mcp:{mcpServer:{endpoint:$e, listingMode:"DYNAMIC"}}}')
  T=$(target_id "$GW" "$adapter")
  if [[ -z "$T" ]]; then
    T=$(aws "${ACC[@]}" create-gateway-target --gateway-identifier "$GW" --name "$adapter" \
          --description "MCP adapter '$adapter' behind the Microsoft MCP Gateway" \
          --target-configuration "$CFG" --credential-provider-configurations "$CRED" | jq -r .targetId)
    ok "target created $T"
  else
    aws "${ACC[@]}" update-gateway-target --gateway-identifier "$GW" --target-id "$T" --name "$adapter" \
      --target-configuration "$CFG" --credential-provider-configurations "$CRED" >/dev/null
    ok "target updated $T"
  fi
  wait_ready "target $adapter" aws "${ACC[@]}" get-gateway-target --gateway-identifier "$GW" --target-id "$T"
  gw_json=$(aws "${ACC[@]}" get-gateway --gateway-identifier "$GW")
  url=$(jq -r .gatewayUrl <<<"$gw_json")
  URLS+=("$adapter=${url%/mcp}")
  GW_ARNS+=("$adapter=$(jq -r .gatewayArn <<<"$gw_json")")
  # AWS tag values allow letters, digits, spaces and _ . : / = + - @ (no commas): space-separated lists.
  aws "${ACC[@]}" tag-resource --resource-arn "$(jq -r .gatewayArn <<<"$gw_json")" \
    --tags "$(jq -c --arg a "$adapter" '{"mcp:server": $a, "mcp:tools": ([.[$a][].name] | join(" "))}' <<<"$CAPS")"
done

if [[ -n "${AGENT_RUNTIME_ARN:-}" ]]; then
  step "Inbound AgentCore Gateway '$AGENT_GW' -> agent runtime ${AGENT_RUNTIME_ARN##*/}"
  GW=$(gateway_id "$AGENT_GW")
  if [[ -z "$GW" ]]; then
    sleep 10
    # No --protocol-type: AgentCore Runtime (HTTP) targets cannot be added to MCP gateways.
    GW=$(aws "${ACC[@]}" create-gateway --name "$AGENT_GW" --role-arn "$ROLE_ARN" \
          --authorizer-type CUSTOM_JWT --authorizer-configuration "$AUTHZ" \
          --description "Inbound gateway for the mcpdemo agent runtime (Entra JWT, token passthrough)" \
          --tags "$TAGS" | jq -r .gatewayId)
    ok "created $GW"
  else
    ok "exists: $GW"
  fi
  wait_ready "gateway $AGENT_GW" aws "${ACC[@]}" get-gateway --gateway-identifier "$GW"
  # Declare the agent's API and the MCP tools it uses, as the target's OpenAPI schema (visible via GetGatewayTarget).
  SCHEMA=$(jq -c --argjson arns "$(printf '%s\n' "${GW_ARNS[@]}" | jq -R 'split("=") | {(.[0]): (.[1:] | join("="))}' | jq -s add)" '
    {openapi: "3.0.3",
     info: {title: "mcpdemo agent", version: "1",
            description: ("Weather and HR assistant. Calls MCP tools through AgentCore Gateways, on behalf of the signed-in user: "
              + ([to_entries[] | "\(.key) (\([.value[].name] | join(", ")))"] | join("; ")))},
     "x-mcp-servers": [to_entries[] | {name: .key, gatewayArn: $arns[.key],
                        tools: [.value[] | {name, description, parameters: .params}]}],
     paths: {"/invocations": {post: {operationId: "invoke", summary: "Ask the agent a question",
       requestBody: {required: true, content: {"application/json": {schema: {type: "object", required: ["prompt"],
         properties: {prompt: {type: "string"}}}}}},
       responses: {"200": {description: "Server-sent events (hop, final, error)",
         content: {"text/event-stream": {schema: {type: "string"}}}}}}}}}' <<<"$CAPS")
  CFG=$(jq -n --arg arn "$AGENT_RUNTIME_ARN" --arg schema "$SCHEMA" \
    '{http:{agentcoreRuntime:{arn:$arn, qualifier:"DEFAULT", schema:{source:{inlinePayload:$schema}}}}}')
  PASS='[{"credentialProviderType":"JWT_PASSTHROUGH"}]'
  T=$(target_id "$GW" "$AGENT_TARGET")
  if [[ -z "$T" ]]; then
    T=$(aws "${ACC[@]}" create-gateway-target --gateway-identifier "$GW" --name "$AGENT_TARGET" \
          --description "mcpdemo agent runtime (DEFAULT endpoint)" \
          --target-configuration "$CFG" --credential-provider-configurations "$PASS" | jq -r .targetId)
    ok "target created $T"
  else
    aws "${ACC[@]}" update-gateway-target --gateway-identifier "$GW" --target-id "$T" --name "$AGENT_TARGET" \
      --target-configuration "$CFG" --credential-provider-configurations "$PASS" >/dev/null
    ok "target updated $T"
  fi
  wait_ready "target $AGENT_TARGET" aws "${ACC[@]}" get-gateway-target --gateway-identifier "$GW" --target-id "$T"
  url=$(aws "${ACC[@]}" get-gateway --gateway-identifier "$GW" | jq -r .gatewayUrl)
  set_env AGENT_GATEWAY_URL "${url%/mcp}/$AGENT_TARGET"

  step "Declaring the agent's MCP servers and tools on the runtime (tags)"
  aws "${ACC[@]}" tag-resource --resource-arn "$AGENT_RUNTIME_ARN" --tags "$(jq -c --arg gws "${GW_ARNS[*]#*=}" '{
      "mcp:servers": (keys_unsorted | join(" ")),
      "mcp:tools": ([to_entries[] | .key as $s | .value[] | "\($s)/\(.name)"] | join(" ")),
      "mcp:gateways": $gws}' <<<"$CAPS")"
  ok "$(aws "${ACC[@]}" list-tags-for-resource --resource-arn "$AGENT_RUNTIME_ARN" | jq -c '.tags | with_entries(select(.key | startswith("mcp:")))')"
  ok "agent reachable at ${url%/mcp}/$AGENT_TARGET/invocations"
else
  warn "AGENT_RUNTIME_ARN not set: skipping the inbound gateway for the agent (deploy the agent first)"
fi

if [[ -n "$(gateway_id "$LEGACY_GW")" ]]; then
  step "Removing the old single gateway '$LEGACY_GW'"
  delete_gateway "$LEGACY_GW"
fi

set_env AGENTCORE_GATEWAY_URLS "$(IFS=,; echo "${URLS[*]}")"
unset_env AGENTCORE_GATEWAY_ID; unset_env AGENTCORE_GATEWAY_URL
ok "AgentCore Gateways ready: ${URLS[*]}"
echo "Use them:  TOOLS_VIA=agentcore-gateway make agent      (back to the direct path: TOOLS_VIA=mcp-gateway make agent)"
