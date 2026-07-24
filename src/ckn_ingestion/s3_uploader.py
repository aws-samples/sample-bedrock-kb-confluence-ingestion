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


def _page_key_pattern(safe_space: str, safe_page_id: str) -> re.Pattern[str]:
    """Regex matching exactly this page's object keys (content + sidecars).

    Anchored on the full key so page ``123`` never matches page ``1234``'s
    objects. Matches ``{page_id}.md`` and ``{page_id}_chunk_{N}.md`` plus their
    ``.metadata.json`` sidecars, and nothing else under the space prefix.
    """
    prefix = re.escape(f"confluence/{safe_space}/{safe_page_id}")
    return re.compile(rf"^{prefix}(_chunk_\d+)?\.md(\.metadata\.json)?$")


def _delete_orphan_objects(
    s3_client: Any,
    bucket: str,
    safe_space: str,
    safe_page_id: str,
    written_keys: set[str],
    page_id: str,
) -> None:
    """Delete stale objects for this page that were not (re)written this run.

    Lists existing objects under the page's key prefix, keeps only those whose
    key belongs to this exact page (via :func:`_page_key_pattern`), and deletes
    any not present in *written_keys*. Best-effort: list/delete failures are
    logged and swallowed so cleanup never aborts an otherwise-successful upload.
    """
    list_prefix = f"confluence/{safe_space}/{safe_page_id}"
    key_re = _page_key_pattern(safe_space, safe_page_id)

    existing_keys: list[str] = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=list_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key_re.match(key):
                    existing_keys.append(key)
    except Exception as exc:
        logger.error(
            "Failed to list existing objects for orphan cleanup of page '%s': %s",
            page_id,
            type(exc).__name__,
        )
        return

    orphan_keys = [k for k in existing_keys if k not in written_keys]
    if not orphan_keys:
        return

    # Delete in batches of 1000 (S3 delete_objects limit).
    deleted = 0
    for start in range(0, len(orphan_keys), 1000):
        batch = orphan_keys[start : start + 1000]
        try:
            resp = s3_client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
            # In Quiet mode S3 returns only per-key Errors; count actual deletions
            # (batch minus failures) so ORPHANS_DELETED reflects reality.
            errors = resp.get("Errors", []) if isinstance(resp, dict) else []
            deleted += len(batch) - len(errors)
            for err in errors:
                logger.error(
                    "Failed to delete orphan object for page '%s': key=%s code=%s",
                    page_id,
                    err.get("Key"),
                    err.get("Code"),
                )
        except Exception as exc:
            logger.error(
                "Failed to delete orphan objects for page '%s': %s",
                page_id,
                type(exc).__name__,
            )

    if deleted:
        # Stable token ORPHANS_DELETED drives a CloudWatch metric filter, matching
        # the PAGE_BODY_OVERSIZE / INGESTION_RUN_COMPLETE marker idiom.
        logger.info(
            "ORPHANS_DELETED page_id=%s count=%d — stale chunk generation removed",
            page_id,
            deleted,
        )


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

    written_keys: set[str] = set()
    all_writes_ok = True

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
            written_keys.add(content_key)
        except Exception as exc:
            all_writes_ok = False
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
            written_keys.add(sidecar_key)
        except Exception as exc:
            all_writes_ok = False
            logger.error(
                "Failed to upload sidecar for page '%s': %s",
                page_id,
                type(exc).__name__,
            )

    # F2: reconcile orphan generations. A content change that shifts chunk
    # boundaries (or the single-chunk ↔ multi-chunk transition) leaves the
    # previous run's objects behind under keys we no longer write; with the KB's
    # DELETE deletion policy those stale objects keep producing duplicate vectors
    # until removed. Delete any existing object under this page's prefix that we
    # did not just (re)write. Guarded on all_writes_ok so a partial write failure
    # never deletes the only surviving copy of the content.
    if all_writes_ok:
        _delete_orphan_objects(s3_client, bucket, safe_space, safe_page_id, written_keys, page_id)
    else:
        logger.warning(
            "Skipping orphan cleanup for page '%s' — not all objects uploaded successfully.",
            page_id,
        )
