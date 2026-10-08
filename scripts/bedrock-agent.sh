#!/usr/bin/env bash
# Bedrock Agent "mcpdemo-tools-agent": the model loop for mcpdemo_agent when AGENT_ENGINE=bedrock-agent.
#
# One action group per MCP server (weather, hr-directory), one function per MCP tool - read from the servers' source
# by scripts/agent_capabilities.py. The action groups are RETURN_CONTROL: no Lambda runs them. The Bedrock Agent hands
# each chosen tool call back to mcpdemo_agent, which runs it through the AgentCore Gateways with the *user's* token and
# returns the result. So the user's token never leaves mcpdemo_agent, and governance tools that read Bedrock Agent
# action groups (e.g. SailPoint Agentic Fabric's "Tools") can see the agent's tools.
#
#   scripts/bedrock-agent.sh            create or update the agent, action groups and alias "live" (idempotent)
#   scripts/bedrock-agent.sh --delete   delete them and the agent's IAM role
source "$(dirname "$0")/lib.sh"
need AWS_REGION BEDROCK_MODEL_ID
NAME=mcpdemo-tools-agent
ROLE=mcpdemo-tools-agent-role
ALIAS=live
ADAPTERS=${MCP_ADAPTERS:-weather hr-directory}
BA=(bedrock-agent --region "$AWS_REGION" --output json)
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

agent_id() { aws "${BA[@]}" list-agents | jq -r --arg n "$NAME" '.agentSummaries[] | select(.agentName == $n) | .agentId' | head -1; }
wait_agent() {  # wait_agent <id> <wanted status...>
  local id=$1 s; shift
  for _ in $(seq 1 60); do
    s=$(aws "${BA[@]}" get-agent --agent-id "$id" | jq -r .agent.agentStatus)
    for want in "$@"; do [[ "$s" == "$want" ]] && return; done
    [[ "$s" == FAILED ]] && die "agent $id FAILED: $(aws "${BA[@]}" get-agent --agent-id "$id" | jq -c .agent.failureReasons)"
    sleep 4
  done
  die "agent $id still $s"
}

if [[ "${1:-}" == --delete ]]; then
  step "Deleting Bedrock Agent '$NAME'"
  id=$(agent_id)
  [[ -z "$id" ]] || { aws "${BA[@]}" delete-agent --agent-id "$id" --skip-resource-in-use-check >/dev/null && ok "agent $id deleted"; }
  aws iam delete-role-policy --role-name "$ROLE" --policy-name model 2>/dev/null || true
  aws iam delete-role --role-name "$ROLE" 2>/dev/null && ok "role deleted" || true
  unset_env BEDROCK_AGENT_ID; unset_env BEDROCK_AGENT_ALIAS_ID
  exit 0
fi

# The model: an inference profile id (e.g. global.anthropic...) becomes its ARN; a plain model id stays as is.
case "$BEDROCK_MODEL_ID" in
  global.*|apac.*|us.*|eu.*) MODEL="arn:aws:bedrock:$AWS_REGION:$ACCOUNT:inference-profile/$BEDROCK_MODEL_ID" ;;
  *) MODEL="$BEDROCK_MODEL_ID" ;;
esac

step "IAM role '$ROLE' (Bedrock Agents service -> model only)"
# Account condition only: Bedrock validates the role when the agent is created, before an agent ARN exists to match.
TRUST=$(jq -cn --arg a "$ACCOUNT" '{Version:"2012-10-17",
  Statement:[{Effect:"Allow", Principal:{Service:"bedrock.amazonaws.com"}, Action:"sts:AssumeRole",
  Condition:{StringEquals:{"aws:SourceAccount":$a}}}]}')
if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$ROLE" --policy-document "$TRUST"
else
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document "$TRUST" \
    --description "Bedrock Agent $NAME: invoke the model" --tags Key=ManagedBy,Value=mcpdemo >/dev/null
  sleep 10
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name model --policy-document "$(jq -cn --arg m "$MODEL" '{
  Version:"2012-10-17", Statement:[{Effect:"Allow",
  Action:["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream","bedrock:GetInferenceProfile"],
  Resource:[$m, "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5*"]}]}')"
ROLE_ARN=$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)
ok "$ROLE_ARN"

step "Bedrock Agent '$NAME'"
INSTRUCTION="You are a helpful company assistant for weather and HR directory questions. Answer using the tools available \
to you. Never invent employee information that did not come from a tool. If a tool result says the user does not \
have access to a service, tell the user they lack access to that service and answer the rest. Keep answers short."
ID=$(agent_id)
COMMON=(--agent-name "$NAME" --foundation-model "$MODEL" --agent-resource-role-arn "$ROLE_ARN" --instruction "$INSTRUCTION"
        --idle-session-ttl-in-seconds 600
        --description "Model loop for the mcpdemo AgentCore agent; tools are MCP servers reached through AgentCore Gateways")
if [[ -z "$ID" ]]; then
  ID=$(aws "${BA[@]}" create-agent "${COMMON[@]}" --tags ManagedBy=mcpdemo | jq -r .agent.agentId)
  ok "created $ID"
else
  aws "${BA[@]}" update-agent --agent-id "$ID" "${COMMON[@]}" >/dev/null
  ok "updated $ID"
fi
wait_agent "$ID" NOT_PREPARED PREPARED

CAPS=$(python3 "$REPO_ROOT/scripts/agent_capabilities.py" ${ADAPTERS//,/ })
# Action groups are named <adapter>-mcp (= the MCP server image name). Bedrock Agents mask action-group names in
# their answers, so a group simply called "weather" would turn the word "Weather" into "<REDACTED>".
WANTED=()
for adapter in ${ADAPTERS//,/ }; do WANTED+=("$adapter-mcp"); done
for stale in $(aws "${BA[@]}" list-agent-action-groups --agent-id "$ID" --agent-version DRAFT \
               | jq -r --argjson w "$(printf '%s\n' "${WANTED[@]}" | jq -R . | jq -s .)" \
                   '.actionGroupSummaries[] | select(.actionGroupName as $n | $w | index($n) | not) | "\(.actionGroupId)=\(.actionGroupName)"'); do
  agid=${stale%%=*}; agname=${stale#*=}
  [[ "$agname" == User* || "$agname" == Code* ]] && continue   # built-in groups, if any
  aws "${BA[@]}" update-agent-action-group --agent-id "$ID" --agent-version DRAFT --action-group-id "$agid" \
    --action-group-name "$agname" --action-group-state DISABLED --action-group-executor customControl=RETURN_CONTROL \
    --function-schema "$(aws "${BA[@]}" get-agent-action-group --agent-id "$ID" --agent-version DRAFT --action-group-id "$agid" | jq -c .agentActionGroup.functionSchema)" >/dev/null
  aws "${BA[@]}" delete-agent-action-group --agent-id "$ID" --agent-version DRAFT --action-group-id "$agid" >/dev/null
  ok "removed old action group $agname"
done
for adapter in ${ADAPTERS//,/ }; do
  group="$adapter-mcp"
  # Bedrock function parameters: {name: {type, description, required}}; types string/integer/number/boolean.
  SCHEMA=$(jq -c --arg a "$adapter" '{functions: [.[$a][] | {name, description,
    parameters: (.params | with_entries(.value = {type: .value, description: .key, required: (.key != "days")}))}]}' <<<"$CAPS")
  AG=$(aws "${BA[@]}" list-agent-action-groups --agent-id "$ID" --agent-version DRAFT \
       | jq -r --arg a "$group" '.actionGroupSummaries[] | select(.actionGroupName == $a) | .actionGroupId')
  ARGS=(--agent-id "$ID" --agent-version DRAFT --action-group-name "$group" --action-group-state ENABLED
        --description "MCP server '$adapter' via its AgentCore Gateway (returned to mcpdemo_agent, run with the user's token)"
        --action-group-executor customControl=RETURN_CONTROL --function-schema "$SCHEMA")
  if [[ -z "$AG" ]]; then
    aws "${BA[@]}" create-agent-action-group "${ARGS[@]}" >/dev/null
  else
    aws "${BA[@]}" update-agent-action-group --action-group-id "$AG" "${ARGS[@]}" >/dev/null
  fi
  ok "action group $group: $(jq -r '[.functions[].name] | join(", ")' <<<"$SCHEMA")"
done

step "Prepare + alias '$ALIAS'"
aws "${BA[@]}" prepare-agent --agent-id "$ID" >/dev/null
wait_agent "$ID" PREPARED
ALIAS_ID=$(aws "${BA[@]}" list-agent-aliases --agent-id "$ID" | jq -r --arg n "$ALIAS" '.agentAliasSummaries[] | select(.agentAliasName == $n) | .agentAliasId')
if [[ -z "$ALIAS_ID" ]]; then
  ALIAS_ID=$(aws "${BA[@]}" create-agent-alias --agent-id "$ID" --agent-alias-name "$ALIAS" --tags ManagedBy=mcpdemo | jq -r .agentAlias.agentAliasId)
else
  # Point the alias at a fresh version of the prepared draft.
  aws "${BA[@]}" update-agent-alias --agent-id "$ID" --agent-alias-id "$ALIAS_ID" --agent-alias-name "$ALIAS" >/dev/null
fi
for _ in $(seq 1 30); do
  s=$(aws "${BA[@]}" get-agent-alias --agent-id "$ID" --agent-alias-id "$ALIAS_ID" | jq -r .agentAlias.agentAliasStatus)
  [[ "$s" == PREPARED ]] && break; sleep 3
done
[[ "$s" == PREPARED ]] || die "alias $ALIAS_ID: $s"

set_env BEDROCK_AGENT_ID "$ID"
set_env BEDROCK_AGENT_ALIAS_ID "$ALIAS_ID"
ok "Bedrock Agent ready: $ID alias $ALIAS ($ALIAS_ID)"
echo "Use it:  AGENT_ENGINE=bedrock-agent TOOLS_VIA=agentcore-gateway make agent"
