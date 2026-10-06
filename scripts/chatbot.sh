#!/usr/bin/env bash
# Run the Angular chatbot on http://localhost:3000/mcp/ (public: $CHAT_PUBLIC; config generated from .env).
source "$(dirname "$0")/lib.sh"
need TENANT_ID CHAT_CLIENT_ID AGENT_API_CLIENT_ID AGENT_RUNTIME_ARN
cd "$REPO_ROOT/chatbot"
# The agent runtime's execution role, shown on the traffic panel's agent node (looked up once, then kept in .env).
if [[ -z "${AGENT_ROLE_ARN:-}" && -n "${AGENT_RUNTIME_ARN:-}" ]]; then
  AGENT_ROLE_ARN=$(aws bedrock-agentcore-control get-agent-runtime --region "$AWS_REGION" \
    --agent-runtime-id "${AGENT_RUNTIME_ARN##*/}" --query roleArn --output text 2>/dev/null || true)
  [[ -z "$AGENT_ROLE_ARN" || "$AGENT_ROLE_ARN" == None ]] || set_env AGENT_ROLE_ARN "$AGENT_ROLE_ARN"
fi
GATEWAY_ROLE_ARN=${AGENT_ROLE_ARN:+$(cut -d: -f1-5 <<<"$AGENT_ROLE_ARN"):role/mcpdemo-agentcore-gateway-role}
jq -n --arg t "$TENANT_ID" --arg c "$CHAT_CLIENT_ID" --arg a "$AGENT_API_CLIENT_ID" --arg tv "${TOOLS_VIA:-mcp-gateway}" \
  --arg av "${AGENT_VIA:-direct}" --arg ar "${AGENT_ROLE_ARN:-}" --arg gr "${GATEWAY_ROLE_ARN:-}" --arg g "$GATEWAY_API_CLIENT_ID" \
  --arg ae "${AGENT_ENGINE:-claude}" --arg ba "${BEDROCK_AGENT_ID:-}" \
  --arg rt "$AGENT_RUNTIME_ARN" --arg bn "${BEDROCK_AGENT_ID:+mcpdemo-tools-agent}" --arg ad "${MCP_ADAPTERS:-weather hr-directory}" \
  '{tenantId:$t, chatClientId:$c, agentApiClientId:$a, toolsVia:$tv, agentVia:$av, agentEngine:$ae, bedrockAgentId:$ba,
    agentRoleArn:$ar, gatewayRoleArn:$gr, gatewayApiClientId:$g,
    agentRuntimeArn:$rt, bedrockAgentName:$bn, mcpAdapters:($ad | gsub(","; " ") | split(" ") | map(select(. != "")))}' > public/config.json
[[ "${AGENT_VIA:-direct}" != agentcore-gateway ]] || need AGENT_GATEWAY_URL
[[ -d node_modules ]] || npm ci
# 127.0.0.1: the edge container (Docker Desktop) reaches host ports over IPv4; "localhost" may bind ::1 only.
exec npx ng serve --host 127.0.0.1 --port 3000
