# Feature: security-review-remediation, Property 1: S3 uploads always include KMS encryption parameters
"""Property-based tests for S3 upload KMS encryption.

Uses Hypothesis to verify that every put_object call made by upload_page()
includes both ServerSideEncryption="aws:kms" and a non-empty SSEKMSKeyId.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st

from ckn_ingestion.models import MetadataSidecar
from ckn_ingestion.s3_uploader import upload_page

# ---------------------------------------------------------------------------
# Custom Hypothesis strategies
# ---------------------------------------------------------------------------

# Safe characters for S3 key components (ASCII letters and digits only).
_s3_safe_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N"),
        whitelist_characters="",
        max_codepoint=127,
    ),
    min_size=1,
    max_size=30,
)

# KMS key ARNs — realistic format with random key IDs
_kms_key_arn = st.from_regex(
    r"arn:aws:kms:us-east-1:[0-9]{12}:key/[a-f0-9\-]{36}",
    fullmatch=True,
)

# Non-empty chunk text
_chunk_text = st.text(min_size=1, max_size=200)

# Metadata attributes — flat string dict
_metadata_attrs = st.dictionaries(
    keys=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "Pd")),
        min_size=1,
        max_size=20,
    ),
    values=st.text(max_size=50),
    min_size=1,
    max_size=5,
)


# ---------------------------------------------------------------------------
# Property 1: S3 uploads always include KMS encryption parameters
# ---------------------------------------------------------------------------
# **Validates: Requirements 1.2**


@given(
    space_key=_s3_safe_text,
    page_id=_s3_safe_text,
    chunks=st.lists(_chunk_text, min_size=1, max_size=5),
    kms_arn=_kms_key_arn,
    metadata_attributes=_metadata_attrs,
)
@settings(max_examples=100)
def test_property1_s3_uploads_always_include_kms_encryption_params(
    space_key: str,
    page_id: str,
    chunks: list[str],
    kms_arn: str,
    metadata_attributes: dict[str, str],
):
    """Property 1: S3 uploads always include KMS encryption parameters.

    For any page content or metadata sidecar uploaded via upload_page(),
    every put_object call to S3 SHALL include both
    ServerSideEncryption="aws:kms" and a non-empty SSEKMSKeyId parameter.

    **Validates: Requirements 1.2**
    """
    mock_client = MagicMock()
    account_id = "123456789012"
    sidecar = MetadataSidecar(metadata_attributes=metadata_attributes)

    upload_page(mock_client, account_id, space_key, page_id, chunks, sidecar, kms_key_arn=kms_arn)

    # Every put_object call must include both SSE parameters
    calls = mock_client.put_object.call_args_list
    assert len(calls) > 0, "Expected at least one put_object call for non-empty chunks"

    for i, call_obj in enumerate(calls):
        kwargs = call_obj.kwargs

        # ServerSideEncryption must be "aws:kms"
        assert "ServerSideEncryption" in kwargs, (
            f"put_object call {i} missing ServerSideEncryption parameter.\n"
            f"Call kwargs: {kwargs}"
        )
        assert kwargs["ServerSideEncryption"] == "aws:kms", (
            f"put_object call {i} has wrong ServerSideEncryption value.\n"
            f"Expected: 'aws:kms'\n"
            f"Actual: {kwargs['ServerSideEncryption']!r}"
        )

        # SSEKMSKeyId must be present and non-empty
        assert "SSEKMSKeyId" in kwargs, (
            f"put_object call {i} missing SSEKMSKeyId parameter.\n" f"Call kwargs: {kwargs}"
        )
        assert kwargs["SSEKMSKeyId"], (
            f"put_object call {i} has empty SSEKMSKeyId.\n"
            f"SSEKMSKeyId: {kwargs['SSEKMSKeyId']!r}"
        )


# ---------------------------------------------------------------------------
# Property 2: Encryption error logs never expose KMS key ARN
# ---------------------------------------------------------------------------
# **Validates: Requirements 1.4**

import logging  # noqa: E402
import logging.handlers  # noqa: E402


@given(
    page_id=_s3_safe_text,
    kms_arn=_kms_key_arn,
    metadata_attributes=_metadata_attrs,
)
@settings(max_examples=100)
def test_property2_encryption_error_logs_never_expose_kms_key_arn(
    page_id: str,
    kms_arn: str,
    metadata_attributes: dict[str, str],
):
    """Property 2: Encryption error logs never expose KMS key ARN.

    For any page ID and any KMS key ARN, when an S3 upload fails with an
    encryption-related error, the logged error message SHALL contain the
    page ID and the exception type but SHALL NOT contain the KMS key ARN
    string.

    **Validates: Requirements 1.4**
    """
    mock_client = MagicMock()
    mock_client.put_object.side_effect = Exception("KMS.NotFoundException: key not found")

    account_id = "123456789012"
    space_key = "TESTSPACE"
    chunks = ["some content"]
    sidecar = MetadataSidecar(metadata_attributes=metadata_attributes)

    handler = logging.handlers.MemoryHandler(capacity=1000)
    target_logger = logging.getLogger("ckn_ingestion.s3_uploader")
    target_logger.addHandler(handler)
    target_logger.setLevel(logging.ERROR)

    try:
        upload_page(
            mock_client,
            account_id,
            space_key,
            page_id,
            chunks,
            sidecar,
            kms_key_arn=kms_arn,
        )

        # There should be error log records (content upload + sidecar upload)
        records = handler.buffer
        assert len(records) > 0, "Expected error log records when S3 put_object raises"

        for record in records:
            msg = record.getMessage()
            # The KMS key ARN must never appear in any log message
            assert kms_arn not in msg, (
                f"KMS key ARN leaked in log message!\n" f"ARN: {kms_arn}\n" f"Log message: {msg}"
            )
            # The page ID should be present in the log message
            assert page_id in msg, (
                f"Page ID missing from error log message.\n"
                f"Page ID: {page_id}\n"
                f"Log message: {msg}"
            )
            # The exception type should be present in the log message
            assert "Exception" in msg, (
                f"Exception type missing from error log message.\n" f"Log message: {msg}"
            )
    finally:
        target_logger.removeHandler(handler)
        handler.close()
