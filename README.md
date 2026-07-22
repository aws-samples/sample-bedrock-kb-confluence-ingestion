# Sample: Amazon Bedrock Knowledge Base — Confluence Ingestion

> **This is sample / reference code.** It demonstrates one technique for ingesting
> Atlassian Confluence content into an Amazon Bedrock Knowledge Base for
> Retrieval-Augmented Generation (RAG). It is **not** a production-ready product and
> is provided as a starting point to learn from and adapt. Review, test, and harden
> it for your own requirements before using it with real data.

This sample shows a scheduled pipeline that extracts pages from Confluence Cloud,
classifies and enriches the content with Amazon Bedrock (Claude), converts it to
Markdown, and stores it in Amazon S3 for indexing by a Bedrock Knowledge Base
backed by Amazon OpenSearch Serverless (AOSS).

## Architecture Overview

```
EventBridge (daily schedule)
        │
        ▼
ECS Fargate task (this Python app)
        │  1. Retrieve Confluence API token from Secrets Manager (KMS-encrypted)
        │  2. Extract pages + attachments via Confluence Cloud REST API
        │  3. Process images/diagrams with Amazon Bedrock (Claude vision)
        │  4. Classify + enrich metadata with Amazon Bedrock (Claude Haiku)
        │  5. Convert to Markdown, chunk, and write to S3 (SSE-KMS)
        ▼
Amazon S3  (confluence/{space}/{page_id}.md + .metadata.json)
        │
        ▼
Amazon Bedrock Knowledge Base  ──►  Amazon OpenSearch Serverless (vector store)
```

Key components:

- **Python ingestion app** (`src/ckn_ingestion/`) — the ECS Fargate workload.
- **AWS CDK stack** (`src/infra/`) — provisions ECR, S3 (SSE-KMS), ECS/Fargate,
  IAM roles, KMS, Secrets Manager, AOSS collection, EventBridge schedule, and an
  optional Bedrock Knowledge Base.
- **`client.json`** — per-deployment configuration (Confluence base URL, spaces,
  KB ID, KMS key ARN, Secrets Manager secret ID). The committed file contains
  **placeholder values only** — replace them with your own.

## Prerequisites

- An AWS account with access to Amazon Bedrock (the Claude and Titan Embeddings
  models used here must be enabled in your target Region).
- AWS CLI v2, configured with credentials for the target account.
- Docker.
- Node.js 20+ and AWS CDK v2 (`npm install -g aws-cdk`) for the infrastructure.
- Python 3.11+ to run or test the app locally.
- An Atlassian Confluence Cloud site and an API token
  (https://id.atlassian.com/manage-profile/security/api-tokens).

## Configuration

Edit `client.json` and replace the placeholder values:

```json
{
  "kb_id": "YOUR_KB_ID",
  "kb_region": "us-east-1",
  "confluence": {
    "base_url": "https://your-confluence.atlassian.net",
    "kms_key_arn": "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000",
    "kms_secret_id": "ams/ckn/confluence-token",
    "spaces": ["YOUR_SPACE_KEY"]
  }
}
```

The AWS account ID is not configured: the app derives it at runtime from its
own credentials (STS `GetCallerIdentity`) to build the S3 bucket name
(`ams-ckn-<account_id>`), so uploads always target the account the task runs in.

The Confluence API token is **never** stored in `client.json`. It is read at
runtime from AWS Secrets Manager (`kms_secret_id`), encrypted with a
customer-managed KMS key (CMK).

## Deployment

Detailed, step-by-step instructions are in [`DEPLOYMENT.md`](DEPLOYMENT.md) and
[`POST_DEPLOYMENT.md`](POST_DEPLOYMENT.md). At a high level:

1. Deploy the CDK stack (`src/infra/`) to create the infrastructure.
2. Create the AOSS vector index (see [`src/infra/README.md`](src/infra/README.md)).
3. Store your Confluence API token in Secrets Manager as `email:api_token`.
4. Update `client.json` with the CDK output values (`kb_id`, `kms_key_arn`).
5. Build and push the container image to ECR.
6. Trigger an ingestion run (or wait for the daily schedule).

A local dry run (no S3 writes) is available for testing extraction and
classification:

```bash
python -m ckn_ingestion --dry-run --config client.json
```

## Cost Considerations

> **This sample deploys resources that incur AWS charges.** Running it in your
> account will generate costs, including but not limited to:
>
> - **Amazon Bedrock** model invocations (classification, image processing,
>   embeddings) — billed per token/image.
> - **Amazon OpenSearch Serverless** — charged per OCU-hour; a collection incurs
>   ongoing cost even when idle.
> - **ECS Fargate** task runtime, **NAT Gateway**, **VPC interface endpoints**,
>   **S3** storage/requests, **KMS**, **Secrets Manager**, and **CloudWatch Logs**.
>
> Estimate costs with the [AWS Pricing Calculator](https://calculator.aws/) before
> deploying, and monitor spend. **Remember to clean up when you are done.**

## Cleanup

To avoid ongoing charges, delete everything this sample created:

1. Delete the Bedrock Knowledge Base and its S3 data source (if created).
2. Delete the AOSS vector index and collection.
3. Empty and delete the S3 buckets (`ams-ckn-<account>` and its access-log bucket).
4. Destroy the CDK stack:
   ```bash
   cd src/infra && cdk destroy
   ```
   Note: some resources use `RemovalPolicy.RETAIN` (S3 buckets, KMS key, log
   group, ECR repository) and must be deleted manually after `cdk destroy`.
5. Delete the Secrets Manager secret and schedule any KMS key deletion.

## Security

- This is sample code. **Review and harden it before any production use.**
- Do not commit real credentials, account IDs, or ARNs. `client.json`
  ships with placeholders only; secrets belong in AWS Secrets Manager.
- IAM policies in the CDK stack aim to be least-privilege but should be reviewed
  against your own security requirements.
- Confluence page content is untrusted input; it is HTML-sanitized before
  processing. Review the sanitizer and the classification-prompt handling for your
  own threat model.
- To report a security issue, follow the process in [`CONTRIBUTING.md`](CONTRIBUTING.md);
  do not open a public GitHub issue.

## Repository Layout

| Path | Description |
|------|-------------|
| `src/ckn_ingestion/` | Python ingestion application |
| `src/infra/` | AWS CDK (TypeScript) infrastructure |
| `test/` | Python unit and property tests |
| `client.json` | Per-deployment configuration (placeholders) |
| `Dockerfile` | Container image for the Fargate task |
| `DEPLOYMENT.md`, `POST_DEPLOYMENT.md`, `SOP.md` | Operational guides |

## License

This sample is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
