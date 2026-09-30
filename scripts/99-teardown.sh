#!/usr/bin/env bash
# Tear down demo resources.
#   99-teardown.sh            stop tunnel/port-forward, delete the 'adapter' namespace
#   99-teardown.sh --aws      also destroy the AgentCore runtime and the SSM secret
#   99-teardown.sh --entra    also delete the Entra apps (users are kept; remove them in Entra if wanted)
#   99-teardown.sh --k3s      also uninstall k3s and remove the local registry
source "$(dirname "$0")/lib.sh"
"$REPO_ROOT/scripts/tunnel.sh" stop || true
kubectl delete namespace "$NS" --ignore-not-found
for arg in "$@"; do
  case "$arg" in
    --aws)
      (cd "$REPO_ROOT/agent" && agentcore destroy --agent mcpdemo_agent --force) || warn "agentcore destroy failed"
      aws ssm delete-parameter --region "$AWS_REGION" --name /mcpdemo/agent-obo-secret 2>/dev/null || true
      sed -i '/^AGENT_RUNTIME_ARN=/d' "$REPO_ROOT/.env" ;;
    --entra) python3 "$REPO_ROOT/scripts/entra.py" teardown ;;
    --k3s)   docker rm -f registry 2>/dev/null || true; sudo /usr/local/bin/k3s-uninstall.sh ;;
    *) die "unknown option $arg" ;;
  esac
done
ok "teardown complete"
