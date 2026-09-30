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
GATEWAY_PUBLIC="https://${NGROK_DOMAIN:-}"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[31m✘ %s\033[0m\n' "$*" >&2; exit 1; }
need() { for v in "$@"; do [[ -n "${!v:-}" ]] || die "$v is not set in .env"; done; }
token() { uv run --quiet --with msal "$REPO_ROOT/scripts/get_token.py" "$1" "$2"; }
