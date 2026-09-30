#!/usr/bin/env bash
# Local image registry on localhost:5000 (idempotent).
source "$(dirname "$0")/lib.sh"
if docker ps --format '{{.Names}}' | grep -qx registry; then ok "registry running"; exit 0; fi
docker start registry 2>/dev/null || docker run -d --restart unless-stopped -p 5000:5000 --name registry registry:2.7
ok "registry on ${REGISTRY}"
