"""Unit tests for s3_uploader module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from ckn_ingestion.models import MetadataSidecar
from ckn_ingestion.s3_uploader import upload_page

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sidecar(**overrides) -> MetadataSidecar:
    attrs = {
        "doc_type": "runbook",
        "service": "rds",
        "severity_relevance": "sev1",
        "owner_team": "database-ops",
        "region": "us-east-1",
        "source_url": "https://acme.atlassian.net/wiki/spaces/OPS/pages/12345",
        "last_synced": "2026-03-22T14:00:00Z",
        "confluence_space": "OPS",
        "confluence_author": "jsmith",
        "summary": "Procedure for failing over RDS instances.",
        "has_images": "true",
    }
    attrs.update(overrides)
    return MetadataSidecar(metadata_attributes=attrs)


def _make_s3_client(existing_keys: list[str] | None = None) -> MagicMock:
    """Mock S3 client. ``existing_keys`` seeds what the orphan-cleanup paginator
    "finds" already in the bucket for the page prefix (default: none)."""
    client = MagicMock()
    client.put_object.return_value = {}
    client.delete_objects.return_value = {}

    pages = [{"Contents": [{"Key": k} for k in (existing_keys or [])]}]
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    client.get_paginator.return_value = paginator
    return client


_TEST_KMS_KEY_ARN = "arn:aws:kms:us-east-1:123456789012:key/test-key-id"


# ---------------------------------------------------------------------------
# S3 bucket and key naming
# ---------------------------------------------------------------------------


class TestS3Paths:
    def test_bucket_name_includes_account_id(self):
        client = _make_s3_client()
        upload_page(
            client,
            "123456789012",
            "OPS",
            "12345",
            ["# Content"],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        calls = client.put_object.call_args_list
        for c in calls:
            assert c.kwargs["Bucket"] == "ams-ckn-123456789012"

    def test_content_key_format(self):
        client = _make_s3_client()
        upload_page(
            client,
            "123456789012",
            "OPS",
            "12345",
            ["# Content"],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        content_call = client.put_object.call_args_list[0]
        assert content_call.kwargs["Key"] == "confluence/OPS/12345.md"

    def test_sidecar_key_format(self):
        client = _make_s3_client()
        upload_page(
            client,
            "123456789012",
            "OPS",
            "12345",
            ["# Content"],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        sidecar_call = client.put_object.call_args_list[1]
        assert sidecar_call.kwargs["Key"] == "confluence/OPS/12345.md.metadata.json"

    def test_sidecar_key_not_metadata_json_without_md(self):
        """Key must be {page_id}.md.metadata.json, NOT {page_id}.metadata.json."""
        client = _make_s3_client()
        upload_page(
            client,
            "123456789012",
            "OPS",
            "99999",
            ["content"],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        sidecar_call = client.put_object.call_args_list[1]
        key = sidecar_call.kwargs["Key"]
        assert key == "confluence/OPS/99999.md.metadata.json"
        assert key != "confluence/OPS/99999.metadata.json"

    def test_space_key_used_in_path(self):
        client = _make_s3_client()
        upload_page(
            client,
            "111111111111",
            "INFRA",
            "42",
            ["content"],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        for c in client.put_object.call_args_list:
            assert "INFRA" in c.kwargs["Key"]


# ---------------------------------------------------------------------------
# PutObject call parameters
# ---------------------------------------------------------------------------


class TestPutObjectParams:
    def test_content_body_is_utf8_encoded(self):
        client = _make_s3_client()
        upload_page(
            client,
            "123456789012",
            "OPS",
            "12345",
            ["Hello World"],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        content_call = client.put_object.call_args_list[0]
        assert content_call.kwargs["Body"] == b"Hello World"

    def test_content_type_is_text_markdown(self):
        client = _make_s3_client()
        upload_page(
            client,
            "123456789012",
            "OPS",
            "12345",
            ["content"],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        content_call = client.put_object.call_args_list[0]
        assert content_call.kwargs["ContentType"] == "text/markdown"

    def test_content_sse_kms(self):
        client = _make_s3_client()
        upload_page(
            client,
            "123456789012",
            "OPS",
            "12345",
            ["content"],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        content_call = client.put_object.call_args_list[0]
        assert content_call.kwargs["ServerSideEncryption"] == "aws:kms"

    def test_sidecar_sse_kms(self):
        client = _make_s3_client()
        upload_page(
            client,
            "123456789012",
            "OPS",
            "12345",
            ["content"],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        sidecar_call = client.put_object.call_args_list[1]
        assert sidecar_call.kwargs["ServerSideEncryption"] == "aws:kms"

    def test_content_sse_kms_key_id(self):
        client = _make_s3_client()
        upload_page(
            client,
            "123456789012",
            "OPS",
            "12345",
            ["content"],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        content_call = client.put_object.call_args_list[0]
        assert content_call.kwargs["SSEKMSKeyId"] == _TEST_KMS_KEY_ARN

    def test_sidecar_sse_kms_key_id(self):
        client = _make_s3_client()
        upload_page(
            client,
            "123456789012",
            "OPS",
            "12345",
            ["content"],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        sidecar_call = client.put_object.call_args_list[1]
        assert sidecar_call.kwargs["SSEKMSKeyId"] == _TEST_KMS_KEY_ARN

    def test_two_put_object_calls_made(self):
        client = _make_s3_client()
        upload_page(
            client,
            "123456789012",
            "OPS",
            "12345",
            ["content"],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        assert client.put_object.call_count == 2


# ---------------------------------------------------------------------------
# Sidecar JSON format
# ---------------------------------------------------------------------------


class TestSidecarJsonFormat:
    def test_sidecar_body_is_valid_json(self):
        client = _make_s3_client()
        sidecar = _make_sidecar()
        upload_page(
            client,
            "123456789012",
            "OPS",
            "12345",
            ["content"],
            sidecar,
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        sidecar_call = client.put_object.call_args_list[1]
        body_bytes = sidecar_call.kwargs["Body"]
        parsed = json.loads(body_bytes.decode("utf-8"))
        assert isinstance(parsed, dict)

    def test_sidecar_has_metadata_attributes_key(self):
        client = _make_s3_client()
        sidecar = _make_sidecar()
        upload_page(
            client,
            "123456789012",
            "OPS",
            "12345",
            ["content"],
            sidecar,
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        sidecar_call = client.put_object.call_args_list[1]
        parsed = json.loads(sidecar_call.kwargs["Body"].decode("utf-8"))
        assert "metadataAttributes" in parsed

    def test_sidecar_attributes_match_input(self):
        client = _make_s3_client()
        sidecar = _make_sidecar(doc_type="architecture", service="ec2")
        upload_page(
            client,
            "123456789012",
            "OPS",
            "12345",
            ["content"],
            sidecar,
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        sidecar_call = client.put_object.call_args_list[1]
        parsed = json.loads(sidecar_call.kwargs["Body"].decode("utf-8"))
        assert parsed["metadataAttributes"]["doc_type"] == "architecture"
        assert parsed["metadataAttributes"]["service"] == "ec2"


# ---------------------------------------------------------------------------
# Error handling — no raise on failure
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_content_upload_failure_does_not_raise(self):
        client = _make_s3_client()
        client.put_object.side_effect = RuntimeError("S3 unavailable")
        # Should not raise
        upload_page(
            client,
            "123456789012",
            "OPS",
            "12345",
            ["content"],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )

    def test_sidecar_upload_failure_does_not_raise(self):
        client = _make_s3_client()
        # First call (content) succeeds, second (sidecar) fails
        client.put_object.side_effect = [None, RuntimeError("S3 unavailable")]
        upload_page(
            client,
            "123456789012",
            "OPS",
            "12345",
            ["content"],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )

    def test_content_failure_logs_page_id(self, caplog):
        client = _make_s3_client()
        client.put_object.side_effect = RuntimeError("boom")
        with caplog.at_level("ERROR", logger="ckn_ingestion.s3_uploader"):
            upload_page(
                client,
                "123456789012",
                "OPS",
                "page-abc",
                ["content"],
                _make_sidecar(),
                kms_key_arn=_TEST_KMS_KEY_ARN,
            )
        assert "page-abc" in caplog.text

    def test_sidecar_failure_logs_page_id(self, caplog):
        client = _make_s3_client()
        client.put_object.side_effect = [None, RuntimeError("boom")]
        with caplog.at_level("ERROR", logger="ckn_ingestion.s3_uploader"):
            upload_page(
                client,
                "123456789012",
                "OPS",
                "page-xyz",
                ["content"],
                _make_sidecar(),
                kms_key_arn=_TEST_KMS_KEY_ARN,
            )
        assert "page-xyz" in caplog.text


# ---------------------------------------------------------------------------
# F2: orphan-generation cleanup
# ---------------------------------------------------------------------------


def _deleted_keys(client) -> set[str]:
    """Collect every key passed to delete_objects across all calls."""
    keys: set[str] = set()
    for call in client.delete_objects.call_args_list:
        for obj in call.kwargs["Delete"]["Objects"]:
            keys.add(obj["Key"])
    return keys


class TestOrphanCleanup:
    def test_no_existing_objects_no_delete(self):
        client = _make_s3_client(existing_keys=[])
        upload_page(
            client, "123456789012", "OPS", "12345", ["# Content"], _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        client.delete_objects.assert_not_called()

    def test_stale_chunks_from_previous_generation_deleted(self):
        # Previous run wrote 3 chunks; this run writes a single chunk. The two
        # now-unused chunk objects (and their sidecars) must be deleted.
        existing = [
            "confluence/OPS/12345_chunk_0.md",
            "confluence/OPS/12345_chunk_0.md.metadata.json",
            "confluence/OPS/12345_chunk_1.md",
            "confluence/OPS/12345_chunk_1.md.metadata.json",
            "confluence/OPS/12345_chunk_2.md",
            "confluence/OPS/12345_chunk_2.md.metadata.json",
        ]
        client = _make_s3_client(existing_keys=existing)
        upload_page(
            client, "123456789012", "OPS", "12345", ["# Single now"], _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        deleted = _deleted_keys(client)
        # All six old chunk-generation objects removed...
        assert set(existing) == deleted
        # ...and the just-written single-chunk objects are NOT deleted.
        assert "confluence/OPS/12345.md" not in deleted
        assert "confluence/OPS/12345.md.metadata.json" not in deleted

    def test_rewritten_keys_are_not_deleted(self):
        # Existing objects that match this run's write-set must be preserved.
        existing = ["confluence/OPS/12345.md", "confluence/OPS/12345.md.metadata.json"]
        client = _make_s3_client(existing_keys=existing)
        upload_page(
            client, "123456789012", "OPS", "12345", ["# Content"], _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        client.delete_objects.assert_not_called()

    def test_other_pages_objects_are_never_touched(self):
        # Page 123 must not delete page 1234's objects (prefix collision guard).
        existing = [
            "confluence/OPS/123.md",  # this page, old single-chunk (orphan now)
            "confluence/OPS/1234.md",  # DIFFERENT page — must survive
            "confluence/OPS/1234_chunk_0.md",  # DIFFERENT page — must survive
        ]
        client = _make_s3_client(existing_keys=existing)
        # This run writes page 123 as 2 chunks.
        upload_page(
            client, "123456789012", "OPS", "123", ["# a", "# b"], _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        deleted = _deleted_keys(client)
        assert "confluence/OPS/123.md" in deleted  # orphaned single-chunk of THIS page
        assert "confluence/OPS/1234.md" not in deleted
        assert "confluence/OPS/1234_chunk_0.md" not in deleted

    def test_no_cleanup_when_a_write_failed(self):
        # If any put_object failed, cleanup must be skipped to avoid deleting the
        # only surviving copy of the content.
        existing = ["confluence/OPS/12345_chunk_9.md"]
        client = _make_s3_client(existing_keys=existing)
        client.put_object.side_effect = RuntimeError("S3 down")
        upload_page(
            client, "123456789012", "OPS", "12345", ["# Content"], _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        client.delete_objects.assert_not_called()

    def test_list_failure_does_not_raise_or_delete(self):
        client = _make_s3_client()
        client.get_paginator.side_effect = RuntimeError("list denied")
        # Must not raise; must not delete.
        upload_page(
            client, "123456789012", "OPS", "12345", ["# Content"], _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        client.delete_objects.assert_not_called()

    def test_delete_failure_does_not_raise(self, caplog):
        existing = ["confluence/OPS/12345_chunk_0.md"]
        client = _make_s3_client(existing_keys=existing)
        client.delete_objects.side_effect = RuntimeError("delete denied")
        with caplog.at_level("ERROR", logger="ckn_ingestion.s3_uploader"):
            upload_page(
                client, "123456789012", "OPS", "12345", ["# Content"], _make_sidecar(),
                kms_key_arn=_TEST_KMS_KEY_ARN,
            )
        assert "12345" in caplog.text

    def test_orphans_deleted_logs_metric_token(self, caplog):
        existing = ["confluence/OPS/12345_chunk_0.md"]
        client = _make_s3_client(existing_keys=existing)
        with caplog.at_level("INFO", logger="ckn_ingestion.s3_uploader"):
            upload_page(
                client, "123456789012", "OPS", "12345", ["# Content"], _make_sidecar(),
                kms_key_arn=_TEST_KMS_KEY_ARN,
            )
        assert "ORPHANS_DELETED" in caplog.text

    def test_pagination_and_batching_over_1000_orphans(self):
        # 2500 stale orphan chunk objects spread across 3 list pages must all be
        # deleted, in delete_objects batches of <=1000.
        orphans = [f"confluence/OPS/12345_chunk_{i}.md" for i in range(2500)]
        client = MagicMock()
        client.put_object.return_value = {}
        client.delete_objects.return_value = {}
        # Paginator yields 3 pages (1000/1000/500).
        pages = [
            {"Contents": [{"Key": k} for k in orphans[0:1000]]},
            {"Contents": [{"Key": k} for k in orphans[1000:2000]]},
            {"Contents": [{"Key": k} for k in orphans[2000:2500]]},
        ]
        paginator = MagicMock()
        paginator.paginate.return_value = pages
        client.get_paginator.return_value = paginator

        upload_page(
            client, "123456789012", "OPS", "12345", ["# Single now"], _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        # Every delete batch must be <= 1000 keys, and all 2500 orphans deleted.
        batch_sizes = [
            len(c.kwargs["Delete"]["Objects"]) for c in client.delete_objects.call_args_list
        ]
        assert all(n <= 1000 for n in batch_sizes)
        assert sum(batch_sizes) == 2500

    def test_list_uses_page_prefix(self):
        # Cross-page safety also relies on scoping the list to the page prefix.
        client = _make_s3_client(existing_keys=[])
        upload_page(
            client, "123456789012", "OPS", "12345", ["# Content"], _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        paginate_call = client.get_paginator.return_value.paginate.call_args
        assert paginate_call.kwargs["Prefix"] == "confluence/OPS/12345"

    def test_partial_delete_errors_are_logged_and_not_counted(self, caplog):
        import logging

        existing = [
            "confluence/OPS/12345_chunk_0.md",
            "confluence/OPS/12345_chunk_1.md",
        ]
        client = _make_s3_client(existing_keys=existing)
        # S3 reports one key failed to delete (Quiet mode returns only Errors).
        client.delete_objects.return_value = {
            "Errors": [{"Key": "confluence/OPS/12345_chunk_1.md", "Code": "AccessDenied"}]
        }
        with caplog.at_level(logging.INFO, logger="ckn_ingestion.s3_uploader"):
            upload_page(
                client, "123456789012", "OPS", "12345", ["# Content"], _make_sidecar(),
                kms_key_arn=_TEST_KMS_KEY_ARN,
            )
        # The failed key is logged, and the count reflects only the 1 success.
        assert "AccessDenied" in caplog.text
        assert "ORPHANS_DELETED page_id=12345 count=1" in caplog.text

    def test_content_failure_logs_error_type(self, caplog):
        client = _make_s3_client()
        client.put_object.side_effect = RuntimeError("boom")
        with caplog.at_level("ERROR", logger="ckn_ingestion.s3_uploader"):
            upload_page(
                client,
                "123456789012",
                "OPS",
                "12345",
                ["content"],
                _make_sidecar(),
                kms_key_arn=_TEST_KMS_KEY_ARN,
            )
        assert "RuntimeError" in caplog.text

    def test_content_not_logged(self, caplog):
        """Page content must never appear in logs."""
        client = _make_s3_client()
        client.put_object.side_effect = RuntimeError("boom")
        secret_content = "SECRET_CONTENT_NEVER_LOG_THIS_XYZ"
        with caplog.at_level("ERROR", logger="ckn_ingestion.s3_uploader"):
            upload_page(
                client,
                "123456789012",
                "OPS",
                "12345",
                [secret_content],
                _make_sidecar(),
                kms_key_arn=_TEST_KMS_KEY_ARN,
            )
        assert secret_content not in caplog.text

    def test_kms_key_arn_not_logged_on_failure(self, caplog):
        """KMS key ARN must never appear in error logs."""
        client = _make_s3_client()
        client.put_object.side_effect = RuntimeError("encryption error")
        kms_arn = "arn:aws:kms:us-east-1:999999999999:key/secret-key-id"
        with caplog.at_level("ERROR", logger="ckn_ingestion.s3_uploader"):
            upload_page(
                client,
                "123456789012",
                "OPS",
                "12345",
                ["content"],
                _make_sidecar(),
                kms_key_arn=kms_arn,
            )
        assert kms_arn not in caplog.text


# ---------------------------------------------------------------------------
# Multi-chunk upload
# ---------------------------------------------------------------------------


class TestMultiChunkUpload:
    """Tests for uploading pages that produce multiple chunks.

    Validates: Requirements 1.4, 1.5, 1.6, 3.4, 3.5, 3.6
    """

    def test_multi_chunk_produces_correct_key_pattern(self):
        """Content keys follow {page_id}_chunk_{i}.md for multi-chunk uploads."""
        client = _make_s3_client()
        chunks = ["chunk zero", "chunk one", "chunk two"]
        upload_page(
            client,
            "123456789012",
            "OPS",
            "pg42",
            chunks,
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        content_keys = [
            c.kwargs["Key"]
            for c in client.put_object.call_args_list
            if c.kwargs["ContentType"] == "text/markdown"
        ]
        assert content_keys == [
            "confluence/OPS/pg42_chunk_0.md",
            "confluence/OPS/pg42_chunk_1.md",
            "confluence/OPS/pg42_chunk_2.md",
        ]

    def test_multi_chunk_sidecar_key_pattern(self):
        """Sidecar keys follow {page_id}_chunk_{i}.md.metadata.json."""
        client = _make_s3_client()
        chunks = ["a", "b"]
        upload_page(
            client,
            "123456789012",
            "OPS",
            "pg42",
            chunks,
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        sidecar_keys = [
            c.kwargs["Key"]
            for c in client.put_object.call_args_list
            if c.kwargs["ContentType"] == "application/json"
        ]
        assert sidecar_keys == [
            "confluence/OPS/pg42_chunk_0.md.metadata.json",
            "confluence/OPS/pg42_chunk_1.md.metadata.json",
        ]

    def test_multi_chunk_call_count(self):
        """put_object is called exactly 2 * len(chunks) times (content + sidecar each)."""
        client = _make_s3_client()
        chunks = ["c0", "c1", "c2", "c3"]
        upload_page(
            client,
            "123456789012",
            "OPS",
            "pg42",
            chunks,
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        assert client.put_object.call_count == 2 * len(chunks)

    def test_multi_chunk_each_chunk_content_uploaded(self):
        """Each chunk's text content appears in the Body of a put_object call."""
        client = _make_s3_client()
        chunks = ["alpha content", "beta content"]
        upload_page(
            client,
            "123456789012",
            "OPS",
            "pg42",
            chunks,
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        uploaded_bodies = [
            c.kwargs["Body"]
            for c in client.put_object.call_args_list
            if c.kwargs["ContentType"] == "text/markdown"
        ]
        assert uploaded_bodies == [b"alpha content", b"beta content"]

    def test_multi_chunk_each_sidecar_identical(self):
        """Every sidecar upload contains the same metadata JSON."""
        client = _make_s3_client()
        sidecar = _make_sidecar(doc_type="runbook", service="lambda")
        chunks = ["x", "y", "z"]
        upload_page(
            client, "123456789012", "OPS", "pg42", chunks, sidecar, kms_key_arn=_TEST_KMS_KEY_ARN
        )
        sidecar_bodies = [
            c.kwargs["Body"]
            for c in client.put_object.call_args_list
            if c.kwargs["ContentType"] == "application/json"
        ]
        # All three sidecars must be byte-identical
        assert len(sidecar_bodies) == 3
        assert sidecar_bodies[0] == sidecar_bodies[1] == sidecar_bodies[2]
        # Verify content matches the input sidecar
        parsed = json.loads(sidecar_bodies[0].decode("utf-8"))
        assert parsed["metadataAttributes"]["doc_type"] == "runbook"
        assert parsed["metadataAttributes"]["service"] == "lambda"


# ---------------------------------------------------------------------------
# Personal-space key (leading ~) — F11 regression test
# ---------------------------------------------------------------------------


class TestPersonalSpaceKey:
    """Validate that personal-space keys (leading ~) are accepted.

    Confluence personal-space keys always start with ~ followed by a hex user ID
    (e.g., ~5b58bdf9e288ee2d9b4ba4fe). Previously this was silently rejected by
    the sanitizer regex, causing zero pages to upload from personal spaces.
    """

    def test_tilde_prefixed_space_key_accepted(self):
        """A personal-space key starting with ~ produces valid S3 keys."""
        client = _make_s3_client()
        upload_page(
            client,
            "123456789012",
            "~5b58bdf9e288ee2d9b4ba4fe",
            "163934",
            ["# Personal space content"],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        content_call = client.put_object.call_args_list[0]
        assert content_call.kwargs["Key"] == "confluence/~5b58bdf9e288ee2d9b4ba4fe/163934.md"

    def test_tilde_only_in_first_position(self):
        """A ~ in the middle of a value is still rejected (only leading ~ is valid)."""
        import pytest
        from ckn_ingestion.s3_uploader import _sanitize_key_component

        # Tilde in leading position — OK
        assert _sanitize_key_component("~abc123", "space_key") == "~abc123"

        # Tilde after first char — rejected (not in [A-Za-z0-9._-])
        with pytest.raises(ValueError):
            _sanitize_key_component("abc~123", "space_key")

    def test_pure_tilde_rejected(self):
        """A bare ~ with no trailing chars is rejected."""
        import pytest
        from ckn_ingestion.s3_uploader import _sanitize_key_component

        with pytest.raises(ValueError, match="bare tilde"):
            _sanitize_key_component("~", "space_key")


# ---------------------------------------------------------------------------
# Empty chunks list
# ---------------------------------------------------------------------------


class TestEmptyChunks:
    """Tests for empty chunks list — defensive guard.

    Validates: Requirements 1.4, 1.5, 1.6
    """

    def test_empty_chunks_skips_upload(self):
        """put_object must not be called when chunks list is empty."""
        client = _make_s3_client()
        upload_page(
            client,
            "123456789012",
            "OPS",
            "pg42",
            [],
            _make_sidecar(),
            kms_key_arn=_TEST_KMS_KEY_ARN,
        )
        client.put_object.assert_not_called()

    def test_empty_chunks_logs_warning(self, caplog):
        """A warning containing the page_id must be logged for empty chunks."""
        client = _make_s3_client()
        with caplog.at_level("WARNING", logger="ckn_ingestion.s3_uploader"):
            upload_page(
                client,
                "123456789012",
                "OPS",
                "pg-empty",
                [],
                _make_sidecar(),
                kms_key_arn=_TEST_KMS_KEY_ARN,
            )
        assert "pg-empty" in caplog.text
