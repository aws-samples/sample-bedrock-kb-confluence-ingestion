"""Configuration loading and management for the CKN Ingestion Pipeline.

Uses Pydantic for strict validation of client.json payloads.  The public
API (``ConfluenceConfig``, ``CustomerConfig``, ``load_config``) is
unchanged — callers still receive the same attribute names and types.
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
    kms_key_arn: str = Field(..., min_length=1)
    kms_secret_id: str = Field(..., min_length=1)
    spaces: list[str] = Field(..., min_length=1)

    class Config:
        extra = "ignore"


class CustomerConfig(BaseModel):
    """Top-level customer configuration."""

    name: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    kb_id: str = Field(..., min_length=1)
    kb_region: str = Field(..., min_length=1)
    status: str = Field(..., min_length=1)
    kb_last_synced: str | None = None
    confluence: ConfluenceConfig

    class Config:
        extra = "ignore"


class _ClientFile(BaseModel):
    """Schema for the top-level client.json file."""

    customers: list[CustomerConfig] = Field(..., min_length=1)

    class Config:
        extra = "ignore"


def load_config(path: Path) -> CustomerConfig:
    """Parse and validate client.json, returning the first customer.

    Raises:
        FileNotFoundError: if the config file does not exist.
        KeyError: if required fields are missing.
        ValueError: if field values fail validation.
        json.JSONDecodeError: if the file is not valid JSON.
    """
    with open(path) as f:
        data = json.load(f)

    try:
        client_file = _ClientFile.model_validate(data)
    except ValidationError as exc:
        # Preserve KeyError for missing-field cases so existing callers
        # (and tests) that catch KeyError continue to work.
        for error in exc.errors():
            if error["type"] in ("missing", "value_error.missing"):
                field_path = ".".join(str(loc) for loc in error["loc"])
                raise KeyError(field_path) from exc
        raise ValueError(f"Invalid client.json: {exc}") from exc

    return client_file.customers[0]


def update_last_synced(path: Path, timestamp: str) -> None:
    """Atomically update kb_last_synced for all customers in client.json.

    Writes to a temporary file in the same directory, then renames it over
    the original to ensure atomicity (no partial writes visible to readers).
    """
    with open(path) as f:
        data = json.load(f)

    for customer in data.get("customers", []):
        customer["kb_last_synced"] = timestamp

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
