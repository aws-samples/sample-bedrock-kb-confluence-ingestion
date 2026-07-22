# Post-Deployment Guide

All commands in this project use Docker containers (Node.js 20+ for CDK, Python 3.12 for the ingestion app).

## 1. CDK via Docker

CDK requires Node.js 20+ and this project uses Docker for all CDK operations:

```bash
docker run --rm \
  -v ~/.aws:/root/.aws:ro \
  -v $(pwd)/src/infra:/app \
  -w /app \
  -e AWS_DEFAULT_REGION=us-east-1 \
  -e CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text) \
  -e CDK_DEFAULT_REGION=us-east-1 \
  node:20 \
  bash -c "npm install -g aws-cdk@latest 2>/dev/null && cdk deploy --require-approval never --context deployKb=false"
```

## 2. Two-Phase Deployment (Knowledge Base)

The Bedrock Knowledge Base requires an OpenSearch Serverless vector index that cannot be created via CloudFormation. The stack only creates the KB when the `deployKb` context variable is set to `true` (the default is to skip it). Deploy in two phases:

**Phase 1** — Deploy infrastructure (skip KB; the AOSS index does not exist yet):
```bash
cdk deploy --require-approval never --context deployKb=false
```

**Phase 2** — Create the AOSS index, then deploy the KB:
```bash
./scripts/create-aoss-index.sh --profile default --region us-east-1
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

The `CloudTrailDetection` construct references an external CloudTrail log group (`/aws/cloudtrail/ckn-trail`) via `logs.LogGroup.fromLogGroupName`. This log group must exist before deployment:

```bash
aws logs create-log-group --log-group-name /aws/cloudtrail/ckn-trail --region us-east-1
```

## 5. Token-Aware ARN Validation

The `CloudTrailDetection` construct validates ARN formats at synth time. When props contain CDK tokens (e.g., `role.roleArn`), validation is skipped using `cdk.Token.isUnresolved()`. This was fixed in `lib/cloudtrail-detection.ts`.

## 6. Data Ingestion

After the full stack is deployed (Phase 2 complete), run the ingestion pipeline to populate the Knowledge Base.

### 6.1 Store the Confluence API token

Format: `email:api_token` (plain string, colon-separated).

```bash
aws secretsmanager put-secret-value \
  --secret-id ams/ckn/confluence-token \
  --secret-string 'user@example.com:ATATT3x...' \
  --region us-east-1
```

### 6.2 Create `client.json`

```json
{
  "kb_id": "<KB_ID from stack output>",
  "kb_region": "us-east-1",
  "confluence": {
    "base_url": "https://your-confluence.atlassian.net",
    "kms_key_arn": "<KmsKeyArn from stack output>",
    "kms_secret_id": "ams/ckn/confluence-token",
    "spaces": ["YOUR_SPACE_KEY"]
  }
}
```

There is no `account_id` field: the app derives the account ID at runtime from
its task credentials (STS `GetCallerIdentity`) to build the S3 bucket name
(`ams-ckn-<account_id>`).

### 6.3 Build and push the Docker image

```bash
cd /path/to/CknIngestion
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <ACCOUNT>.dkr.ecr.us-east-1.amazonaws.com
docker build -t <ACCOUNT>.dkr.ecr.us-east-1.amazonaws.com/ckn-ingestion:latest .
docker push <ACCOUNT>.dkr.ecr.us-east-1.amazonaws.com/ckn-ingestion:latest
```

### 6.4 Run the ingestion ECS task

```bash
SUBNET=$(aws ec2 describe-subnets \
  --filters "Name=tag:aws-cdk:subnet-type,Values=Private" \
            "Name=tag:aws:cloudformation:stack-name,Values=CknIngestionStack" \
  --query "Subnets[0].SubnetId" --output text --region us-east-1)

aws ecs run-task \
  --cluster ckn-ingestion \
  --task-definition ckn-ingestion \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET],assignPublicIp=DISABLED}" \
  --region us-east-1
```

### 6.5 Sync the Knowledge Base

After ingestion completes (exit code 0), trigger a KB sync from the Bedrock console or CLI:

```bash
KB_ID=$(aws cloudformation describe-stacks --stack-name CknIngestionStack --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`KnowledgeBaseId`].OutputValue' --output text)

DS_ID=$(aws bedrock-agent list-data-sources --knowledge-base-id $KB_ID --region us-east-1 \
  --query 'dataSourceSummaries[0].dataSourceId' --output text)

aws bedrock-agent start-ingestion-job \
  --knowledge-base-id $KB_ID \
  --data-source-id $DS_ID \
  --region us-east-1
```

Monitor logs at `/ckn/ingestion` in CloudWatch.
