"""S3 uploader — upload page content and metadata sidecar to S3 with SSE-KMS."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ckn_ingestion.models import MetadataSidecar

logger = logging.getLogger(__name__)

# Allowlist pattern for S3 key path components: alphanumeric, hyphens,
# underscores, dots. Leading `~` is permitted because Confluence personal-space
# keys always start with `~` followed by a hex user ID
# (e.g. `~5b58bdf9e288ee2d9b4ba4fe`).
_SAFE_KEY_COMPONENT = re.compile(r"^[A-Za-z0-9~][A-Za-z0-9._-]*$")


def _sanitize_key_component(value: str, label: str) -> str:
    """Validate and sanitize a value used as an S3 key path component.

    Rejects values containing path traversal sequences, path separators,
    or characters outside the safe allowlist.

    Args:
        value: The raw value (e.g. space_key or page_id).
        label: Human-readable label for error messages.

    Returns:
        The validated value (unchanged if it passes).

    Raises:
        ValueError: If the value is empty, contains path traversal, or
                    has characters outside the allowlist.
    """
    if not value:
        raise ValueError(f"{label} must not be empty")
    if ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"{label} contains path traversal or separator characters: {value!r}")
    if not _SAFE_KEY_COMPONENT.match(value):
        raise ValueError(
            f"{label} contains disallowed characters: {value!r}. "
            "Allowed: alphanumeric, hyphens, underscores, dots, and a leading tilde."
        )
    # Reject a bare `~` — personal-space keys are always `~` + hex ID
    if value == "~":
        raise ValueError(f"{label} must not be a bare tilde: {value!r}")
    return value


def upload_page(
    s3_client: Any,
    account_id: str,
    space_key: str,
    page_id: str,
    chunks: list[str],
    sidecar: MetadataSidecar,
    kms_key_arn: str = "",
) -> None:
    """Upload page chunks and metadata sidecars to S3 with SSE-KMS encryption.

    For single-chunk pages: ``confluence/{space_key}/{page_id}.md``
    For multi-chunk pages:  ``confluence/{space_key}/{page_id}_chunk_{i}.md``

    Each content object gets a paired ``.md.metadata.json`` sidecar.
    ContentType is ``text/markdown``.

    On failure of any individual upload, logs the page ID and error to stderr
    and continues without raising.  The KMS key ARN is never logged.

    Args:
        s3_client: Boto3 S3 client.
        account_id: AWS account ID used to derive the bucket name.
        space_key: Confluence space key (used as S3 prefix).
        page_id: Confluence page ID (used as S3 object name).
        chunks: List of markdown chunks to upload.
        sidecar: Metadata sidecar to serialize and upload alongside each chunk.
        kms_key_arn: KMS key ARN for server-side encryption.
    """
    if not chunks:
        logger.warning("Empty chunks list for page '%s' — skipping upload.", page_id)
        return

    # Validate key components to prevent path injection (SEC-048)
    safe_space = _sanitize_key_component(space_key, "space_key")
    safe_page_id = _sanitize_key_component(page_id, "page_id")

    bucket = f"ams-ckn-{account_id}"
    sidecar_json = json.dumps({"metadataAttributes": sidecar.metadata_attributes}, indent=2)

    for i, chunk in enumerate(chunks):
        if len(chunks) == 1:
            content_key = f"confluence/{safe_space}/{safe_page_id}.md"
        else:
            content_key = f"confluence/{safe_space}/{safe_page_id}_chunk_{i}.md"
        sidecar_key = f"{content_key}.metadata.json"

        # Upload chunk content
        try:
            s3_client.put_object(
                Bucket=bucket,
                Key=content_key,
                Body=chunk.encode("utf-8"),
                ContentType="text/markdown",
                ServerSideEncryption="aws:kms",
                SSEKMSKeyId=kms_key_arn,
            )
        except Exception as exc:
            logger.error(
                "Failed to upload content for page '%s': %s",
                page_id,
                type(exc).__name__,
            )

        # Upload sidecar JSON
        try:
            s3_client.put_object(
                Bucket=bucket,
                Key=sidecar_key,
                Body=sidecar_json.encode("utf-8"),
                ContentType="application/json",
                ServerSideEncryption="aws:kms",
                SSEKMSKeyId=kms_key_arn,
            )
        except Exception as exc:
            logger.error(
                "Failed to upload sidecar for page '%s': %s",
                page_id,
                type(exc).__name__,
            )
