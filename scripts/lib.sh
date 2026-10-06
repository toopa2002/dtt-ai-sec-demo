# Common helpers for demo scripts. Source from other scripts.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$REPO_ROOT/.run"
mkdir -p "$RUN_DIR"
export PATH="$HOME/.local/node22/bin:$HOME/.dotnet:$HOME/.local/bin:$PATH" DOTNET_CLI_TELEMETRY_OPTOUT=1
if [[ -f "$REPO_ROOT/.env" ]]; then set -a; source "$REPO_ROOT/.env"; set +a; fi
REGISTRY=localhost:5000
NS=adapter
GATEWAY_LOCAL=http://localhost:8000
# The demo lives under one path prefix on the shared ngrok domain, so other apps can use the rest of it.
# Chatbot: https://$NGROK_DOMAIN/mcp/   MCP Gateway: https://$NGROK_DOMAIN/mcp/gw/...  (chatbot/angular.json baseHref matches)
PUBLIC_PATH=/mcp
CHAT_PUBLIC="https://${NGROK_DOMAIN:-}$PUBLIC_PATH/"
GATEWAY_PUBLIC="https://${NGROK_DOMAIN:-}$PUBLIC_PATH/gw"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[31m✘ %s\033[0m\n' "$*" >&2; exit 1; }
need() { for v in "$@"; do [[ -n "${!v:-}" ]] || die "$v is not set in .env"; done; }
token() { uv run --quiet --with msal "$REPO_ROOT/scripts/get_token.py" "$1" "$2"; }
# set_env KEY VALUE / unset_env KEY: edit .env in place (portable: no GNU-only `sed -i`).
unset_env() { local f="$REPO_ROOT/.env" t; t=$(mktemp); grep -v "^$1=" "$f" >"$t" || true; cat "$t" >"$f"; rm -f "$t"; }
set_env() { unset_env "$1"; echo "$1=$2" >>"$REPO_ROOT/.env"; }
