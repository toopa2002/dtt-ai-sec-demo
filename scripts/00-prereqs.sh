#!/usr/bin/env bash
# Check tools needed by the demo; print how to install anything missing.
source "$(dirname "$0")/lib.sh"
missing=0
check() { # name, command, install hint
  if eval "$2" >/dev/null 2>&1; then ok "$1"; else warn "$1 missing -> $3"; missing=1; fi
}
[[ "$(ps -p 1 -o comm=)" == systemd ]] && ok "WSL systemd" || { warn "systemd off -> add [boot] systemd=true to /etc/wsl.conf, then 'wsl --shutdown'"; missing=1; }
check docker     "docker info"                        "install Docker Engine or enable Docker Desktop WSL integration"
check dotnet-8   "dotnet --list-sdks | grep -q '^8\.'" "curl -sSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel 8.0"
check node-22    "node -v | grep -Eq '^v(2[2-9]|[3-9][0-9])'" "curl -fsSL https://fnm.vercel.app/install | bash && fnm install 22 && fnm default 22"
check uv         "uv --version"                       "curl -LsSf https://astral.sh/uv/install.sh | sh"
check k3s        "command -v k3s"                     "make k3s  (runs scripts/01-k3s-up.sh, needs sudo)"
check kubectl    "kubectl version --client"           "installed with k3s"
check az         "az version"                         "https://learn.microsoft.com/cli/azure/install-azure-cli-linux"
check az-login   "az account show --query tenantId -o tsv | grep -q 2aa0aac8-a644-4603-9530-36541a8e3c79" "az login --tenant 2aa0aac8-a644-4603-9530-36541a8e3c79 --allow-no-subscriptions"
check aws        "aws --version"                      "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
check aws-login  "aws sts get-caller-identity"        "aws configure (or aws sso login); region ap-southeast-1"
check agentcore  "agentcore --help"                   "uv tool install bedrock-agentcore-starter-toolkit"
check ngrok      "ngrok config check"                 "ngrok config add-authtoken <token>"
exit $missing
