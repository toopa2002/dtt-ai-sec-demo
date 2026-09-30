#!/usr/bin/env bash
# Install/start k3s (no Traefik, no ServiceLB) and trust the local HTTP registry. Needs sudo.
source "$(dirname "$0")/lib.sh"
if ! command -v k3s >/dev/null; then
  step "Installing k3s"
  curl -sfL https://get.k3s.io | sudo sh -s - --write-kubeconfig-mode 644 --disable traefik --disable servicelb
fi
step "Trusting ${REGISTRY} (plain HTTP) in k3s containerd"
sudo mkdir -p /etc/rancher/k3s
sudo tee /etc/rancher/k3s/registries.yaml >/dev/null <<YAML
mirrors:
  "${REGISTRY}":
    endpoint:
      - "http://${REGISTRY}"
YAML
sudo systemctl restart k3s
mkdir -p "$HOME/.kube"
cp /etc/rancher/k3s/k3s.yaml "$HOME/.kube/config" && chmod 600 "$HOME/.kube/config"
kubectl wait --for=condition=Ready node --all --timeout=120s
ok "k3s ready"
