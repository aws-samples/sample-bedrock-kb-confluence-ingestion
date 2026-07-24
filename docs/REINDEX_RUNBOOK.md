# Reindex Runbook — Switching KB Chunking to `NONE` (F9 Option A)

> **Deploy-affecting.** This procedure changes the Bedrock Knowledge Base data
> source chunking strategy from `SEMANTIC` to `NONE` and **re-embeds the entire
> corpus**. It changes what is stored in the vector index and briefly affects
> retrieval quality during reindexing. Run it in a maintenance window and with
> explicit approval. All commands assume the variables from
> [`POST_DEPLOYMENT.md` §0](../POST_DEPLOYMENT.md) are already exported
> (`CKN_ACCOUNT`, `CKN_REGION`, `CKN_PROFILE`).

## Why this change

Under **F9 Option A** the *pipeline* owns chunking. `content_splitter.split_markdown`
already splits each Confluence page at H1/H2 boundaries and caps each chunk's
size (`DEFAULT_MAX_CHUNK_CHARS`, see `src/ckn_ingestion/content_splitter.py`),
writing one retrieval-sized, title-prefixed markdown object per chunk to S3.

Leaving the KB data source on `SEMANTIC` means Bedrock **re-chunks** those
objects a second time, discarding the pipeline's heading boundaries and title
prefixes and producing chunk boundaries that no longer match what the pipeline
emitted. Setting `chunkingStrategy: NONE` tells Bedrock to embed each S3 object
**as-is** — one object → one vector — so the pipeline's chunking is authoritative.

## What changes

| | Before | After |
|---|---|---|
| Data source `ChunkingStrategy` | `SEMANTIC` (maxTokens 500) | `NONE` |
| Who splits content | pipeline **and** Bedrock (double chunking) | pipeline only |
| Vectors per S3 object | 1..N (Bedrock-decided) | exactly 1 |

The CDK change is already in `src/infra/lib/ckn-ingestion-stack.ts`
(`CknKbDataSource` → `chunkingConfiguration.chunkingStrategy: 'NONE'`).

## Preconditions

- [ ] The intra-section size-aware splitter is deployed in the running image
      (PR #21, merged). Verify the ingestion image tag in ECR is at or newer than
      commit `da89000`.
- [ ] Maintenance window agreed; stakeholders told retrieval may be degraded
      until the reindex completes.
- [ ] You have a way to sanity-check retrieval afterward (a handful of known
      queries + expected pages).

## Procedure

### 1. Capture the current state (rollback reference)

```bash
KB_ID=$(aws cloudformation describe-stacks --stack-name CknIngestionStack \
  --region "$CKN_REGION" --profile "$CKN_PROFILE" \
  --query 'Stacks[0].Outputs[?OutputKey==`KnowledgeBaseId`].OutputValue' --output text)

DS_ID=$(aws bedrock-agent list-data-sources --knowledge-base-id "$KB_ID" \
  --region "$CKN_REGION" --profile "$CKN_PROFILE" \
  --query 'dataSourceSummaries[0].dataSourceId' --output text)

# Record the current chunking config so you can roll back if needed.
aws bedrock-agent get-data-source --knowledge-base-id "$KB_ID" --data-source-id "$DS_ID" \
  --region "$CKN_REGION" --profile "$CKN_PROFILE" \
  --query 'dataSource.vectorIngestionConfiguration.chunkingConfiguration' \
  > "reindex-rollback-$DS_ID.json"
cat "reindex-rollback-$DS_ID.json"   # expect chunkingStrategy: SEMANTIC (API output is camelCase)
```

> ⚠️ **`chunkingConfiguration` is immutable.** The Bedrock `CreateDataSource`
> API does not allow changing `chunkingConfiguration` after a data source is
> created, and CloudFormation marks
> `AWS::Bedrock::DataSource ChunkingConfiguration` as **`Update requires:
> Replacement`**. So the deploy below does **not** update the data source in
> place — it **replaces** it. The new data source gets a **new
> `dataSourceId`**, and any ingestion job or `DS_ID` captured above becomes
> stale. Plan for replacement, not an in-place edit.

### 2. Deploy the CDK change (replaces the data source with a `NONE` one)

Because the change forces replacement and the data source `Name`
(`ckn-confluence-s3`) is unique within the KB, a plain `cdk deploy` would try to
**create** the new `ckn-confluence-s3` before **deleting** the old one and can
fail with a name `ConflictException`. Delete the existing data source first so
replacement is clean:

```bash
# Remove the old SEMANTIC data source (its vectors are dropped from the index;
# the S3 source objects are untouched). This is the reindex boundary.
aws bedrock-agent delete-data-source \
  --knowledge-base-id "$KB_ID" --data-source-id "$DS_ID" \
  --region "$CKN_REGION" --profile "$CKN_PROFILE"

cd src/infra
npx cdk diff --context deployKb=true     # confirm the DataSource is (re)created with NONE
npx cdk deploy --require-approval never --context deployKb=true
```

> **Expected diff:** the `AWS::Bedrock::DataSource` `ckn-confluence-s3` is
> created with `ChunkingConfiguration: { ChunkingStrategy: NONE }` (no
> `SemanticChunkingConfiguration`). If the diff shows the **KnowledgeBase** or
> the **AOSS collection/index** being replaced or removed, STOP — only the data
> source should change; the KB and vector index must survive.

> **Alternative (no manual delete):** temporarily give the data source a new
> `name` in the CDK (e.g. `ckn-confluence-s3-v2`) so CloudFormation's
> create-then-delete replacement doesn't collide on the name. This avoids the
> pre-delete but leaves a renamed resource; the manual-delete path keeps the
> name stable and is preferred here.

### 3. Re-derive the (new) data source ID and trigger a full reindex

The deploy created a **fresh** data source — look its ID up again; do not reuse
the `$DS_ID` from step 1.

```bash
DS_ID=$(aws bedrock-agent list-data-sources --knowledge-base-id "$KB_ID" \
  --region "$CKN_REGION" --profile "$CKN_PROFILE" \
  --query 'dataSourceSummaries[0].dataSourceId' --output text)
echo "New data source: $DS_ID"

aws bedrock-agent start-ingestion-job \
  --knowledge-base-id "$KB_ID" --data-source-id "$DS_ID" \
  --region "$CKN_REGION" --profile "$CKN_PROFILE"
```

A brand-new data source has no prior sync state, so its first ingestion job
embeds **every** object under `confluence/` (a full reindex) rather than doing
an incremental skip-if-unchanged sync.

If the on-disk S3 chunks predate the size-aware splitter (PR #21), re-run the
ingestion pipeline first so S3 holds correctly size-capped objects, then start
the job above (see [`POST_DEPLOYMENT.md` §6.4–6.5](../POST_DEPLOYMENT.md)).

### 4. Monitor the ingestion job to completion

```bash
JOB_ID=$(aws bedrock-agent list-ingestion-jobs \
  --knowledge-base-id "$KB_ID" --data-source-id "$DS_ID" \
  --region "$CKN_REGION" --profile "$CKN_PROFILE" \
  --sort-by '{"attribute":"STARTED_AT","order":"DESCENDING"}' \
  --query 'ingestionJobSummaries[0].ingestionJobId' --output text)

# Poll until status is COMPLETE (or FAILED).
aws bedrock-agent get-ingestion-job \
  --knowledge-base-id "$KB_ID" --data-source-id "$DS_ID" --ingestion-job-id "$JOB_ID" \
  --region "$CKN_REGION" --profile "$CKN_PROFILE" \
  --query 'ingestionJob.{status:status,stats:statistics}'
```

Watch for `numberOfDocumentsFailed > 0`. A document exceeding the embedding
model's token limit indicates a chunk that slipped past the size cap — treat as
a bug in the splitter, not a reindex step.

### 5. Verify retrieval

Run your known query set and confirm results are sensible and that chunks now
carry their `# {page_title}` prefix and heading context intact. The
`mcp__ckn-kb__QueryKnowledgeBases` tool (or a direct
`aws bedrock-agent-runtime retrieve`) works for a quick spot check.

## Rollback

If retrieval regresses or the ingestion job fails hard:

1. Revert the CDK change — restore `chunkingStrategy: SEMANTIC` with the values
   captured in `reindex-rollback-$DS_ID.json` (`maxTokens: 500`,
   `breakpointPercentileThreshold: 95`, `bufferSize: 1`).
2. Re-deploy. Because `chunkingConfiguration` is immutable, this is **another
   replacement**, not an in-place revert — delete the current (`NONE`) data
   source first (as in step 2) so the recreated `ckn-confluence-s3` doesn't
   collide on the name.
3. Re-derive the new `DS_ID` (step 3) and start a fresh ingestion job to
   re-embed under `SEMANTIC`.

Because the source objects live in S3 (SSE-KMS, `RemovalPolicy.RETAIN`), no
source content is lost across these replacements — only the vector index is
rebuilt each time. Each strategy flip is a delete-then-recreate of the data
source plus a full reindex; there is no in-place toggle.

## Related

- Issue #10 (F9 decision), issue #20 (Option A umbrella), PR #21 (splitter).
- Follow-on work unblocked by this switch: F5 (#5) size policy, F7 (#8) GFM
  tables, F2 (#2) generation manifest.
