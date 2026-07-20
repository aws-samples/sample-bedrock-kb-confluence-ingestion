"""CKN Ingestion Pipeline.

Extracts Confluence Cloud pages, processes image attachments via Bedrock Vision,
classifies documents using Claude, enriches metadata, and uploads content to S3
for Amazon Bedrock Knowledge Base ingestion.
"""

__version__ = "1.0.0"
