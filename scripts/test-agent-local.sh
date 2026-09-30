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

async def main():
    ctx = types.SimpleNamespace(request_headers={"Authorization": "Bearer " + os.environ["TOKEN"]})
    async for ev in agent.invoke({"prompt": os.environ["PROMPT"]}, ctx):
        if ev.get("type") == "hop":
            if ev["status"] == "start":
                continue
            target = f" [{ev['target']}]" if ev.get("target") else ""
            print(f"  {ev['status']:<7} {ev['from']:>7} -> {ev['to']:<8}{target:<15} {ev['label']:<32} {ev.get('ms', ''):>5}ms  "
                  + json.dumps(ev.get("detail", {}), ensure_ascii=False)[:220])
        else:
            print(json.dumps(ev, indent=1, ensure_ascii=False))

asyncio.run(main())
PY
done
