#!/usr/bin/env bash
# Build and push the gateway (with the Entra-in-Development patch; portal UI skipped) and both MCP servers.
source "$(dirname "$0")/lib.sh"
GW="$REPO_ROOT/mcp-gateway"

step "Applying gateway patch to a clean submodule"
git -C "$GW" checkout -- .
git -C "$GW" apply "$REPO_ROOT/patches/0001-entra-auth-in-development.patch"
trap 'git -C "$GW" checkout -- .' EXIT

step "Publishing gateway + tool router images"
dotnet publish "$GW/dotnet/Microsoft.McpGateway.Service/src/Microsoft.McpGateway.Service.csproj" -c Release /p:PublishProfile=localhost_5000.pubxml /p:BuildPortal=false
dotnet publish "$GW/dotnet/Microsoft.McpGateway.Tools/src/Microsoft.McpGateway.Tools.csproj" -c Release /p:PublishProfile=localhost_5000.pubxml

for s in weather-mcp hr-directory-mcp; do
  step "Building $s"
  docker build -t "$REGISTRY/$s:1.0.0" "$REPO_ROOT/servers/$s"
  docker push "$REGISTRY/$s:1.0.0"
done
curl -s "http://$REGISTRY/v2/_catalog"; echo
ok "images pushed"
