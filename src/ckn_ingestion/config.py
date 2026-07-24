"""Configuration loading and management for the CKN Ingestion Pipeline.

Uses Pydantic for strict validation of client.json payloads.  Public
API: ``ConfluenceConfig``, ``AppConfig``, ``load_config``, ``resolve_config``,
``update_last_synced``.

The deployment config can come from one of three sources, resolved in order by
``resolve_config`` (see that function): an SSM Parameter Store parameter
(``CKN_CONFIG_SSM_PARAM``), an S3 object (``CKN_CONFIG_S3_URI``), or a local
file (default). Externalizing config means the container image is
config-agnostic — routine changes (e.g. adding a space) no longer require an
image rebuild. Secrets are unaffected: the Confluence token still lives in
Secrets Manager, never in this config.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, Field, ValidationError

from ckn_ingestion.size_policy import DEFAULT_MAX_BODY_BYTES

logger = logging.getLogger(__name__)

# Env vars selecting an externalized config source (checked by resolve_config).
ENV_SSM_PARAM = "CKN_CONFIG_SSM_PARAM"
ENV_S3_URI = "CKN_CONFIG_S3_URI"
ENV_CONFIG_PATH = "CKN_CONFIG_PATH"


# ---------------------------------------------------------------------------
# Pydantic models — strict validation of client.json
# ---------------------------------------------------------------------------


class ConfluenceConfig(BaseModel):
    """Confluence connection settings."""

    base_url: str = Field(..., min_length=1)
    # kms_key_arn / kms_secret_id are AWS deployment concerns (S3 encryption
    # key and the Secrets Manager entry holding the Confluence token). They
    # live under the confluence block for historical reasons.
    kms_key_arn: str = Field(..., min_length=1)
    kms_secret_id: str = Field(..., min_length=1)
    spaces: list[str] = Field(..., min_length=1)

    class Config:
        extra = "ignore"


class AppConfig(BaseModel):
    """Top-level deployment configuration (one client.json per deployment)."""

    kb_id: str = Field(..., min_length=1)
    kb_region: str = Field(..., min_length=1)
    kb_last_synced: str | None = None
    # F5 ingestion size policy: pages whose (post-extraction markdown) body
    # exceeds this many UTF-8 bytes are indexed as a title + summary + source-link
    # placeholder instead of having their low-signal body (e.g. a row-by-row table
    # dump) embedded whole. Distinct from confluence_extractor.MAX_PAGE_BODY_BYTES,
    # which is a much larger HTML-body hard-skip safety guard at fetch time.
    # Optional; defaults to size_policy.DEFAULT_MAX_BODY_BYTES when omitted.
    max_indexable_body_bytes: int = Field(default=DEFAULT_MAX_BODY_BYTES, gt=0)
    confluence: ConfluenceConfig

    class Config:
        extra = "forbid"


def _validate_payload(data: object) -> AppConfig:
    """Validate a parsed config payload (shared by all sources).

    Raises:
        ValueError: if the payload uses the retired multi-tenant format,
            is missing required fields, or fails field validation.
    """
    if isinstance(data, dict) and "customers" in data:
        raise ValueError(
            "client.json uses the retired multi-tenant format (top-level 'customers' "
            "array). The schema is now a flat single-deployment object: move the "
            "fields of the customer entry to the top level. See the committed "
            "client.json or the 'Configuration' section of README.md for the schema."
        )

    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Invalid client.json: {exc}") from exc


def load_config(path: Path) -> AppConfig:
    """Parse and validate a local client.json file.

    Raises:
        FileNotFoundError: if the config file does not exist.
        ValueError: if the payload uses the retired multi-tenant format,
            is missing required fields, or fails field validation.
        json.JSONDecodeError: if the file is not valid JSON.
    """
    with open(path) as f:
        data = json.load(f)
    return _validate_payload(data)


class ConfigSource(NamedTuple):
    """Where the resolved config came from, so the caller knows whether/how to
    write ``kb_last_synced`` back. ``kind`` is ``"file"``, ``"ssm"``, or ``"s3"``;
    ``ref`` is the path / parameter name / ``bucket,key`` respectively.
    """

    kind: str
    ref: str


def _read_ssm_parameter(name: str) -> str:
    """Fetch a parameter value from SSM Parameter Store."""
    import boto3  # local import: keeps boto3 off the import path for file-only use

    ssm = boto3.client("ssm")
    resp = ssm.get_parameter(Name=name, WithDecryption=True)
    return resp["Parameter"]["Value"]


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split ``s3://bucket/key`` into ``(bucket, key)``."""
    if not uri.startswith("s3://"):
        raise ValueError(f"{ENV_S3_URI} must be an s3://bucket/key URI, got: {uri!r}")
    without_scheme = uri[len("s3://") :]
    bucket, _, key = without_scheme.partition("/")
    if not bucket or not key:
        raise ValueError(f"{ENV_S3_URI} must be an s3://bucket/key URI, got: {uri!r}")
    return bucket, key


def _read_s3_object(uri: str) -> str:
    """Fetch an object body from S3 given an ``s3://bucket/key`` URI."""
    import boto3  # local import: only needed when S3 config is selected

    bucket, key = _parse_s3_uri(uri)
    s3 = boto3.client("s3")
    resp = s3.get_object(Bucket=bucket, Key=key)
    return resp["Body"].read().decode("utf-8")


def resolve_config(default_path: Path) -> tuple[AppConfig, ConfigSource]:
    """Load config from the first configured source, in priority order:

    1. ``CKN_CONFIG_SSM_PARAM`` — an SSM Parameter Store parameter name.
    2. ``CKN_CONFIG_S3_URI`` — an ``s3://bucket/key`` URI.
    3. ``CKN_CONFIG_PATH`` env var, else ``default_path`` — a local file.

    Externalized sources make the container image config-agnostic. All sources
    share the same Pydantic validation. Returns the validated config plus a
    ``ConfigSource`` describing where it came from (so callers know whether a
    ``kb_last_synced`` write-back is possible).

    Raises:
        ValueError: on an invalid payload or malformed S3 URI.
        json.JSONDecodeError: if the source content is not valid JSON.
        botocore exceptions: on SSM/S3 fetch failures.
        FileNotFoundError: if the local file source does not exist.
    """
    ssm_param = os.environ.get(ENV_SSM_PARAM)
    if ssm_param:
        logger.info("Loading config from SSM parameter %s", ssm_param)
        data = json.loads(_read_ssm_parameter(ssm_param))
        return _validate_payload(data), ConfigSource("ssm", ssm_param)

    s3_uri = os.environ.get(ENV_S3_URI)
    if s3_uri:
        logger.info("Loading config from S3 object %s", s3_uri)
        data = json.loads(_read_s3_object(s3_uri))
        return _validate_payload(data), ConfigSource("s3", s3_uri)

    path = Path(os.environ.get(ENV_CONFIG_PATH, str(default_path)))
    logger.info("Loading config from local file %s", path)
    return load_config(path), ConfigSource("file", str(path))


def update_last_synced_source(source: ConfigSource, timestamp: str) -> bool:
    """Write ``kb_last_synced`` back to the config, if the source supports it.

    Only the local-file source is written in place (atomically). For
    externalized sources (SSM/S3) the write-back is intentionally skipped: the
    pipeline is stateless and its incremental-sync semantics do not depend on
    persisting this field, and rewriting a shared parameter/object from the task
    is undesirable. Returns True if a write occurred, False if skipped.
    """
    if source.kind == "file":
        update_last_synced(Path(source.ref), timestamp)
        return True
    logger.info(
        "Config source is %s (%s); skipping kb_last_synced write-back "
        "(externalized config is not rewritten by the task).",
        source.kind,
        source.ref,
    )
    return False


def update_last_synced(path: Path, timestamp: str) -> None:
    """Atomically update kb_last_synced in a local client.json.

    Writes to a temporary file in the same directory, then renames it over
    the original to ensure atomicity (no partial writes visible to readers).
    """
    with open(path) as f:
        data = json.load(f)

    data["kb_last_synced"] = timestamp

    dir_path = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        # Clean up temp file on failure; do not swallow the exception.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    logger.info("Updated kb_last_synced to %s in %s", timestamp, path)
