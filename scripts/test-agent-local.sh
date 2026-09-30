#!/usr/bin/env bash
# Run agent/agent.py locally against the REAL gateway, Entra (OBO), SSM and Bedrock, before deploying.
# Uses demo-CLI-issued persona tokens, so the chatbot-only (azp) check is pointed at the CLI app here.
source "$(dirname "$0")/lib.sh"
need CLI_CLIENT_ID NGROK_DOMAIN
PROMPT=${PROMPT:-"What's the weather in Bangkok, and who is Somchai's manager?"}
PERSONAS=${*:-full weather-only}
cd "$REPO_ROOT/agent"
for p in $PERSONAS; do
  step "$p: $PROMPT"
  TOKEN=$(token "$p" agent) PROMPT="$PROMPT" CHAT_CLIENT_ID="$CLI_CLIENT_ID" GATEWAY_URL="https://$NGROK_DOMAIN" \
    uv run --quiet --no-cache --with-requirements requirements.txt python - <<'PY'
import asyncio, json, os, types
import agent
ctx = types.SimpleNamespace(request_headers={"Authorization": "Bearer " + os.environ["TOKEN"]})
print(json.dumps(asyncio.run(agent.invoke({"prompt": os.environ["PROMPT"]}, ctx)), indent=1, ensure_ascii=False))
PY
done
