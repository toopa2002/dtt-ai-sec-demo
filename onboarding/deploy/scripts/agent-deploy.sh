#!/usr/bin/env bash
# Deploy the ISC Onboarding Agent runtime to Bedrock AgentCore and create the scoped IAM user the session API uses
# (research R2, R4). Pattern: scripts/agent-deploy.sh.
#   agent-deploy.sh            stage + deploy the runtime, grant it Bedrock + AgentCore Identity, create/refresh the
#                              IAM user onboarding-api and write its keys straight into Secret onboarding/onboarding-aws
#   agent-deploy.sh --delete   delete the runtime, the IAM user, and every onboarding-isc-* credential provider
source "$(dirname "$0")/lib.sh"
need AWS_REGION
BUILD="$RUN_DIR/onboarding-agent-build"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

if [[ "${1:-}" == --delete ]]; then
  step "Deleting runtime $ONB_RUNTIME_NAME"
  if [[ -d "$BUILD" ]]; then (cd "$BUILD" && agentcore destroy --agent "$ONB_RUNTIME_NAME" --force) || warn "runtime not deleted"; fi
  step "Deleting tenant credential providers"
  for p in $(aws bedrock-agentcore-control list-oauth2-credential-providers --region "$AWS_REGION" \
              --query "credentialProviders[?starts_with(name, 'onboarding-isc-')].name" --output text 2>/dev/null); do
    aws bedrock-agentcore-control delete-oauth2-credential-provider --region "$AWS_REGION" --name "$p" && ok "deleted $p"
  done
  aws bedrock-agentcore-control delete-workload-identity --region "$AWS_REGION" --name isc-onboarding-agent 2>/dev/null \
    && ok "deleted workload identity isc-onboarding-agent" || true
  step "Deleting IAM user $ONB_API_USER"
  for k in $(aws iam list-access-keys --user-name "$ONB_API_USER" --query 'AccessKeyMetadata[].AccessKeyId' --output text 2>/dev/null); do
    aws iam delete-access-key --user-name "$ONB_API_USER" --access-key-id "$k"
  done
  aws iam delete-user-policy --user-name "$ONB_API_USER" --policy-name onboarding-api 2>/dev/null || true
  aws iam delete-user --user-name "$ONB_API_USER" 2>/dev/null && ok "user deleted" || true
  kubectl -n "$ONB_NS" delete secret onboarding-aws --ignore-not-found >/dev/null
  unset_env ONBOARDING_AGENT_RUNTIME_ARN
  exit 0
fi

step "Resolving the Claude Haiku 4.5 inference profile in $AWS_REGION"
if [[ -z "${BEDROCK_MODEL_ID:-}" ]]; then
  profiles=$(aws bedrock list-inference-profiles --region "$AWS_REGION" \
    --query "inferenceProfileSummaries[?contains(inferenceProfileId, 'claude-haiku-4-5')].inferenceProfileId" --output text)
  BEDROCK_MODEL_ID=$(tr '\t' '\n' <<<"$profiles" | grep -m1 '^apac\.' || tr '\t' '\n' <<<"$profiles" | grep -m1 '^global\.' || true)
  [[ -n "$BEDROCK_MODEL_ID" ]] || die "no Claude Haiku 4.5 inference profile in $AWS_REGION"
  set_env BEDROCK_MODEL_ID "$BEDROCK_MODEL_ID"
fi
ok "model: $BEDROCK_MODEL_ID"

step "Staging the runtime (code + connector catalog)"
rm -rf "$BUILD/src" "$BUILD/catalog"; mkdir -p "$BUILD"
cp -R "$ONB_ROOT/agent/src" "$BUILD/src"
cp -R "$ONB_ROOT/catalog" "$BUILD/catalog"
find "$BUILD" -name __pycache__ -prune -exec rm -rf {} +
cat >"$BUILD/agentcore_entry.py" <<'PY'
"""AgentCore entry for the ISC Onboarding Agent (staged by onboarding/deploy/scripts/agent-deploy.sh)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
os.environ.setdefault("CATALOG_DIR", os.path.join(HERE, "catalog"))

from onboarding_agent.main import app  # noqa: E402

if __name__ == "__main__":
    app.run()
PY
(cd "$ONB_ROOT/agent" && uv export --frozen --no-dev --no-hashes --no-emit-project -q) >"$BUILD/requirements.txt"
ok "staged in $BUILD"

step "Deploying runtime $ONB_RUNTIME_NAME (IAM-authorised: only the session API invokes it)"
cd "$BUILD"
agentcore configure --non-interactive --name "$ONB_RUNTIME_NAME" --entrypoint agentcore_entry.py \
  --requirements-file requirements.txt --region "$AWS_REGION" --deployment-type direct_code_deploy \
  --runtime PYTHON_3_12 --idle-timeout 900 --disable-memory --disable-otel
export UV_NO_CACHE=1
agentcore deploy --agent "$ONB_RUNTIME_NAME" --auto-update-on-conflict \
  --env "AWS_REGION=$AWS_REGION" --env "BEDROCK_MODEL_ID=$BEDROCK_MODEL_ID" --env "CATALOG_DIR=catalog"
ARN=$(grep -oE 'agent_arn: arn:aws:bedrock-agentcore:[^ ]+' .bedrock_agentcore.yaml | head -1 | cut -d' ' -f2)
ROLE=$(grep -oE 'execution_role: arn:aws:iam::[^ ]+' .bedrock_agentcore.yaml | head -1 | cut -d' ' -f2)
[[ -n "$ARN" && -n "$ROLE" ]] || die "could not read runtime ARN / execution role from .bedrock_agentcore.yaml"
set_env ONBOARDING_AGENT_RUNTIME_ARN "$ARN"
ok "$ARN"

step "Execution role: Claude Haiku on Bedrock + ISC tokens from AgentCore Identity (onboarding-isc-* only)"
aws iam put-role-policy --role-name "${ROLE##*/}" --policy-name onboarding-agent --policy-document "$(cat <<JSON
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],
  "Resource":["arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5*","arn:aws:bedrock:*:$ACCOUNT:inference-profile/$BEDROCK_MODEL_ID"]},
 {"Effect":"Allow","Action":["bedrock-agentcore:GetResourceOauth2Token","bedrock-agentcore:GetWorkloadAccessToken"],
  "Resource":["arn:aws:bedrock-agentcore:$AWS_REGION:$ACCOUNT:workload-identity-directory/default*",
              "arn:aws:bedrock-agentcore:$AWS_REGION:$ACCOUNT:token-vault/default*"]},
 {"Effect":"Allow","Action":"secretsmanager:GetSecretValue",
  "Resource":"arn:aws:secretsmanager:$AWS_REGION:$ACCOUNT:secret:bedrock-agentcore-identity!default/oauth2/onboarding-isc-*"}]}
JSON
)"
ok "role ${ROLE##*/}"

step "IAM user $ONB_API_USER: invoke this runtime + manage onboarding-isc-* credential providers"
aws iam get-user --user-name "$ONB_API_USER" >/dev/null 2>&1 \
  || aws iam create-user --user-name "$ONB_API_USER" --tags Key=ManagedBy,Value=isc-onboarding >/dev/null
aws iam put-user-policy --user-name "$ONB_API_USER" --policy-name onboarding-api --policy-document "$(cat <<JSON
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":"bedrock-agentcore:InvokeAgentRuntime","Resource":["$ARN","$ARN/*"]},
 {"Effect":"Allow","Action":["bedrock-agentcore:CreateOauth2CredentialProvider","bedrock-agentcore:UpdateOauth2CredentialProvider",
   "bedrock-agentcore:DeleteOauth2CredentialProvider","bedrock-agentcore:GetOauth2CredentialProvider"],
  "Resource":["arn:aws:bedrock-agentcore:$AWS_REGION:$ACCOUNT:token-vault/default",
              "arn:aws:bedrock-agentcore:$AWS_REGION:$ACCOUNT:token-vault/default/oauth2credentialprovider/*"]},
 {"Effect":"Allow","Action":["bedrock-agentcore:CreateTokenVault","bedrock-agentcore:GetTokenVault"],
  "Resource":"arn:aws:bedrock-agentcore:$AWS_REGION:$ACCOUNT:token-vault/default"},
 {"Effect":"Allow","Action":["secretsmanager:CreateSecret","secretsmanager:PutSecretValue","secretsmanager:DeleteSecret",
   "secretsmanager:DescribeSecret","secretsmanager:TagResource"],
  "Resource":"arn:aws:secretsmanager:$AWS_REGION:$ACCOUNT:secret:bedrock-agentcore-identity!default/oauth2/onboarding-isc-*"}]}
JSON
)"
# One active key, rotated on every deploy. The key goes from IAM straight into the cluster Secret: never printed.
for k in $(aws iam list-access-keys --user-name "$ONB_API_USER" --query 'AccessKeyMetadata[].AccessKeyId' --output text); do
  aws iam delete-access-key --user-name "$ONB_API_USER" --access-key-id "$k"
done
kubectl get namespace "$ONB_NS" >/dev/null 2>&1 || kubectl create namespace "$ONB_NS" >/dev/null
aws iam create-access-key --user-name "$ONB_API_USER" --output json \
  | jq -r '.AccessKey | "AWS_ACCESS_KEY_ID=\(.AccessKeyId)\nAWS_SECRET_ACCESS_KEY=\(.SecretAccessKey)"' \
  | kubectl -n "$ONB_NS" create secret generic onboarding-aws --from-env-file=/dev/stdin --dry-run=client -o yaml \
  | kubectl apply -f - >/dev/null
ok "Secret $ONB_NS/onboarding-aws updated (new key; the API picks it up on restart)"

step "Workload identity isc-onboarding-agent (the agent's SailPoint tokens; the runtime's own is service-linked)"
aws bedrock-agentcore-control get-workload-identity --region "$AWS_REGION" --name isc-onboarding-agent >/dev/null 2>&1 \
  || aws bedrock-agentcore-control create-workload-identity --region "$AWS_REGION" --name isc-onboarding-agent >/dev/null
ok "isc-onboarding-agent"

step "Log retention: 1 day"
for g in $(aws logs describe-log-groups --region "$AWS_REGION" --log-group-name-prefix "/aws/bedrock-agentcore/runtimes/$ONB_RUNTIME_NAME" \
  --query 'logGroups[].logGroupName' --output text); do
  aws logs put-retention-policy --region "$AWS_REGION" --log-group-name "$g" --retention-in-days 1
done
ok "agent deployed; run make onboarding-up to point the API at it"
