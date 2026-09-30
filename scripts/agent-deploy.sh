#!/usr/bin/env bash
# Deploy the agent to Bedrock AgentCore Runtime (ap-southeast-1) with an Entra JWT authorizer.
#   scripts/agent-deploy.sh            configure + deploy (creates the OBO secret if missing)
#   scripts/agent-deploy.sh --rotate   also rotate the OBO client secret
source "$(dirname "$0")/lib.sh"
need AWS_REGION TENANT_ID AGENT_API_CLIENT_ID GATEWAY_API_CLIENT_ID CHAT_CLIENT_ID NGROK_DOMAIN
AGENT_NAME=mcpdemo_agent
PARAM=/mcpdemo/agent-obo-secret
cd "$REPO_ROOT/agent"

step "Resolving the cheapest Claude Haiku 4.5 model id in $AWS_REGION"
if [[ -z "${BEDROCK_MODEL_ID:-}" ]]; then
  profiles=$(aws bedrock list-inference-profiles --region "$AWS_REGION" \
    --query "inferenceProfileSummaries[?contains(inferenceProfileId, 'claude-haiku-4-5')].inferenceProfileId" --output text)
  # Prefer the in-geography APAC profile, then global.
  BEDROCK_MODEL_ID=$(tr '\t' '\n' <<<"$profiles" | grep -m1 '^apac\.' || tr '\t' '\n' <<<"$profiles" | grep -m1 '^global\.' || true)
  [[ -n "$BEDROCK_MODEL_ID" ]] || die "no Claude Haiku 4.5 inference profile in $AWS_REGION; enable model access in the Bedrock console"
  sed -i "/^BEDROCK_MODEL_ID=/d" "$REPO_ROOT/.env"; echo "BEDROCK_MODEL_ID=$BEDROCK_MODEL_ID" >>"$REPO_ROOT/.env"
fi
ok "model: $BEDROCK_MODEL_ID"

step "OBO client secret for mcpdemo-agent-api -> SSM $PARAM"
if [[ "${1:-}" == --rotate ]] || ! aws ssm get-parameter --region "$AWS_REGION" --name "$PARAM" >/dev/null 2>&1; then
  end=$(date -u -d '+30 days' +%Y-%m-%dT%H:%M:%SZ)
  # The secret goes straight from Entra into SSM; it is never printed or written to disk.
  az ad app credential reset --id "$AGENT_API_CLIENT_ID" --append --display-name agentcore-obo --end-date "$end" \
    --query password -o tsv |
    aws ssm put-parameter --region "$AWS_REGION" --name "$PARAM" --type SecureString --overwrite --value file:///dev/stdin >/dev/null
  ok "secret stored (expires $end)"
else
  ok "secret already in SSM"
fi

step "Configuring AgentCore runtime '$AGENT_NAME'"
AUTHZ=$(printf '{"customJWTAuthorizer":{"discoveryUrl":"https://login.microsoftonline.com/%s/v2.0/.well-known/openid-configuration","allowedAudience":["%s","api://%s"]}}' \
  "$TENANT_ID" "$AGENT_API_CLIENT_ID" "$AGENT_API_CLIENT_ID")
agentcore configure --non-interactive --name "$AGENT_NAME" --entrypoint agent.py --requirements-file requirements.txt \
  --region "$AWS_REGION" --deployment-type direct_code_deploy --runtime PYTHON_3_12 \
  --authorizer-config "$AUTHZ" --request-header-allowlist Authorization \
  --idle-timeout 300 --disable-memory --disable-otel

step "Deploying"
# Bypass the local uv cache for the ARM64 cross-build (a corrupt cache entry yields "Invalid Wheel-Version").
export UV_NO_CACHE=1
agentcore deploy --agent "$AGENT_NAME" --auto-update-on-conflict \
  --env "AWS_REGION=$AWS_REGION" --env "BEDROCK_MODEL_ID=$BEDROCK_MODEL_ID" --env "TENANT_ID=$TENANT_ID" \
  --env "AGENT_API_CLIENT_ID=$AGENT_API_CLIENT_ID" --env "CHAT_CLIENT_ID=$CHAT_CLIENT_ID" \
  --env "GATEWAY_API_CLIENT_ID=$GATEWAY_API_CLIENT_ID" --env "GATEWAY_URL=https://$NGROK_DOMAIN" \
  --env "MCP_ADAPTERS=weather,hr-directory" --env "OBO_SECRET_PARAM=$PARAM"

step "Granting the execution role read access to the OBO secret"
# Read from the toolkit's config file (the `agentcore status` box wraps long ARNs).
ARN=$(grep -oE 'agent_arn: arn:aws:bedrock-agentcore:[^ ]+' .bedrock_agentcore.yaml | head -1 | cut -d' ' -f2)
ROLE=$(grep -oE 'execution_role: arn:aws:iam::[^ ]+' .bedrock_agentcore.yaml | head -1 | cut -d' ' -f2)
[[ -n "$ARN" && -n "$ROLE" ]] || die "could not read runtime ARN / execution role from .bedrock_agentcore.yaml"
ACCOUNT=$(cut -d: -f5 <<<"$ARN")
aws iam put-role-policy --role-name "${ROLE##*/}" --policy-name mcpdemo-obo-secret --policy-document "$(cat <<JSON
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":"ssm:GetParameter","Resource":"arn:aws:ssm:$AWS_REGION:$ACCOUNT:parameter$PARAM"},
 {"Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],
  "Resource":["arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5*","arn:aws:bedrock:*:$ACCOUNT:inference-profile/$BEDROCK_MODEL_ID"]}]}
JSON
)"
sed -i "/^AGENT_RUNTIME_ARN=/d" "$REPO_ROOT/.env"; echo "AGENT_RUNTIME_ARN=$ARN" >>"$REPO_ROOT/.env"

step "Log retention: 1 day"
for g in $(aws logs describe-log-groups --region "$AWS_REGION" --log-group-name-prefix /aws/bedrock-agentcore/runtimes/ \
  --query 'logGroups[].logGroupName' --output text); do
  aws logs put-retention-policy --region "$AWS_REGION" --log-group-name "$g" --retention-in-days 1
done
ok "agent deployed: $ARN"
