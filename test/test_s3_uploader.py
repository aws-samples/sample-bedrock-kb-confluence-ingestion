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


def _make_s3_client() -> MagicMock:
    client = MagicMock()
    client.put_object.return_value = {}
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
