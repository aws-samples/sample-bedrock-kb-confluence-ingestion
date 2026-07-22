# CKN Ingestion — Infrastructure

## What CDK Manages

- ECR repository (`ckn-ingestion`)
- S3 bucket (`ams-ckn-{account_id}`)
- ECS Cluster (Fargate) + Task Definition (4 vCPU / 8GB)
- Task Role (Secrets Manager, KMS, Bedrock, S3)
- Execution Role (ECR pull + CloudWatch Logs)
- EventBridge scheduled rule (daily 02:00 UTC)
- CloudWatch log group (`/ckn/ingestion`)
- OpenSearch Serverless collection (`ckn-kb-vectors`) + security policies
- Bedrock KB execution role (`ckn-bedrock-kb-role`)
- Bedrock Knowledge Base + S3 data source — only when deployed with
  `--context deployKb=true` (skipped by default because the KB requires the
  manually created AOSS vector index; see two-phase deployment below and in
  `POST_DEPLOYMENT.md`)

## What CDK Does NOT Manage

The following resource is created manually and must NOT be added to CloudFormation:

### AOSS Vector Index (`bedrock-knowledge-base-default-index`)
- Created via AOSS Dashboard
- Collection: `ckn-kb-vectors` (ID: `<your-collection-id>`)

### Why This Is Outside IaC

Some account configurations apply a gateway restriction on AOSS that
blocks data plane write operations (index creation) from Lambda, ECS, and
development environments. The AOSS Dashboard works because it routes through
a different gateway path.

Attempts to automate index creation via:
- CDK Custom Resource (Lambda) → persistent 403 from AOSS data access policy
- Standalone Python script from dev-dsk → same 403
- Both failed even with correct IAM policies and AOSS data access policies

The AOSS Dashboard is the only path that works for index creation in this
account configuration.

## Deploying

### Prerequisites
- Docker (for running CDK with Node 22)
- AWS CLI configured with default profile

### Deploy command
```bash
docker run --rm \
  -v ~/.aws:/root/.aws:ro \
  -v $(pwd)/infra:/app \
  -w /app \
  -e AWS_DEFAULT_REGION=us-west-2 \
  -e CDK_DEFAULT_ACCOUNT=123456789012 \
  -e CDK_DEFAULT_REGION=us-west-2 \
  node:22 \
  sh -c "rm -rf cdk.out && npm install --silent && npx cdk deploy \
    --require-approval never \
    --context deployKb=true \
    --toolkit-stack-name CDKToolkit-cknpipe"
```

The `deployKb=true` context flag creates the Bedrock Knowledge Base and its S3
data source. It requires the AOSS vector index to already exist — on the very
first deploy (before the index is created), pass `--context deployKb=false`
instead, then redeploy with `deployKb=true` after creating the index. See the
two-phase deployment in `POST_DEPLOYMENT.md`.

### Override KB ID
```bash
... --context kbId=NEW_KB_ID
```

### Override schedule
```bash
... --context schedule="rate(6 hours)"
```

## Bootstrap
CDK bootstrap qualifier: `cknpipe`
Toolkit stack: `CDKToolkit-cknpipe`

## Manual Steps for New Deployment

1. Deploy CDK stack without the KB (`--context deployKb=false`) — creates the
   AOSS collection + all infra
2. Open AOSS Dashboard → create vector index `bedrock-knowledge-base-default-index`
   - Settings: `knn: true`, `knn.algo_param.ef_search: 512`
   - Mappings:
     - `bedrock-knowledge-base-default-vector`: knn_vector, dim 1024, hnsw/l2/faiss
     - `AMAZON_BEDROCK_TEXT`: text
     - `AMAZON_BEDROCK_METADATA`: text (index: false)
3. Redeploy with `--context deployKb=true` — creates the Bedrock KB and its
   S3 data source via CDK
4. Update `client.json` with the new KB ID (stack output)
5. Build + push Docker image
6. Trigger ingestion run
