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

## What CDK Does NOT Manage

The following resources are created manually and must NOT be added to CloudFormation:

### AOSS Vector Index (`bedrock-knowledge-base-default-index`)
- Created via AOSS Dashboard
- Collection: `ckn-kb-vectors` (ID: `<your-collection-id>`)

### Bedrock Knowledge Base (ID: `YOUR_KB_ID`)
- Created via AWS CLI
- Data source: S3 (`ams-ckn-123456789012/confluence/`)

### Why These Are Outside IaC

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
    --toolkit-stack-name CDKToolkit-cknpipe"
```

### Override KB ID (if creating a new KB for a different customer)
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

## Manual Steps for New Customer Deployment

1. Deploy CDK stack (creates AOSS collection + all infra)
2. Open AOSS Dashboard → create vector index `bedrock-knowledge-base-default-index`
   - Settings: `knn: true`, `knn.algo_param.ef_search: 512`
   - Mappings:
     - `bedrock-knowledge-base-default-vector`: knn_vector, dim 1024, hnsw/l2/faiss
     - `AMAZON_BEDROCK_TEXT`: text
     - `AMAZON_BEDROCK_METADATA`: text (index: false)
3. Create Bedrock KB via CLI pointing to the AOSS collection
4. Create S3 data source on the KB
5. Update `client.json` with the new KB ID
6. Build + push Docker image
7. Trigger ingestion run
