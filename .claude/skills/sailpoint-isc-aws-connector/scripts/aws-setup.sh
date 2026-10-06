#!/usr/bin/env bash
# COMMAND 1 — AWS side for the SailPoint ISC "Amazon Web Services SaaS" connector.
#
# Creates the IAM role SailPoint assumes (same name + same External ID in every account):
#   * management account: CloudFormation stack (role + aggregation + organization [+ provisioning] policies)
#   * member accounts:    service-managed CloudFormation StackSet (role + aggregation [+ provisioning] policies)
# The role trusts SailPoint's ciem_universal role, conditioned on the External ID ISC generated for the source.
#
# Dry-run by default: preflight checks run (read-only), every change is printed instead of executed.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

usage() {
  cat <<'EOF'
Usage: aws-setup.sh --external-id <id> [options]

Required:
  --external-id ID          External ID shown on the ISC source's Connection Settings page

Options:
  --role-name NAME          IAM role name, identical in every account (default: SailPointAWSRole)
  --targets T               Member accounts to cover via StackSet:
                              org            all accounts in the organization (default)
                              ou-xxxx,...    these OUs (and their children)
                              111122223333,… only these account IDs
                              none           management account only (single-account org)
  --provisioning            Also grant IAM write permissions (default: read-only aggregation)
  --bedrock                 Also grant Amazon Bedrock Agents discovery (SailPoint Machine Identity Governance)
  --agentcore               Also grant Amazon Bedrock AgentCore agents discovery
  --partition P             aws (default) | aws-us-gov — picks SailPoint's documented principal
  --principals ARN,ARN      SailPoint role ARN(s) to trust instead of the partition default. Repeat the default
                            here too if you add one (e.g. a demo environment's ciem_universal).
  --region R                Region for the stack / StackSet home (default: profile region or us-east-1)
  --profile P               AWS CLI profile for the MANAGEMENT account
  --stack-name N            CloudFormation stack/StackSet name (default: SailPoint-ISC-AWS)
  --print-template          Print the generated CloudFormation template and exit
  --skip-preflight          Don't call AWS for preflight checks (needs --mgmt-account-id)
  --mgmt-account-id ID      Management account ID (only with --skip-preflight)
  --out FILE                Where to write the JSON summary (default: ./sailpoint-aws-setup.json)
  --apply                   Execute the changes (default is dry-run)
EOF
}

EXTERNAL_ID="" ROLE_NAME="SailPointAWSRole" TARGETS="org" PROVISIONING=false BEDROCK=false AGENTCORE=false PARTITION="aws"
PRINCIPALS="" REGION="" PROFILE="" STACK_NAME="SailPoint-ISC-AWS" PRINT_TEMPLATE=0 SKIP_PREFLIGHT=0 MGMT_ID=""
OUT="./sailpoint-aws-setup.json"
while (($#)); do
  case "$1" in
    --external-id) EXTERNAL_ID="$2"; shift ;;
    --role-name) ROLE_NAME="$2"; shift ;;
    --targets) TARGETS="$2"; shift ;;
    --provisioning) PROVISIONING=true ;;
    --bedrock) BEDROCK=true ;;
    --agentcore) AGENTCORE=true ;;
    --partition) PARTITION="$2"; shift ;;
    --principals) PRINCIPALS="$2"; shift ;;
    --region) REGION="$2"; shift ;;
    --profile) PROFILE="$2"; shift ;;
    --stack-name) STACK_NAME="$2"; shift ;;
    --print-template) PRINT_TEMPLATE=1 ;;
    --skip-preflight) SKIP_PREFLIGHT=1 ;;
    --mgmt-account-id) MGMT_ID="$2"; shift ;;
    --out) OUT="$2"; shift ;;
    --apply) APPLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "unknown option: $1" ;;
  esac
  shift
done

need_cmd jq
case "$PARTITION" in
  aws)        DEFAULT_PRINCIPAL="arn:aws:iam::874540850173:role/ciem_universal" ;;
  aws-us-gov) DEFAULT_PRINCIPAL="arn:aws-us-gov:iam::229634586956:role/ciem_universal" ;;
  *) die "--partition must be aws or aws-us-gov" ;;
esac
PRINCIPALS="${PRINCIPALS:-$DEFAULT_PRINCIPAL}"
IFS=, read -ra _p <<<"$PRINCIPALS"
for a in "${_p[@]}"; do [[ "$a" =~ ^arn:aws(-us-gov)?:iam::[0-9]{12}:role/.+ ]] || die "not a role ARN in --principals: $a"; done
[[ "$ROLE_NAME" =~ ^[A-Za-z0-9+=,.@_-]{1,64}$ ]] || die "--role-name must be a plain IAM role name (not an ARN or path)"

# CloudFormation template, built from the policy JSON in assets/ so the policies live in one place.
template() {
  jq -n \
    --slurpfile agg "$ASSETS/policy-aggregation.json" \
    --slurpfile org "$ASSETS/policy-organization-mgo.json" \
    --slurpfile prov "$ASSETS/policy-provisioning-mgo.json" \
    --slurpfile bedrock "$ASSETS/policy-bedrock-agent-discovery.json" \
    --slurpfile agentcore "$ASSETS/policy-bedrock-agentcore-discovery.json" '
  def inline(name; doc; cond): {"Fn::If": [cond, {PolicyName: name, PolicyDocument: doc}, {Ref: "AWS::NoValue"}]};
  {
    AWSTemplateFormatVersion: "2010-09-09",
    Description: "IAM role assumed by the SailPoint ISC Amazon Web Services SaaS connector",
    Parameters: {
      RoleName:                  {Type: "String", Default: "SailPointAWSRole"},
      ExternalId:                {Type: "String", NoEcho: true, MinLength: 2},
      SailPointPrincipals:       {Type: "CommaDelimitedList"},
      IncludeOrganizationPolicy: {Type: "String", AllowedValues: ["true","false"], Default: "false"},
      IncludeProvisioning:       {Type: "String", AllowedValues: ["true","false"], Default: "false"},
      IncludeBedrock:            {Type: "String", AllowedValues: ["true","false"], Default: "false"},
      IncludeAgentCore:          {Type: "String", AllowedValues: ["true","false"], Default: "false"}
    },
    Conditions: {
      Org:  {"Fn::Equals": [{Ref: "IncludeOrganizationPolicy"}, "true"]},
      Prov: {"Fn::Equals": [{Ref: "IncludeProvisioning"}, "true"]},
      Bedrock:   {"Fn::Equals": [{Ref: "IncludeBedrock"}, "true"]},
      AgentCore: {"Fn::Equals": [{Ref: "IncludeAgentCore"}, "true"]}
    },
    Resources: {
      SailPointRole: {
        Type: "AWS::IAM::Role",
        Properties: {
          RoleName: {Ref: "RoleName"},
          Description: "Assumed by SailPoint ISC (AWS SaaS connector)",
          AssumeRolePolicyDocument: {
            Version: "2012-10-17",
            Statement: [{
              Effect: "Allow",
              Principal: {AWS: {Ref: "SailPointPrincipals"}},
              Action: "sts:AssumeRole",
              Condition: {StringEquals: {"sts:ExternalId": {Ref: "ExternalId"}}}
            }]
          },
          Policies: [
            {PolicyName: "SPAggregationPolicy", PolicyDocument: $agg[0]},
            inline("SPOrganizationPolicy"; $org[0]; "Org"),
            inline("SPProvisioningPolicy"; $prov[0]; "Prov"),
            inline("SPBedrockAgentDiscoveryPolicy"; $bedrock[0]; "Bedrock"),
            inline("SPBedrockAgentCoreDiscoveryPolicy"; $agentcore[0]; "AgentCore")
          ],
          Tags: [{Key: "ManagedBy", Value: "sailpoint-isc-aws-connector-skill"}]
        }
      }
    },
    Outputs: {RoleArn: {Value: {"Fn::GetAtt": ["SailPointRole", "Arn"]}}}
  }'
}

if (( PRINT_TEMPLATE )); then template; exit 0; fi
[[ -n "$EXTERNAL_ID" ]] || { usage; die "--external-id is required (copy it from the ISC source's Connection Settings)"; }

need_cmd aws
AWS=(aws)
[[ -n "$PROFILE" ]] && AWS+=(--profile "$PROFILE")
[[ -n "$REGION" ]] || REGION="$("${AWS[@]}" configure get region 2>/dev/null || true)"
REGION="${REGION:-us-east-1}"
AWS+=(--region "$REGION")

# --- Preflight (read-only) -----------------------------------------------------------------------
if (( SKIP_PREFLIGHT )); then
  is_account_id "$MGMT_ID" || die "--skip-preflight needs --mgmt-account-id <12 digits>"
  warn "preflight skipped: not checking Organizations, StackSets trusted access or caller identity"
  ROOT_ID="r-xxxx"
else
  step "Preflight"
  CALLER="$("${AWS[@]}" sts get-caller-identity --query Account --output text)" || die "AWS credentials not usable"
  ORG="$("${AWS[@]}" organizations describe-organization --output json 2>/dev/null)" \
    || die "AWS Organizations is not enabled for $CALLER. The SaaS connector needs Organizations even for one account
   (enable it: aws organizations create-organization --feature-set ALL)."
  MGMT_ID="$(jq -r .Organization.MasterAccountId <<<"$ORG")"
  [[ "$CALLER" == "$MGMT_ID" ]] || die "profile is account $CALLER, but the management account is $MGMT_ID. Use a management-account profile."
  [[ "$(jq -r .Organization.FeatureSet <<<"$ORG")" == "ALL" ]] || die "Organizations must have all features enabled"
  ok "management account $MGMT_ID, all features enabled"
  # Never take over a role someone else made: the stack would fail on it, or rewrite a trust policy in use.
  if existing="$("${AWS[@]}" iam get-role --role-name "$ROLE_NAME" --output json 2>/dev/null)"; then
    # IAM roles don't carry aws:cloudformation:* tags, so ask the stack whether it owns the role.
    owned="$("${AWS[@]}" cloudformation describe-stack-resources --stack-name "$STACK_NAME" \
              --query "StackResources[?ResourceType=='AWS::IAM::Role' && PhysicalResourceId=='$ROLE_NAME'] | length(@)" \
              --output text 2>/dev/null || echo 0)"
    [[ "$owned" == 1 ]] || die "role $ROLE_NAME already exists in $MGMT_ID (not created by stack $STACK_NAME) and trusts
   $(jq -c '.Role.AssumeRolePolicyDocument.Statement[0].Principal' <<<"$existing").
   It is probably used by another SailPoint tenant or CIEM. Choose a different --role-name."
    ok "role $ROLE_NAME exists from stack $STACK_NAME; will update it"
  fi
  # Another role may already trust this tenant (e.g. built from the UI's CloudFormation template). Not an error —
  # but worth telling the user, since reusing it may be all they need, or it may be broader than they expect.
  others="$("${AWS[@]}" iam list-roles --output json \
    | jq -r --arg ext "$EXTERNAL_ID" --arg me "$ROLE_NAME" '.Roles[]
        | select(.RoleName != $me) | select((.AssumeRolePolicyDocument|tostring)|contains($ext)) | .RoleName')"
  [[ -z "$others" ]] || warn "already trusting this External ID: $(tr '\n' ' ' <<<"$others")— check whether one of these should be reused instead (iam list-role-policies / list-attached-role-policies)"
  ROOT_ID="$("${AWS[@]}" organizations list-roots --query 'Roots[0].Id' --output text)"
  if [[ "$TARGETS" != none ]]; then
    if "${AWS[@]}" organizations list-aws-service-access-for-organization \
         --query "EnabledServicePrincipals[?ServicePrincipal=='member.org.stacksets.cloudformation.amazonaws.com']" \
         --output text | grep -q .; then
      ok "StackSets trusted access enabled"
    else
      warn "StackSets trusted access is not enabled. Enable it, then rerun:"
      warn "  aws cloudformation activate-organizations-access${PROFILE:+ --profile $PROFILE} --region $REGION"
      (( APPLY )) && die "cannot deploy the StackSet without trusted access"
    fi
  fi
fi

# --- Work out StackSet deployment targets --------------------------------------------------------
csv_json() { jq -cR 'split(",") | map(select(. != ""))' <<<"$1"; }
TARGET_ARGS=() COVERED="none"
case "$TARGETS" in
  none) ;;
  org) TARGET_ARGS=(--deployment-targets "{\"OrganizationalUnitIds\":[\"$ROOT_ID\"]}"); COVERED="organization root $ROOT_ID" ;;
  ou-*|r-*) TARGET_ARGS=(--deployment-targets "{\"OrganizationalUnitIds\":$(csv_json "$TARGETS")}"); COVERED="OUs $TARGETS" ;;
  *)
    IFS=, read -ra ids <<<"$TARGETS"
    members=()
    for id in "${ids[@]}"; do
      is_account_id "$id" || die "bad account id in --targets: $id"
      if [[ "$id" == "$MGMT_ID" ]]; then warn "management account is covered by its own stack, not the StackSet; leaving it out there"
      else members+=("$id"); fi
    done
    if ((${#members[@]})); then
      list="$(IFS=,; echo "${members[*]}")"
      TARGET_ARGS=(--deployment-targets "{\"OrganizationalUnitIds\":[\"$ROOT_ID\"],\"Accounts\":$(csv_json "$list"),\"AccountFilterType\":\"INTERSECTION\"}")
      COVERED="accounts $list"
    else
      TARGETS=none
    fi ;;
esac

TEMPLATE_FILE="$(mktemp -t sailpoint-cfn.XXXXXX)"; trap 'rm -f "$TEMPLATE_FILE"' EXIT
template >"$TEMPLATE_FILE"
PARAMS=(RoleName="$ROLE_NAME" ExternalId="$EXTERNAL_ID" SailPointPrincipals="$PRINCIPALS" IncludeProvisioning="$PROVISIONING"
        IncludeBedrock="$BEDROCK" IncludeAgentCore="$AGENTCORE")

step "Plan"
cat >&2 <<EOF
  role name        $ROLE_NAME   (same in every account)
  trusts           ${PRINCIPALS//,/ + }
                   only with sts:ExternalId = $EXTERNAL_ID
  permissions      SPAggregationPolicy$( [[ $PROVISIONING == true ]] && echo " + SPProvisioningPolicy (IAM write)" || echo " (read-only)")
                   + SPOrganizationPolicy in the management account$( [[ $BEDROCK == true ]] && printf '\n                   + SPBedrockAgentDiscoveryPolicy (Bedrock Agents discovery)')$( [[ $AGENTCORE == true ]] && printf '\n                   + SPBedrockAgentCoreDiscoveryPolicy (AgentCore discovery)')
  management acct  $MGMT_ID  -> stack $STACK_NAME in $REGION
  member accounts  ${COVERED}$( [[ $TARGETS != none ]] && echo "  -> StackSet $STACK_NAME (service-managed, auto-deploy to new accounts)")
EOF

# --- Management account: plain stack (StackSets never deploy to the management account) ----------
step "Management account stack"
run "${AWS[@]}" cloudformation deploy --stack-name "$STACK_NAME" --template-file "$TEMPLATE_FILE" \
  --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset \
  --parameter-overrides "${PARAMS[@]}" IncludeOrganizationPolicy=true \
  --tags ManagedBy=sailpoint-isc-aws-connector-skill

# --- Member accounts: service-managed StackSet ---------------------------------------------------
if [[ "$TARGETS" != none ]]; then
  step "Member accounts StackSet"
  # JSON, not shorthand: parameter values (the principal list) contain commas.
  SS_PARAMS="$(printf '%s\n' "${PARAMS[@]}" IncludeOrganizationPolicy=false \
    | jq -Rcs 'split("\n") | map(select(length>0) | capture("^(?<ParameterKey>[^=]+)=(?<ParameterValue>.*)$"))')"
  exists=0
  (( SKIP_PREFLIGHT )) || { "${AWS[@]}" cloudformation describe-stack-set --stack-set-name "$STACK_NAME" >/dev/null 2>&1 && exists=1; }
  common=(--stack-set-name "$STACK_NAME" --template-body "file://$TEMPLATE_FILE" --capabilities CAPABILITY_NAMED_IAM
          --parameters "$SS_PARAMS")
  if (( exists )); then
    run "${AWS[@]}" cloudformation update-stack-set "${common[@]}" "${TARGET_ARGS[@]}" --regions "$REGION"
  else
    run "${AWS[@]}" cloudformation create-stack-set "${common[@]}" --permission-model SERVICE_MANAGED \
      --auto-deployment Enabled=true,RetainStacksOnAccountRemoval=false
    run "${AWS[@]}" cloudformation create-stack-instances --stack-set-name "$STACK_NAME" \
      "${TARGET_ARGS[@]}" --regions "$REGION" \
      --operation-preferences FailureTolerancePercentage=10,MaxConcurrentPercentage=50
  fi
  (( APPLY )) && ok "StackSet operation started; check: aws cloudformation list-stack-instances --stack-set-name $STACK_NAME"
fi

# --- Summary for command 2 -------------------------------------------------------------------------
jq -n --arg mgmt "$MGMT_ID" --arg role "$ROLE_NAME" --arg region "$REGION" --arg targets "$TARGETS" \
      --arg partition "$PARTITION" --argjson prov "$PROVISIONING" --argjson applied "$APPLY" \
      --argjson bedrock "$BEDROCK" --argjson agentcore "$AGENTCORE" \
  '{managementAccountId:$mgmt, roleName:$role, region:$region, targets:$targets, partition:$partition,
    provisioning:$prov, bedrockDiscovery:$bedrock, agentCoreDiscovery:$agentcore, applied:($applied==1)}' | tee "$OUT"
(( APPLY )) && ok "AWS side done. Next: isc-source.sh configure --role-name $ROLE_NAME --mgmt-account-id $MGMT_ID" \
            || warn "dry-run only; rerun with --apply to make these changes"
