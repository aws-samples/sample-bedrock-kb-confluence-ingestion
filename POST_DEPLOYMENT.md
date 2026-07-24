# Post-Deployment Guide

> **Quickstart:** For a fully automated deploy, use `./scripts/deploy.sh` — it
> handles all steps below in sequence. This document explains each step for
> manual execution or troubleshooting.

All commands in this project use Docker containers (Node.js 22+ for CDK, Python 3.12 for the ingestion app).

## 0. Set Your Variables Once

Every command below uses these values. Set them at the top of your session:

```bash
export CKN_ACCOUNT="123456789012"       # Your 12-digit AWS account ID
export CKN_REGION="us-west-2"           # Deploy region (us-east-1, us-west-2, etc.)
export CKN_PROFILE="default"            # AWS CLI profile name
export CONFLUENCE_EMAIL="user@example.com"
export CONFLUENCE_URL="https://your-site.atlassian.net"
export SPACE_KEY="YOUR_SPACE_KEY"       # See "Finding your space key" below
```

**Finding your space key:** The space key appears in Confluence URLs as
`/wiki/spaces/{SPACE_KEY}/...`. For personal spaces it starts with `~`
(e.g. `~5b58bdf9e288ee2d9b4ba4fe`). To look up a key from a page URL or
short link (`/wiki/x/XXXX`):
```bash
curl -s -u "$CONFLUENCE_EMAIL:$TOKEN" \
  "$CONFLUENCE_URL/wiki/rest/api/content/{pageId}?expand=space" | \
  python3 -c "import json,sys; print(json.load(sys.stdin)['space']['key'])"
```

## 1. CDK via Docker

CDK requires Node.js 22+ and this project uses Docker for all CDK operations:

```bash
docker run --rm \
  -v ~/.aws:/root/.aws:ro \
  -v $(pwd)/src/infra:/app \
  -w /app \
  -e AWS_DEFAULT_REGION=$CKN_REGION \
  -e CDK_DEFAULT_ACCOUNT=$CKN_ACCOUNT \
  -e CDK_DEFAULT_REGION=$CKN_REGION \
  node:22 \
  bash -c "npm install -g aws-cdk@latest 2>/dev/null && cdk deploy --require-approval never --context deployKb=false"
```

> **Note:** `--require-approval never` bypasses CDK's IAM/security-change
> review. For first-time deployments, consider running `cdk diff` first to
> review the 4 IAM roles and 2 policies the stack creates, then deploy with
> `--require-approval broadening` or `never` once satisfied.

## 2. Two-Phase Deployment (Knowledge Base)

The Bedrock Knowledge Base requires an OpenSearch Serverless vector index that cannot be created via CloudFormation. The stack only creates the KB when the `deployKb` context variable is set to `true` (the default is to skip it). Deploy in two phases:

**Phase 1** — Deploy infrastructure (skip KB; the AOSS index does not exist yet):
```bash
cdk deploy --require-approval never --context deployKb=false
```

**Phase 2** — Create the AOSS index, then deploy the KB:
```bash
./scripts/create-aoss-index.sh --profile "$CKN_PROFILE" --region "$CKN_REGION"
cdk deploy --require-approval never --context deployKb=true
```

## 3. AOSS VPC Endpoint — Must Use AOSS API, Not EC2 API

**Problem**: The Fargate task gets 401/403 when accessing the AOSS collection endpoint even though the data access policy and IAM permissions are correct.

**Root cause**: There are two ways to create a VPC endpoint for AOSS:

1. **EC2 API** (`ec2:CreateVpcEndpoint` / CDK `vpc.addInterfaceEndpoint()`): Creates the endpoint but does NOT create the wildcard private hosted zone for collection-specific hostnames (`*.us-east-1.aoss.amazonaws.com`). Traffic to collection endpoints goes through NAT to the public internet, which the network policy rejects.

2. **AOSS API** (`aoss:CreateVpcEndpoint` / CDK `aoss.CfnVpcEndpoint`): Creates the endpoint AND a Route 53 private hosted zone with a wildcard record that resolves all collection hostnames to the VPC endpoint's private IPs. Traffic stays within the VPC.

**Fix**: Use `aoss.CfnVpcEndpoint` instead of `vpc.addInterfaceEndpoint()`:

```typescript
const aossVpce = new aoss.CfnVpcEndpoint(this, 'AossEndpoint', {
  name: 'ckn-aoss-vpce',
  vpcId: vpc.vpcId,
  subnetIds: vpc.selectSubnets({ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }).subnetIds,
  securityGroupIds: [aossVpceSg.securityGroupId],
});
```

Reference the endpoint in the network policy with `aossVpce.attrId`.

**AWS docs**: https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-vpc.html#vpc-endpoint-dnc

## 4. CloudTrail Log Group Prerequisite

> **IMPORTANT:** This step must be done BEFORE `cdk deploy`. The stack will
> fail at synth if this log group does not exist.

The `CloudTrailDetection` construct references an external CloudTrail log group (`/aws/cloudtrail/ckn-trail`) via `logs.LogGroup.fromLogGroupName`. This log group must exist before deployment:

```bash
aws logs create-log-group --log-group-name /aws/cloudtrail/ckn-trail \
  --region "$CKN_REGION" --profile "$CKN_PROFILE"
```

## 5. Token-Aware ARN Validation

The `CloudTrailDetection` construct validates ARN formats at synth time. When props contain CDK tokens (e.g., `role.roleArn`), validation is skipped using `cdk.Token.isUnresolved()`. This was fixed in `lib/cloudtrail-detection.ts`.

## 6. Data Ingestion

After the full stack is deployed (Phase 2 complete), run the ingestion pipeline to populate the Knowledge Base.

### 6.1 Store the Confluence API token

Format: `email:api_token` (plain string, colon-separated, **no trailing newline**).

> **WARNING:** Using `echo` adds a trailing newline that silently breaks
> Confluence authentication (HTTP 401 with no useful error message). Always
> use `printf '%s'` instead.

```bash
# Store the token (use printf, NOT echo, to avoid trailing newline)
printf '%s' "$CONFLUENCE_EMAIL:YOUR_API_TOKEN" | \
  aws secretsmanager put-secret-value \
    --secret-id ams/ckn/confluence-token \
    --secret-string file:///dev/stdin \
    --region "$CKN_REGION" --profile "$CKN_PROFILE"
```

Verify authentication works before proceeding:
```bash
curl -s -u "$CONFLUENCE_EMAIL:YOUR_API_TOKEN" \
  "$CONFLUENCE_URL/wiki/rest/api/user/current" | python3 -c "import json,sys; print(json.load(sys.stdin).get('displayName', 'ERROR'))"
```
If this prints your display name, the token is correct.

### 6.2 Create `client.json`

```json
{
  "kb_id": "<KB_ID from stack output — see KnowledgeBaseId>",
  "kb_region": "<CKN_REGION>",
  "confluence": {
    "base_url": "<CONFLUENCE_URL>",
    "kms_key_arn": "<KmsKeyArn from stack output>",
    "kms_secret_id": "ams/ckn/confluence-token",
    "spaces": ["<SPACE_KEY>"]
  }
}
```

> **Tip:** `./scripts/deploy.sh` auto-populates `client.json` from CDK outputs.

There is no `account_id` field: the app derives the account ID at runtime from
its task credentials (STS `GetCallerIdentity`) to build the S3 bucket name
(`ams-ckn-<account_id>`).

### 6.3 Build and push the Docker image

The stack uses **two image tags** from the same ECR repository:
- `:latest` — the main ingestion task (`ckn-ingestion` task definition)
- `:index-creator` — the AOSS index creation task (`ckn-create-aoss-index` task definition)

Both tags point to the same image. Push both:

```bash
ECR_REPO="$CKN_ACCOUNT.dkr.ecr.$CKN_REGION.amazonaws.com/ckn-ingestion"

aws ecr get-login-password --region "$CKN_REGION" --profile "$CKN_PROFILE" | \
  docker login --username AWS --password-stdin "$CKN_ACCOUNT.dkr.ecr.$CKN_REGION.amazonaws.com"

docker build -t "$ECR_REPO:latest" .
docker tag "$ECR_REPO:latest" "$ECR_REPO:index-creator"
docker push "$ECR_REPO:latest"
docker push "$ECR_REPO:index-creator"
```

### 6.4 Run the ingestion ECS task

```bash
SUBNET=$(aws ec2 describe-subnets \
  --filters "Name=tag:aws-cdk:subnet-type,Values=Private" \
            "Name=tag:aws:cloudformation:stack-name,Values=CknIngestionStack" \
  --query "Subnets[0].SubnetId" --output text \
  --region "$CKN_REGION" --profile "$CKN_PROFILE")

aws ecs run-task \
  --cluster ckn-ingestion \
  --task-definition ckn-ingestion \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET],assignPublicIp=DISABLED}" \
  --region "$CKN_REGION" --profile "$CKN_PROFILE"
```

### 6.5 Sync the Knowledge Base

After ingestion completes (exit code 0), the pipeline automatically triggers a
KB sync (see `cli.py`). If you need to trigger manually:

```bash
KB_ID=$(aws cloudformation describe-stacks --stack-name CknIngestionStack \
  --region "$CKN_REGION" --profile "$CKN_PROFILE" \
  --query 'Stacks[0].Outputs[?OutputKey==`KnowledgeBaseId`].OutputValue' --output text)

DS_ID=$(aws bedrock-agent list-data-sources --knowledge-base-id "$KB_ID" \
  --region "$CKN_REGION" --profile "$CKN_PROFILE" \
  --query 'dataSourceSummaries[0].dataSourceId' --output text)

aws bedrock-agent start-ingestion-job \
  --knowledge-base-id "$KB_ID" \
  --data-source-id "$DS_ID" \
  --region "$CKN_REGION" --profile "$CKN_PROFILE"
```

Monitor logs at `/ckn/ingestion` in CloudWatch:
```bash
aws logs tail /ckn/ingestion --follow --region "$CKN_REGION" --profile "$CKN_PROFILE"
```

## 7. Changing the KB Chunking Strategy (Reindex)

The data source is configured with `chunkingStrategy: NONE` — the pipeline owns
chunking (`content_splitter.split_markdown` pre-chunks and size-caps each page),
so Bedrock embeds each S3 object as-is. `chunkingConfiguration` is **immutable**:
changing the chunking strategy on an existing data source **replaces** it (new
`dataSourceId`) and requires a **full reindex** of the corpus. See
[`docs/REINDEX_RUNBOOK.md`](docs/REINDEX_RUNBOOK.md) for the deploy-affecting
procedure and rollback.
