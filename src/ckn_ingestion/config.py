"""Configuration loading and management for the CKN Ingestion Pipeline.

Uses Pydantic for strict validation of client.json payloads.  Public
API: ``ConfluenceConfig``, ``AppConfig``, ``load_config``, ``update_last_synced``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


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
    confluence: ConfluenceConfig

    class Config:
        extra = "forbid"


def load_config(path: Path) -> AppConfig:
    """Parse and validate client.json.

    Raises:
        FileNotFoundError: if the config file does not exist.
        ValueError: if the payload uses the retired multi-tenant format,
            is missing required fields, or fails field validation.
        json.JSONDecodeError: if the file is not valid JSON.
    """
    with open(path) as f:
        data = json.load(f)

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


def update_last_synced(path: Path, timestamp: str) -> None:
    """Atomically update kb_last_synced in client.json.

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
