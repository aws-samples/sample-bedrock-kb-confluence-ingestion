# Standard Operating Procedure — CKN Ingestion

## Adding a Confluence Space

Add the space key to the `spaces` array in `client.json`:

```json
"spaces": ["YOUR_SPACE_KEY", "NEW_SPACE_KEY"]
```

The space key is from the Confluence URL: `https://<instance>.atlassian.net/wiki/spaces/<SPACE_KEY>/overview`

Confluence spaces are content partitions within a single deployment, not tenants.

Then rebuild the Docker image and run the ECS task.

## Initial Setup

### 1. Provision infrastructure

Deploy the CDK stack into the target AWS account (see `POST_DEPLOYMENT.md`).

### 2. Create the config

Edit `client.json` with deployment-specific values:

```json
{
  "kb_id": "<Bedrock Knowledge Base ID>",
  "kb_region": "us-east-1",
  "confluence": {
    "base_url": "https://<instance>.atlassian.net",
    "kms_key_arn": "<KMS key ARN from stack output>",
    "kms_secret_id": "ams/ckn/confluence-token",
    "spaces": ["SPACE_KEY_1", "SPACE_KEY_2"]
  }
}
```

The AWS account ID is not part of the config — the app derives it at runtime
from its task credentials (STS `GetCallerIdentity`) when building the S3
bucket name (`ams-ckn-<account_id>`).

### 3. Store the Confluence API token

The token is stored in Secrets Manager as `email:api_token`:

```bash
aws secretsmanager put-secret-value \
  --secret-id ams/ckn/confluence-token \
  --secret-string 'user@example.com:ATATT3x...' \
  --region us-east-1
```

### 4. Build and push the Docker image

The `client.json` is baked into the image:

```bash
docker build -t <ACCOUNT>.dkr.ecr.us-east-1.amazonaws.com/ckn-ingestion:latest .
docker push <ACCOUNT>.dkr.ecr.us-east-1.amazonaws.com/ckn-ingestion:latest
```

### 5. Run the ingestion task

```bash
aws ecs run-task \
  --cluster ckn-ingestion \
  --task-definition ckn-ingestion \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<PRIVATE_SUBNET>],assignPublicIp=DISABLED}" \
  --region us-east-1
```

### 6. Sync the Knowledge Base

```bash
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id <KB_ID> \
  --data-source-id <DS_ID> \
  --region us-east-1
```

## Architecture Note

- One `client.json` = one deployment = one ECS task run
- The pipeline is stateless — `kb_last_synced` in `client.json` tracks incremental sync (but is lost on image rebuild; only matters for the running container)
