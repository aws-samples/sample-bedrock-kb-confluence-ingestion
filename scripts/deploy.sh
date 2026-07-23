#!/usr/bin/env bash
# ============================================================================
# CKN Ingestion Pipeline — Full Deploy Script
#
# Deploys the CDK stack, creates the AOSS index, builds+pushes the container
# image, stores the Confluence token, and triggers the first ingestion run.
#
# Prerequisites:
#   - AWS CLI v2 configured (or pass --profile)
#   - Docker (or Finch) running
#   - Node.js 22+ and CDK v2 (`npm install -g aws-cdk`) for CDK operations
#   - Python 3.11+ (for local dry runs)
#   - A Confluence Cloud API token (https://id.atlassian.com/manage-profile/security/api-tokens)
#
# Usage:
#   ./scripts/deploy.sh --account 123456789012 --region us-west-2 \
#       --confluence-email user@example.com \
#       --confluence-url https://your-site.atlassian.net \
#       --space-key KB
#
# All flags are required on first deploy. Subsequent runs reuse the values
# from client.json if flags are omitted.
# ============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
ACCOUNT=""
REGION=""
PROFILE="${AWS_PROFILE:-default}"
CONFLUENCE_EMAIL=""
CONFLUENCE_URL=""
SPACE_KEY=""
SKIP_CDK=""
SKIP_IMAGE=""
SKIP_INDEX=""
PHASE="full"  # full | infra-only | kb-only

usage() {
  cat <<EOF
Usage: $0 [OPTIONS]

Required (first deploy):
  --account ID          AWS account ID (12 digits)
  --region REGION       AWS region (e.g. us-west-2)
  --confluence-email E  Confluence user email
  --confluence-url URL  Confluence base URL (https://your-site.atlassian.net)
  --space-key KEY       Confluence space key (find via GET /rest/api/space)

Optional:
  --profile PROFILE     AWS CLI profile (default: \$AWS_PROFILE or 'default')
  --skip-cdk            Skip CDK deploy (image-only redeploy)
  --skip-image          Skip Docker build+push
  --skip-index          Skip AOSS index creation (if already exists)
  --phase PHASE         Deploy phase: full (default), infra-only, kb-only

Finding your space key:
  Space keys appear in the Confluence URL: /wiki/spaces/{SPACE_KEY}/...
  For personal spaces the key starts with ~ followed by a hex ID.
  To look up a space key from a page URL or short link:
    curl -u email:token https://your-site.atlassian.net/wiki/rest/api/content/{pageId}?expand=space
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --account)          ACCOUNT="$2";          shift 2 ;;
    --region)           REGION="$2";           shift 2 ;;
    --profile)          PROFILE="$2";          shift 2 ;;
    --confluence-email) CONFLUENCE_EMAIL="$2"; shift 2 ;;
    --confluence-url)   CONFLUENCE_URL="$2";   shift 2 ;;
    --space-key)        SPACE_KEY="$2";        shift 2 ;;
    --skip-cdk)         SKIP_CDK=1;            shift ;;
    --skip-image)       SKIP_IMAGE=1;          shift ;;
    --skip-index)       SKIP_INDEX=1;          shift ;;
    --phase)            PHASE="$2";            shift 2 ;;
    -h|--help)          usage ;;
    *) echo "Unknown arg: $1"; usage ;;
  esac
done

# ---------------------------------------------------------------------------
# Validate required arguments
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -z "$ACCOUNT" ]]; then
  echo "ERROR: --account is required" >&2; usage
fi
if [[ -z "$REGION" ]]; then
  echo "ERROR: --region is required" >&2; usage
fi
if ! [[ "$ACCOUNT" =~ ^[0-9]{12}$ ]]; then
  echo "ERROR: --account must be a 12-digit AWS account ID" >&2; exit 1
fi

export AWS_REGION="$REGION"
export AWS_DEFAULT_REGION="$REGION"

ECR_REPO="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/ckn-ingestion"

# ---------------------------------------------------------------------------
# Helper: aws with profile
# ---------------------------------------------------------------------------
aws_cmd() {
  aws --profile "$PROFILE" --region "$REGION" "$@"
}

echo "============================================================"
echo "CKN Ingestion Pipeline — Deploy"
echo "  Account:  $ACCOUNT"
echo "  Region:   $REGION"
echo "  Profile:  $PROFILE"
echo "  Phase:    $PHASE"
echo "============================================================"
echo ""

# ---------------------------------------------------------------------------
# Step 0: Prerequisite — CloudTrail log group
# ---------------------------------------------------------------------------
echo "==> [0/7] Ensuring CloudTrail log group exists..."
if aws_cmd logs describe-log-groups --log-group-name-prefix /aws/cloudtrail/ckn-trail \
    --query 'logGroups[0].logGroupName' --output text 2>/dev/null | grep -q ckn-trail; then
  echo "    Already exists."
else
  echo "    Creating /aws/cloudtrail/ckn-trail..."
  aws_cmd logs create-log-group --log-group-name /aws/cloudtrail/ckn-trail
  echo "    Created."
fi

# ---------------------------------------------------------------------------
# Step 1: CDK Deploy — Phase 1 (infra, no KB)
# ---------------------------------------------------------------------------
if [[ -z "$SKIP_CDK" ]]; then
  cd "$REPO_ROOT/src/infra"
  npm install --silent 2>/dev/null

  if [[ "$PHASE" == "full" || "$PHASE" == "infra-only" ]]; then
    echo ""
    echo "==> [1/7] CDK deploy — Phase 1 (infra, deployKb=false)..."
    echo "    Running cdk diff first (IAM review)..."
    npx cdk diff --context deployKb=false \
      --toolkit-stack-name CDKToolkit-cknpipe 2>&1 | tail -30 || true
    echo ""
    echo "    Deploying..."
    npx cdk deploy --require-approval never --context deployKb=false \
      --toolkit-stack-name CDKToolkit-cknpipe \
      -O /tmp/ckn-cdk-outputs.json
    echo "    Phase 1 complete."
  fi

  # -------------------------------------------------------------------------
  # Step 2: Create AOSS vector index
  # -------------------------------------------------------------------------
  if [[ -z "$SKIP_INDEX" && "$PHASE" != "kb-only" ]]; then
    echo ""
    echo "==> [2/7] Creating AOSS vector index via Fargate task..."
    "$REPO_ROOT/src/infra/scripts/create-aoss-index.sh" --profile "$PROFILE" --region "$REGION"
  else
    echo ""
    echo "==> [2/7] Skipping AOSS index creation (--skip-index or kb-only phase)."
  fi

  # -------------------------------------------------------------------------
  # Step 3: CDK Deploy — Phase 2 (with KB)
  # -------------------------------------------------------------------------
  if [[ "$PHASE" == "full" || "$PHASE" == "kb-only" ]]; then
    echo ""
    echo "==> [3/7] CDK deploy — Phase 2 (deployKb=true)..."
    npx cdk deploy --require-approval never --context deployKb=true \
      --toolkit-stack-name CDKToolkit-cknpipe \
      -O /tmp/ckn-cdk-outputs.json
    echo "    Phase 2 complete."
  fi

  cd "$REPO_ROOT"
else
  echo ""
  echo "==> [1-3/7] Skipping CDK deploy (--skip-cdk)."
fi

# ---------------------------------------------------------------------------
# Step 4: Build and push Docker image (both tags)
# ---------------------------------------------------------------------------
if [[ -z "$SKIP_IMAGE" ]]; then
  echo ""
  echo "==> [4/7] Building and pushing Docker image..."
  echo "    Logging in to ECR..."
  aws_cmd ecr get-login-password | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

  echo "    Building image..."
  docker build -t "$ECR_REPO:latest" .

  # The index-creator task uses the same image with tag 'index-creator'
  docker tag "$ECR_REPO:latest" "$ECR_REPO:index-creator"

  echo "    Pushing :latest..."
  docker push "$ECR_REPO:latest"
  echo "    Pushing :index-creator..."
  docker push "$ECR_REPO:index-creator"
  echo "    Done."
else
  echo ""
  echo "==> [4/7] Skipping Docker build+push (--skip-image)."
fi

# ---------------------------------------------------------------------------
# Step 5: Store Confluence token in Secrets Manager
# ---------------------------------------------------------------------------
echo ""
echo "==> [5/7] Confluence token..."
SECRET_EXISTS=$(aws_cmd secretsmanager describe-secret --secret-id ams/ckn/confluence-token \
  --query 'Name' --output text 2>/dev/null || echo "")

if [[ -n "$SECRET_EXISTS" ]]; then
  echo "    Secret 'ams/ckn/confluence-token' already exists."
  echo "    To update, run:"
  echo "      printf '%s' 'EMAIL:API_TOKEN' | aws secretsmanager put-secret-value \\"
  echo "        --secret-id ams/ckn/confluence-token --secret-string file:///dev/stdin \\"
  echo "        --profile $PROFILE --region $REGION"
  echo ""
  echo "    IMPORTANT: Use printf (not echo) to avoid a trailing newline."
  echo "    Format: email:api_token (colon-separated, no spaces)."
  echo "    Verify auth works:"
  echo "      curl -s -u 'email:token' https://your-site.atlassian.net/wiki/rest/api/user/current | head -1"
else
  echo "    Secret does not exist yet — it will be created by CDK on first deploy."
  echo "    After deploy, store the token:"
  echo "      printf '%s' 'EMAIL:API_TOKEN' | aws secretsmanager put-secret-value \\"
  echo "        --secret-id ams/ckn/confluence-token --secret-string file:///dev/stdin \\"
  echo "        --profile $PROFILE --region $REGION"
fi

# ---------------------------------------------------------------------------
# Step 6: Generate client.local.json (gitignored — never committed)
# ---------------------------------------------------------------------------
echo ""
echo "==> [6/7] Generating client.local.json..."
echo "    NOTE: client.json is the tracked placeholder file (never commit real values)."
echo "    This script writes your deploy-specific values to client.local.json instead."

# Read KB ID from CDK outputs if available
KB_ID=""
if [[ -f /tmp/ckn-cdk-outputs.json ]]; then
  KB_ID=$(python3 -c "
import json, sys
try:
    outputs = json.load(open('/tmp/ckn-cdk-outputs.json'))
    for stack in outputs.values():
        if 'KnowledgeBaseId' in stack:
            print(stack['KnowledgeBaseId'])
            sys.exit(0)
except Exception:
    pass
" 2>/dev/null || echo "")
fi

KMS_ARN=""
if [[ -f /tmp/ckn-cdk-outputs.json ]]; then
  KMS_ARN=$(python3 -c "
import json, sys
try:
    outputs = json.load(open('/tmp/ckn-cdk-outputs.json'))
    for stack in outputs.values():
        if 'KmsKeyArn' in stack:
            print(stack['KmsKeyArn'])
            sys.exit(0)
except Exception:
    pass
" 2>/dev/null || echo "")
fi

if [[ -n "$KB_ID" || -n "$CONFLUENCE_URL" ]]; then
  python3 -c "
import json, sys

path = 'client.local.json'
try:
    cfg = json.load(open(path))
except Exception:
    cfg = {}

kb_id = '${KB_ID}' or cfg.get('kb_id', 'YOUR_KB_ID')
region = '${REGION}'
url = '${CONFLUENCE_URL}' or cfg.get('confluence', {}).get('base_url', '')
kms = '${KMS_ARN}' or cfg.get('confluence', {}).get('kms_key_arn', '')
space = '${SPACE_KEY}' or (cfg.get('confluence', {}).get('spaces', [''])[0])

cfg['kb_id'] = kb_id
cfg['kb_region'] = region
cfg.setdefault('confluence', {})
if url: cfg['confluence']['base_url'] = url
if kms: cfg['confluence']['kms_key_arn'] = kms
cfg['confluence']['kms_secret_id'] = 'ams/ckn/confluence-token'
if space: cfg['confluence']['spaces'] = [space]

with open(path, 'w') as f:
    json.dump(cfg, f, indent=2)
    f.write('\n')
print(f'    Wrote client.local.json (kb_id={kb_id}, region={region})')
print(f'    Copy to client.json in the Docker image or pass via --config flag.')
"
else
  echo "    No CDK outputs found and no --confluence-url provided."
  echo "    Create client.local.json manually with your KB ID and Confluence settings."
fi

# ---------------------------------------------------------------------------
# Step 7: Trigger ingestion run
# ---------------------------------------------------------------------------
echo ""
echo "==> [7/7] Triggering ingestion run..."

# Discover all CDK-tagged private subnets (multi-AZ placement)
SUBNETS=$(aws_cmd ec2 describe-subnets \
  --filters "Name=tag:aws-cdk:subnet-type,Values=Private" \
            "Name=tag:aws:cloudformation:stack-name,Values=CknIngestionStack" \
  --query "Subnets[*].SubnetId" --output text 2>/dev/null || echo "")
# Convert tab-separated to comma-separated for awsvpcConfiguration
SUBNETS_CSV=$(echo "$SUBNETS" | tr '\t' ',')

if [[ -z "$SUBNETS_CSV" || "$SUBNETS_CSV" == "None" ]]; then
  echo "    WARNING: Could not find CDK-tagged private subnets."
  echo "    Trigger manually:"
  echo "      aws ecs run-task --cluster ckn-ingestion --task-definition ckn-ingestion \\"
  echo "        --launch-type FARGATE --network-configuration 'awsvpcConfiguration={subnets=[SUBNET1,SUBNET2],assignPublicIp=DISABLED}' \\"
  echo "        --profile $PROFILE --region $REGION"
else
  echo "    Subnets: $SUBNETS_CSV"
  TASK_ARN=$(aws_cmd ecs run-task \
    --cluster ckn-ingestion \
    --task-definition ckn-ingestion \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS_CSV],assignPublicIp=DISABLED}" \
    --query "tasks[0].taskArn" --output text 2>/dev/null || echo "FAILED")

  if [[ "$TASK_ARN" == "FAILED" || -z "$TASK_ARN" ]]; then
    echo "    WARNING: Failed to start ECS task. Check credentials and subnet."
  else
    echo "    Task started: $TASK_ARN"
    echo "    Monitor: aws logs tail /ckn/ingestion --follow --profile $PROFILE --region $REGION"
  fi
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "Deploy complete!"
echo ""
echo "Next steps:"
echo "  1. Ensure the Confluence token is stored (step 5 above)."
echo "  2. Verify auth: curl -s -u 'email:token' ${CONFLUENCE_URL:-https://your-site.atlassian.net}/wiki/rest/api/user/current"
echo "  3. Monitor logs: aws logs tail /ckn/ingestion --follow --profile $PROFILE --region $REGION"
echo ""
echo "Cleanup (when done testing):"
echo "  ./scripts/teardown.sh --account $ACCOUNT --region $REGION --profile $PROFILE"
echo "============================================================"
