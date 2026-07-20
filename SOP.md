# Standard Operating Procedure — CKN Ingestion

## Adding a Confluence Space to an Existing Customer

Add the space key to the `spaces` array in the customer's `client.json`:

```json
"spaces": ["YOUR_SPACE_KEY", "NEW_SPACE_KEY"]
```

The space key is from the Confluence URL: `https://<tenant>.atlassian.net/wiki/spaces/<SPACE_KEY>/overview`

Then rebuild the Docker image and run the ECS task.

## Onboarding a New Customer

Each customer gets its own `client.json` file. The pipeline processes one customer per run (`load_config()` returns the first entry only).

### 1. Provision infrastructure

If the customer runs in a separate AWS account, deploy the full CDK stack into that account (see `POST_DEPLOYMENT.md`). If sharing the same account, reuse the existing stack.

### 2. Create the customer config

Create a dedicated config file (e.g., `client-<customer>.json`):

```json
{
  "customers": [
    {
      "name": "customer-name",
      "account_id": "<AWS account ID>",
      "kb_id": "<Bedrock Knowledge Base ID>",
      "kb_region": "us-east-1",
      "status": "active",
      "confluence": {
        "base_url": "https://<tenant>.atlassian.net",
        "kms_key_arn": "<KMS key ARN from stack output>",
        "kms_secret_id": "ams/ckn/confluence-token",
        "spaces": ["SPACE_KEY_1", "SPACE_KEY_2"]
      }
    }
  ]
}
```

### 3. Store the Confluence API token

Each customer's token is stored in Secrets Manager as `email:api_token`:

```bash
aws secretsmanager put-secret-value \
  --secret-id ams/ckn/confluence-token \
  --secret-string 'user@customer.com:ATATT3x...' \
  --region us-east-1
```

> Note: If multiple customers share the same account, you'll need separate secret names per customer and update `kms_secret_id` accordingly.

### 4. Build and push the Docker image

The `client.json` is baked into the image. Build a separate image per customer or parameterize via environment variables:

```bash
cp client-<customer>.json client.json
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

- One `client.json` = one customer = one ECS task run
- Each customer can live in its own AWS account with its own stack
- The pipeline is stateless — `kb_last_synced` in `client.json` tracks incremental sync (but is lost on image rebuild; only matters for the running container)
