#!/usr/bin/env bash
# Docker Desktop (macOS) alternative to 02-registry.sh + 03-build-push.sh: no .NET SDK on the host and no
# registry. Images are built into Docker Desktop's image store under the names the manifests already use
# (localhost:5000/...); Docker Desktop's Kubernetes (kind mode) pulls them from there through its built-in mirror.
source "$(dirname "$0")/lib.sh"
GW="$REPO_ROOT/mcp-gateway"
SDK=mcr.microsoft.com/dotnet/sdk:8.0
OUT="$RUN_DIR/images"
mkdir -p "$OUT"
[[ -f "$GW/dotnet/Microsoft.McpGateway.Service/src/Microsoft.McpGateway.Service.csproj" ]] \
  || git -C "$REPO_ROOT" submodule update --init mcp-gateway

step "Applying gateway patch to a clean submodule"
git -C "$GW" checkout -- .
git -C "$GW" apply "$REPO_ROOT/patches/0001-entra-auth-in-development.patch"
trap 'git -C "$GW" checkout -- .' EXIT

publish() {  # publish <project dir under dotnet/> <image name> [extra msbuild args]
  local proj=$1 name=$2; shift 2
  step "Publishing $name (in $SDK)"
  docker run --rm -v "$GW:/src" -v "$OUT:/out" -w /src "$SDK" \
    dotnet publish "dotnet/$proj/src/$proj.csproj" -c Release -r linux-x64 -t:PublishContainer \
      -p:ContainerRepository="$name" -p:ContainerImageTag=latest -p:ContainerArchiveOutputPath="/out/$name.tar.gz" "$@" \
    | grep -E 'error|warn.*CONTAINER|Pushed|archive|Built' || true
  [[ -s "$OUT/$name.tar.gz" ]] || die "$name: no image archive produced"
  docker load -i "$OUT/$name.tar.gz" | tail -1
  docker tag "$name:latest" "$REGISTRY/$name:latest" && docker rmi "$name:latest" >/dev/null
  rm -f "$OUT/$name.tar.gz"
  ok "$REGISTRY/$name:latest"
}
publish Microsoft.McpGateway.Service microsoft-mcpgateway-service -p:BuildPortal=false
publish Microsoft.McpGateway.Tools microsoft-mcpgateway-tools

for s in weather-mcp hr-directory-mcp; do
  step "Building $s"
  docker build -q -t "$REGISTRY/$s:1.1.0" "$REPO_ROOT/servers/$s" >/dev/null
  ok "$REGISTRY/$s:1.1.0"
done

step "Freeing disk: removing the .NET SDK image"
docker rmi "$SDK" >/dev/null 2>&1 || true
docker images --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' | grep "^$REGISTRY/"
