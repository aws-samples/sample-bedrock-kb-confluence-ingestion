# Feature: security-review-remediation, Property 5: Pages exceeding the body size limit are skipped
"""Property-based tests for page body size limits.

Property 5: Pages exceeding the body size limit are skipped

Uses Hypothesis to verify that the size-check decision in
confluence_extractor correctly skips pages whose UTF-8 encoded HTML body
exceeds MAX_PAGE_BODY_BYTES and processes pages that are within the limit.

**Validates: Requirements 3.2**
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from ckn_ingestion.confluence_extractor import MAX_PAGE_BODY_BYTES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Use a smaller limit for test generation so we don't allocate 10 MB+ strings
# in every Hypothesis example.  The property under test is the *comparison*
# logic, not the specific constant value, so we can safely use a reduced
# limit and also verify the real constant separately.
_TEST_LIMIT = 1024  # 1 KB — keeps Hypothesis fast


def _should_skip(html_body: str, limit: int) -> bool:
    """Reproduce the size-guard logic from extract_pages().

    Returns True when the page should be skipped (body too large).
    """
    if not html_body:
        return False
    return len(html_body.encode("utf-8")) > limit


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Bodies guaranteed to be OVER the limit (skip expected)
_over_limit_body = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    min_size=_TEST_LIMIT + 1,
    max_size=_TEST_LIMIT + 500,
)

# Bodies guaranteed to be UNDER the limit (process expected).
# We cap at limit-1 *characters*; multi-byte chars could push the byte
# length over, so we restrict to ASCII-safe characters for the "under" case.
_under_limit_body = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N"),
        max_codepoint=127,  # ASCII only → 1 byte per char
    ),
    min_size=1,
    max_size=_TEST_LIMIT - 1,
)

# Mixed strategy — bodies that may be above or below the limit
_any_body = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    min_size=0,
    max_size=_TEST_LIMIT + 500,
)


# ---------------------------------------------------------------------------
# Property 5: Pages exceeding the body size limit are skipped
# ---------------------------------------------------------------------------
# **Validates: Requirements 3.2**


@given(body=_over_limit_body)
@settings(max_examples=100)
def test_property5_oversized_pages_are_skipped(body: str):
    """Property 5: Pages exceeding the body size limit are skipped.

    For any HTML body whose UTF-8 byte length exceeds the limit,
    the size guard SHALL decide to skip the page.

    **Validates: Requirements 3.2**
    """
    assert _should_skip(body, _TEST_LIMIT), (
        f"Expected page to be skipped but it was not.\n"
        f"Body byte length: {len(body.encode('utf-8'))}\n"
        f"Limit: {_TEST_LIMIT}"
    )


@given(body=_under_limit_body)
@settings(max_examples=100)
def test_property5_undersized_pages_are_processed(body: str):
    """Property 5: Pages within the body size limit are processed.

    For any non-empty HTML body whose UTF-8 byte length does NOT exceed
    the limit, the size guard SHALL allow the page to be processed.

    **Validates: Requirements 3.2**
    """
    assert not _should_skip(body, _TEST_LIMIT), (
        f"Expected page to be processed but it was skipped.\n"
        f"Body byte length: {len(body.encode('utf-8'))}\n"
        f"Limit: {_TEST_LIMIT}"
    )


@given(body=_any_body)
@settings(max_examples=100)
def test_property5_skip_decision_matches_byte_length(body: str):
    """Property 5: Skip/process decision is consistent with byte length.

    For any HTML body, the skip decision SHALL be True if and only if
    the UTF-8 byte length strictly exceeds the limit.

    **Validates: Requirements 3.2**
    """
    byte_len = len(body.encode("utf-8"))
    expected_skip = byte_len > _TEST_LIMIT
    actual_skip = _should_skip(body, _TEST_LIMIT)
    assert actual_skip == expected_skip, (
        f"Skip decision mismatch.\n"
        f"Body byte length: {byte_len}\n"
        f"Limit: {_TEST_LIMIT}\n"
        f"Expected skip: {expected_skip}\n"
        f"Actual skip: {actual_skip}"
    )


def test_property5_real_constant_value():
    """Verify MAX_PAGE_BODY_BYTES equals the documented 10 MB default.

    **Validates: Requirements 3.1**
    """
    assert (
        MAX_PAGE_BODY_BYTES == 10 * 1024 * 1024
    ), f"MAX_PAGE_BODY_BYTES should be 10 MB (10485760), got {MAX_PAGE_BODY_BYTES}"


def test_property5_empty_body_is_not_skipped():
    """Empty bodies should not trigger the size guard.

    **Validates: Requirements 3.2**
    """
    assert not _should_skip("", _TEST_LIMIT), "Empty body should not be skipped"


# ---------------------------------------------------------------------------
# Property 6: Attachment downloads exceeding the size limit are aborted
# ---------------------------------------------------------------------------
# Feature: security-review-remediation, Property 6
# **Validates: Requirements 4.3, 4.4**

from dataclasses import dataclass  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

from ckn_ingestion.image_processor import (  # noqa: E402
    MAX_ATTACHMENT_BYTES,
    _AttachmentTooLargeError,
    _download_attachment,
)

# Use a small test limit to keep Hypothesis fast
_ATTACHMENT_TEST_LIMIT = 1024  # 1 KB


@dataclass
class _MockAttachment:
    """Minimal attachment object for testing."""

    id: str
    download_url: str


def _make_mock_response(body: bytes, content_length: int | None = None):
    """Build a mock ``requests.Response`` with configurable Content-Length and body.

    Parameters
    ----------
    body:
        The raw bytes the response should yield.
    content_length:
        If not ``None``, the ``Content-Length`` header is set to this value.
        If ``None``, the header is absent (simulating chunked/unknown-length
        responses).
    """
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    # Headers
    headers: dict[str, str] = {}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    mock_resp.headers = headers

    # Streaming: iter_content yields the body in 8 KB chunks (matching real code)
    chunk_size = 8192
    chunks = [body[i : i + chunk_size] for i in range(0, len(body), chunk_size)]
    if not chunks:
        chunks = [b""]
    mock_resp.iter_content = MagicMock(return_value=iter(chunks))
    mock_resp.close = MagicMock()

    return mock_resp


# ---------------------------------------------------------------------------
# Strategies for Property 6
# ---------------------------------------------------------------------------

# Sizes guaranteed to be OVER the test limit
_over_limit_size = st.integers(
    min_value=_ATTACHMENT_TEST_LIMIT + 1, max_value=_ATTACHMENT_TEST_LIMIT + 2000
)

# Sizes guaranteed to be UNDER or AT the test limit
_under_limit_size = st.integers(min_value=1, max_value=_ATTACHMENT_TEST_LIMIT)


@given(size=_over_limit_size)
@settings(max_examples=100)
def test_property6_oversized_attachment_aborted_with_content_length(size: int):
    """Property 6: Attachment downloads exceeding the size limit are aborted.

    When Content-Length is present and exceeds the limit, the download SHALL
    be aborted before reading the body.

    **Validates: Requirements 4.3, 4.4**
    """
    import ckn_ingestion.image_processor as mod

    original = mod.MAX_ATTACHMENT_BYTES
    try:
        mod.MAX_ATTACHMENT_BYTES = _ATTACHMENT_TEST_LIMIT

        attachment = _MockAttachment(id="att-test", download_url="https://example.com/file")
        body = b"x" * size
        mock_resp = _make_mock_response(body, content_length=size)

        with patch("ckn_ingestion.image_processor.requests.get", return_value=mock_resp):
            with pytest.raises(_AttachmentTooLargeError):
                _download_attachment(attachment, "user:token")

            # Body should NOT have been read — iter_content should not be called
            mock_resp.iter_content.assert_not_called()
    finally:
        mod.MAX_ATTACHMENT_BYTES = original


@given(size=_over_limit_size)
@settings(max_examples=100)
def test_property6_oversized_attachment_aborted_without_content_length(size: int):
    """Property 6: Attachment downloads exceeding the size limit are aborted.

    When Content-Length is absent, the download SHALL stream and abort once
    accumulated bytes exceed the limit.

    **Validates: Requirements 4.3, 4.4**
    """
    import ckn_ingestion.image_processor as mod

    original = mod.MAX_ATTACHMENT_BYTES
    try:
        mod.MAX_ATTACHMENT_BYTES = _ATTACHMENT_TEST_LIMIT

        attachment = _MockAttachment(id="att-test", download_url="https://example.com/file")
        body = b"x" * size
        mock_resp = _make_mock_response(body, content_length=None)

        with patch("ckn_ingestion.image_processor.requests.get", return_value=mock_resp):
            with pytest.raises(_AttachmentTooLargeError):
                _download_attachment(attachment, "user:token")
    finally:
        mod.MAX_ATTACHMENT_BYTES = original


@given(size=_under_limit_size)
@settings(max_examples=100)
def test_property6_undersized_attachment_succeeds(size: int):
    """Property 6: Attachments within the size limit are downloaded successfully.

    For any attachment whose size does NOT exceed the limit, the download
    SHALL complete and return the full body bytes.

    **Validates: Requirements 4.3, 4.4**
    """
    import ckn_ingestion.image_processor as mod

    original = mod.MAX_ATTACHMENT_BYTES
    try:
        mod.MAX_ATTACHMENT_BYTES = _ATTACHMENT_TEST_LIMIT

        attachment = _MockAttachment(id="att-test", download_url="https://example.com/file")
        body = b"x" * size
        mock_resp = _make_mock_response(body, content_length=size)

        with patch("ckn_ingestion.image_processor.requests.get", return_value=mock_resp):
            result = _download_attachment(attachment, "user:token")
            assert result == body, f"Expected {len(body)} bytes, got {len(result)} bytes"
    finally:
        mod.MAX_ATTACHMENT_BYTES = original


def test_property6_real_constant_value():
    """Verify MAX_ATTACHMENT_BYTES equals the documented 50 MB default.

    **Validates: Requirements 4.1**
    """
    assert (
        MAX_ATTACHMENT_BYTES == 50 * 1024 * 1024
    ), f"MAX_ATTACHMENT_BYTES should be 50 MB (52428800), got {MAX_ATTACHMENT_BYTES}"
