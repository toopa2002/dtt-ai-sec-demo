#!/usr/bin/env bash
# Build the onboarding app image (API + web build, one container) into localhost:5000
# (pattern: scripts/03-build-docker.sh).
# Docker Desktop Kubernetes pulls localhost:5000/* through its mirror; on k3s the images are also pushed to the
# local registry from scripts/02-registry.sh.
source "$(dirname "$0")/lib.sh"
step "Building onboarding-api (API + web)"
docker build -q -t "$REGISTRY/onboarding-api:latest" -f "$ONB_ROOT/api/Dockerfile" "$ONB_ROOT" >/dev/null
ok "$REGISTRY/onboarding-api:latest"
step "Building onboarding-mongo (slim MongoDB 8.0)"
docker build -q -t "$REGISTRY/onboarding-mongo:8.0" "$ONB_ROOT/deploy/mongo" >/dev/null
ok "$REGISTRY/onboarding-mongo:8.0"
if [[ "$(kubectl config current-context 2>/dev/null)" != docker-desktop ]]; then
  # k3d on macOS: the cluster mirrors localhost:5000 to the mcpdemo-registry container, published on another host port
  # (5000 is AirPlay). PUSH_REGISTRY overrides.
  push=${PUSH_REGISTRY:-$(docker port mcpdemo-registry 5000/tcp 2>/dev/null | sed -nE 's/^0\.0\.0\.0:([0-9]+)$/localhost:\1/p' | head -1)}
  push=${push:-$REGISTRY}
  for i in onboarding-api:latest onboarding-mongo:8.0; do
    [[ "$push" == "$REGISTRY" ]] || docker tag "$REGISTRY/$i" "$push/$i"
    docker push -q "$push/$i" >/dev/null && ok "pushed $i to $push"
    [[ "$push" == "$REGISTRY" ]] || docker rmi "$push/$i" >/dev/null
  done
fi
docker image prune -f >/dev/null
