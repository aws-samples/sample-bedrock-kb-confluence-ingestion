"""Tests for config.py — load_config and update_last_synced."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ckn_ingestion.config import AppConfig, load_config, update_last_synced

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_client_json(tmp_path, data: dict) -> object:
    p = tmp_path / "client.json"
    p.write_text(json.dumps(data))
    return p


def _valid_payload(**overrides) -> dict:
    base = {
        "kb_id": "kb-xxx",
        "kb_region": "us-east-1",
        "kb_last_synced": None,
        "confluence": {
            "base_url": "https://acme.atlassian.net",
            "kms_key_arn": "arn:aws:kms:us-east-1:123:key/abc",
            "kms_secret_id": "ams/ckn/confluence-token",
            "spaces": ["OPS", "RUNBOOKS"],
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_valid_file_returns_app_config(self, tmp_path):
        path = _write_client_json(tmp_path, _valid_payload())
        cfg = load_config(path)

        assert isinstance(cfg, AppConfig)
        assert cfg.kb_id == "kb-xxx"
        assert cfg.kb_region == "us-east-1"
        assert cfg.confluence.base_url == "https://acme.atlassian.net"
        assert cfg.confluence.spaces == ["OPS", "RUNBOOKS"]

    def test_missing_required_field_raises_value_error(self, tmp_path):
        payload = _valid_payload()
        del payload["kb_id"]
        path = _write_client_json(tmp_path, payload)

        with pytest.raises(ValueError, match="kb_id"):
            load_config(path)

    def test_unknown_top_level_field_is_rejected(self, tmp_path):
        path = _write_client_json(tmp_path, _valid_payload(unexpected_field="oops"))

        with pytest.raises(ValueError, match="unexpected_field"):
            load_config(path)

    def test_old_multi_tenant_format_raises_descriptive_error(self, tmp_path):
        """The retired {"customers": [...]} format must fail with a clear
        migration message, not a KeyError or an opaque validation error."""
        old_format = {"customers": [_valid_payload(name="acme", status="active")]}
        path = _write_client_json(tmp_path, old_format)

        with pytest.raises(ValueError, match="multi-tenant format"):
            load_config(path)

    def test_invalid_json_raises_decode_error(self, tmp_path):
        path = tmp_path / "client.json"
        path.write_text("{ not valid json }")

        with pytest.raises(json.JSONDecodeError):
            load_config(path)

    def test_kb_last_synced_is_none_when_not_set(self, tmp_path):
        payload = _valid_payload()
        payload["kb_last_synced"] = None
        path = _write_client_json(tmp_path, payload)

        cfg = load_config(path)
        assert cfg.kb_last_synced is None

    def test_kb_last_synced_is_populated_when_set(self, tmp_path):
        ts = "2024-01-15T12:00:00Z"
        path = _write_client_json(tmp_path, _valid_payload(kb_last_synced=ts))

        cfg = load_config(path)
        assert cfg.kb_last_synced == ts

    def test_committed_sample_client_json_parses(self):
        """The client.json shipped in the repo must always match the schema."""
        sample = Path(__file__).resolve().parent.parent / "client.json"
        cfg = load_config(sample)

        assert isinstance(cfg, AppConfig)
        assert cfg.kb_id
        assert cfg.confluence.spaces

    def test_max_indexable_body_bytes_defaults_when_omitted(self, tmp_path):
        # F5: field is optional; omitting it yields the module default.
        from ckn_ingestion.size_policy import DEFAULT_MAX_BODY_BYTES

        path = _write_client_json(tmp_path, _valid_payload())
        cfg = load_config(path)
        assert cfg.max_indexable_body_bytes == DEFAULT_MAX_BODY_BYTES

    def test_max_indexable_body_bytes_override_is_honored(self, tmp_path):
        path = _write_client_json(tmp_path, _valid_payload(max_indexable_body_bytes=250_000))
        cfg = load_config(path)
        assert cfg.max_indexable_body_bytes == 250_000

    def test_max_indexable_body_bytes_must_be_positive(self, tmp_path):
        path = _write_client_json(tmp_path, _valid_payload(max_indexable_body_bytes=0))
        with pytest.raises(ValueError):
            load_config(path)


# ---------------------------------------------------------------------------
# update_last_synced
# ---------------------------------------------------------------------------


class TestUpdateLastSynced:
    def test_updates_kb_last_synced(self, tmp_path):
        path = _write_client_json(tmp_path, _valid_payload())

        ts = "2024-06-01T00:00:00Z"
        update_last_synced(path, ts)

        updated = json.loads(path.read_text())
        assert updated["kb_last_synced"] == ts

    def test_file_is_valid_json_after_update(self, tmp_path):
        path = _write_client_json(tmp_path, _valid_payload())
        update_last_synced(path, "2024-06-01T00:00:00Z")

        data = json.loads(path.read_text())
        assert "kb_id" in data

    def test_original_file_is_replaced_with_updated_content(self, tmp_path):
        path = _write_client_json(tmp_path, _valid_payload())

        ts = "2024-07-01T00:00:00Z"
        update_last_synced(path, ts)

        data = json.loads(path.read_text())
        assert data["kb_last_synced"] == ts
        assert path.exists()

    def test_raises_on_invalid_path(self, tmp_path):
        missing = tmp_path / "nonexistent" / "client.json"

        with pytest.raises((FileNotFoundError, OSError)):
            update_last_synced(missing, "2024-01-01T00:00:00Z")
