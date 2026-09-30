#!/usr/bin/env bash
# Run the Angular chatbot on http://localhost:3000 (config generated from .env).
source "$(dirname "$0")/lib.sh"
need TENANT_ID CHAT_CLIENT_ID AGENT_API_CLIENT_ID AGENT_RUNTIME_ARN
cd "$REPO_ROOT/chatbot"
printf '{"tenantId":"%s","chatClientId":"%s","agentApiClientId":"%s"}\n' "$TENANT_ID" "$CHAT_CLIENT_ID" "$AGENT_API_CLIENT_ID" > public/config.json
[[ -d node_modules ]] || npm ci
exec npx ng serve --port 3000
