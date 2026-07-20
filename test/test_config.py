"""Tests for config.py — load_config and update_last_synced."""

from __future__ import annotations

import json

import pytest

from ckn_ingestion.config import CustomerConfig, load_config, update_last_synced

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_client_json(tmp_path, data: dict) -> object:
    p = tmp_path / "client.json"
    p.write_text(json.dumps(data))
    return p


def _minimal_customer(**overrides) -> dict:
    base = {
        "name": "acme",
        "account_id": "123456789012",
        "kb_id": "kb-xxx",
        "kb_region": "us-east-1",
        "status": "active",
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


def _valid_payload(**customer_overrides) -> dict:
    return {"version": "1", "customers": [_minimal_customer(**customer_overrides)]}


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_valid_file_returns_customer_config(self, tmp_path):
        path = _write_client_json(tmp_path, _valid_payload())
        cfg = load_config(path)

        assert isinstance(cfg, CustomerConfig)
        assert cfg.name == "acme"
        assert cfg.account_id == "123456789012"
        assert cfg.kb_id == "kb-xxx"
        assert cfg.kb_region == "us-east-1"
        assert cfg.status == "active"
        assert cfg.confluence.base_url == "https://acme.atlassian.net"
        assert cfg.confluence.spaces == ["OPS", "RUNBOOKS"]

    def test_missing_required_field_raises_key_error(self, tmp_path):
        payload = _valid_payload()
        del payload["customers"][0]["kb_id"]
        path = _write_client_json(tmp_path, payload)

        with pytest.raises(KeyError):
            load_config(path)

    def test_invalid_json_raises_decode_error(self, tmp_path):
        path = tmp_path / "client.json"
        path.write_text("{ not valid json }")

        with pytest.raises(json.JSONDecodeError):
            load_config(path)

    def test_kb_last_synced_is_none_when_not_set(self, tmp_path):
        payload = _valid_payload()
        # Explicitly set to null / None
        payload["customers"][0]["kb_last_synced"] = None
        path = _write_client_json(tmp_path, payload)

        cfg = load_config(path)
        assert cfg.kb_last_synced is None

    def test_kb_last_synced_is_populated_when_set(self, tmp_path):
        ts = "2024-01-15T12:00:00Z"
        path = _write_client_json(tmp_path, _valid_payload(kb_last_synced=ts))

        cfg = load_config(path)
        assert cfg.kb_last_synced == ts


# ---------------------------------------------------------------------------
# update_last_synced
# ---------------------------------------------------------------------------


class TestUpdateLastSynced:
    def test_updates_kb_last_synced_for_all_customers(self, tmp_path):
        customers = [_minimal_customer(name="acme"), _minimal_customer(name="beta")]
        payload = {"version": "1", "customers": customers}
        path = _write_client_json(tmp_path, payload)

        ts = "2024-06-01T00:00:00Z"
        update_last_synced(path, ts)

        updated = json.loads(path.read_text())
        for customer in updated["customers"]:
            assert customer["kb_last_synced"] == ts

    def test_file_is_valid_json_after_update(self, tmp_path):
        path = _write_client_json(tmp_path, _valid_payload())
        update_last_synced(path, "2024-06-01T00:00:00Z")

        # Must not raise
        data = json.loads(path.read_text())
        assert "customers" in data

    def test_original_file_is_replaced_with_updated_content(self, tmp_path):
        path = _write_client_json(tmp_path, _valid_payload())

        ts = "2024-07-01T00:00:00Z"
        update_last_synced(path, ts)

        data = json.loads(path.read_text())
        assert data["customers"][0]["kb_last_synced"] == ts
        # On Linux os.replace keeps the same path; the content must be updated
        assert path.exists()

    def test_raises_on_invalid_path(self, tmp_path):
        missing = tmp_path / "nonexistent" / "client.json"

        with pytest.raises((FileNotFoundError, OSError)):
            update_last_synced(missing, "2024-01-01T00:00:00Z")
