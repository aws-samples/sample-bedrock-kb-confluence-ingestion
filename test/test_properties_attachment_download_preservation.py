# Feature: attachment-download-fix, Property 2: Preservation
"""Property-based tests for preservation of non-bug-condition behavior.

Property 2: Preservation — Non-Bug-Condition Behavior Unchanged
(Drawio Detection, Size Limits, Non-Image Skipping, Pagination, Error Placeholders)

These tests capture baseline behavior on UNFIXED code. They MUST PASS on the
current code and continue to pass after the fix is applied, confirming no
regressions in existing functionality.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from ckn_ingestion.confluence_extractor import _list_attachments
from ckn_ingestion.image_processor import (
    MAX_ATTACHMENT_BYTES,
    _AttachmentTooLargeError,
    _download_attachment,
    process_page_images,
)
from ckn_ingestion.models import Attachment, PageContent

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Random base filenames (alphanumeric, reasonable length)
_BASE_FILENAMES = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), max_codepoint=127),
    min_size=3,
    max_size=30,
)

# Random .drawio filenames
_DRAWIO_FILENAMES = st.builds(lambda base: f"{base}.drawio", _BASE_FILENAMES)

# Arbitrary media_type values (including empty, random strings, valid types)
_ARBITRARY_MEDIA_TYPES = st.one_of(
    st.just(""),
    st.just("application/octet-stream"),
    st.just("application/pdf"),
    st.just("image/png"),
    st.just("text/plain"),
    st.text(min_size=0, max_size=50),
)

# Non-image extensions (not .png, .jpeg, .jpg, .gif, .svg, .drawio)
_NON_IMAGE_EXTENSIONS = st.sampled_from(
    [
        ".pdf",
        ".zip",
        ".docx",
        ".xlsx",
        ".txt",
        ".csv",
        ".json",
        ".xml",
        ".pptx",
        ".mp4",
        ".avi",
        ".html",
        ".css",
        ".js",
        ".py",
    ]
)

# Non-image filenames
_NON_IMAGE_FILENAMES = st.builds(
    lambda base, ext: f"{base}{ext}", _BASE_FILENAMES, _NON_IMAGE_EXTENSIONS
)

# Non-image media types (not in PROCESSABLE_MEDIA_TYPES)
_NON_IMAGE_MEDIA_TYPES = st.sampled_from(
    [
        "application/pdf",
        "application/zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/csv",
        "text/html",
        "application/json",
        "video/mp4",
        "audio/mpeg",
    ]
)

# Random page IDs
_PAGE_IDS = st.integers(min_value=1000, max_value=9999999999).map(str)

# Random attachment IDs
_ATTACHMENT_IDS = st.integers(min_value=100000, max_value=9999999999).map(lambda n: f"att{n}")

# File sizes above the limit
_SIZES_ABOVE_LIMIT = st.integers(
    min_value=MAX_ATTACHMENT_BYTES + 1,
    max_value=MAX_ATTACHMENT_BYTES * 3,
)

# File sizes below the limit
_SIZES_BELOW_LIMIT = st.integers(min_value=1, max_value=MAX_ATTACHMENT_BYTES - 1)

# Number of pagination pages (1 to 5)
_PAGE_COUNTS = st.integers(min_value=1, max_value=5)


# ---------------------------------------------------------------------------
# Property: Drawio Detection Preservation (Requirement 3.1)
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="Draw.io detection/processing was removed due to dependency (drawio/cairosvg) license constraints."
)
def test_drawio_files_always_pass_processable_filter():
    """Draw.io processing was removed; this preservation property no longer applies."""
    pass


# ---------------------------------------------------------------------------
# Property: Non-Image Skipping Preservation (Requirement 3.3)
# ---------------------------------------------------------------------------


@given(
    filename=_NON_IMAGE_FILENAMES,
    media_type=_NON_IMAGE_MEDIA_TYPES,
    page_id=_PAGE_IDS,
    att_id=_ATTACHMENT_IDS,
)
@settings(max_examples=50)
def test_non_image_attachments_always_skipped(
    filename: str, media_type: str, page_id: str, att_id: str
):
    """Non-image attachments are always skipped without download attempts.

    For any attachment with a non-image extension and non-image media type,
    process_page_images() SHALL skip it without attempting download.

    **Validates: Requirements 3.3**
    """
    # Ensure the filename doesn't accidentally end with .drawio
    assume(not filename.lower().endswith(".drawio"))

    attachment = Attachment(
        id=att_id,
        filename=filename,
        media_type=media_type,
        file_size=1024,
        download_url=f"https://example.com/download/{att_id}",
    )
    page = PageContent(
        page_id=page_id,
        title="Test Page",
        space_key="TEST",
        author="test",
        last_modified="2024-01-01",
        url="https://example.com",
        markdown="# Test",
        attachments=[attachment],
    )

    with patch(
        "ckn_ingestion.image_processor._download_attachment",
    ) as mock_download:
        result = process_page_images(page, "user:token", MagicMock())

    # Download should NOT be called for non-image attachments
    mock_download.assert_not_called()
    # Markdown should be unchanged (no injections)
    assert result.strip() == "# Test"


# ---------------------------------------------------------------------------
# Property: Size Limit Enforcement Preservation (Requirement 3.2)
# ---------------------------------------------------------------------------


@given(
    size_above=_SIZES_ABOVE_LIMIT,
    att_id=_ATTACHMENT_IDS,
)
@settings(max_examples=50)
def test_attachments_above_size_limit_raise_error(size_above: int, att_id: str):
    """Attachments exceeding MAX_ATTACHMENT_BYTES raise _AttachmentTooLargeError.

    For any attachment where Content-Length exceeds 50 MB, _download_attachment()
    SHALL raise _AttachmentTooLargeError without reading the body.

    **Validates: Requirements 3.2**
    """
    attachment = Attachment(
        id=att_id,
        filename="large_image.png",
        media_type="image/png",
        file_size=size_above,
        download_url="https://example.com/download/large",
    )

    mock_response = MagicMock()
    mock_response.headers = {"Content-Length": str(size_above)}
    mock_response.raise_for_status = MagicMock()

    with patch("ckn_ingestion.image_processor.requests.get", return_value=mock_response):
        try:
            _download_attachment(attachment, "user:token")
            assert False, "Expected _AttachmentTooLargeError to be raised"
        except _AttachmentTooLargeError:
            pass  # Expected behavior


@given(
    size_below=_SIZES_BELOW_LIMIT,
    att_id=_ATTACHMENT_IDS,
)
@settings(max_examples=50)
def test_attachments_below_size_limit_download_successfully(size_below: int, att_id: str):
    """Attachments below MAX_ATTACHMENT_BYTES download successfully.

    For any attachment where Content-Length is below 50 MB, _download_attachment()
    SHALL return the content bytes without raising an error.

    **Validates: Requirements 3.2**
    """
    attachment = Attachment(
        id=att_id,
        filename="small_image.png",
        media_type="image/png",
        file_size=size_below,
        download_url="https://example.com/download/small",
    )

    # Create a small chunk of data
    content = b"x" * min(size_below, 1024)

    mock_response = MagicMock()
    mock_response.headers = {"Content-Length": str(size_below)}
    mock_response.raise_for_status = MagicMock()
    mock_response.iter_content = MagicMock(return_value=iter([content]))

    with patch("ckn_ingestion.image_processor.requests.get", return_value=mock_response):
        result = _download_attachment(attachment, "user:token")

    assert result == content


# ---------------------------------------------------------------------------
# Property: Pagination Preservation (Requirement 3.4)
# ---------------------------------------------------------------------------


@given(
    page_count=_PAGE_COUNTS,
    page_id=_PAGE_IDS,
)
@settings(max_examples=30)
def test_paginated_responses_all_pages_followed(page_count: int, page_id: str):
    """Paginated API responses are followed until all attachments are retrieved.

    For any number of pagination pages, _list_attachments() SHALL follow
    _links.next until it is absent, collecting all attachments.

    **Validates: Requirements 3.4**
    """
    base_url = "https://example.atlassian.net"

    # Build paginated responses
    responses = []
    for i in range(page_count):
        is_last = i == page_count - 1
        response = {
            "results": [
                {
                    "id": f"att{i}00",
                    "title": f"file_{i}.pdf",
                    "mediaType": "application/pdf",
                    "extensions": {"fileSize": 1024},
                    "_links": {
                        "download": f"/download/attachments/{page_id}/file_{i}.pdf",
                    },
                }
            ],
            "_links": (
                {}
                if is_last
                else {"next": f"/rest/api/content/{page_id}/child/attachment?start={25 * (i + 1)}"}
            ),
        }
        responses.append(response)

    call_count = [0]

    def mock_get_with_retry(session, url, params):
        idx = call_count[0]
        call_count[0] += 1
        if idx < len(responses):
            return responses[idx]
        return {"results": [], "_links": {}}

    session = MagicMock()

    with patch(
        "ckn_ingestion.confluence_extractor._get_with_retry",
        side_effect=mock_get_with_retry,
    ):
        attachments = _list_attachments(session, base_url, page_id)

    # All pages should have been followed
    assert len(attachments) == page_count
    assert call_count[0] == page_count


# ---------------------------------------------------------------------------
# Property: Error Placeholder Preservation (Requirement 3.5)
# ---------------------------------------------------------------------------


@given(
    filename=st.builds(
        lambda base: f"{base}.png",
        _BASE_FILENAMES,
    ),
    page_id=_PAGE_IDS,
    att_id=_ATTACHMENT_IDS,
)
@settings(max_examples=50)
def test_download_failures_insert_placeholder(filename: str, page_id: str, att_id: str):
    """Download failures insert failure placeholder block in markdown.

    For any processable attachment where download fails, process_page_images()
    SHALL insert a placeholder block with format:
    > **[FIGURA: {filename} — não processável]**

    **Validates: Requirements 3.5**
    """
    attachment = Attachment(
        id=att_id,
        filename=filename,
        media_type="image/png",
        file_size=1024,
        download_url=f"https://example.com/download/{att_id}",
    )
    page = PageContent(
        page_id=page_id,
        title="Test Page",
        space_key="TEST",
        author="test",
        last_modified="2024-01-01",
        url="https://example.com",
        markdown="# Test",
        attachments=[attachment],
    )

    # Mock download to raise an exception
    with patch(
        "ckn_ingestion.image_processor._download_attachment",
        side_effect=requests.exceptions.ConnectionError("Network error"),
    ):
        result = process_page_images(page, "user:token", MagicMock())

    # Verify placeholder format is preserved
    # Note: filename is sanitized (path separators removed) before use
    from ckn_ingestion.image_processor import _sanitize_filename

    safe_name = _sanitize_filename(filename)
    expected_placeholder = f"> **[FIGURA: {safe_name} — não processável]**"
    assert expected_placeholder in result, (
        f"Expected placeholder '{expected_placeholder}' in result.\n" f"Actual result: {result}"
    )


# ---------------------------------------------------------------------------
# Property: Attachment Dataclass Field Population (Requirement 3.6)
# ---------------------------------------------------------------------------


@given(
    att_id=_ATTACHMENT_IDS,
    filename=_BASE_FILENAMES,
    page_id=_PAGE_IDS,
)
@settings(max_examples=50)
def test_attachment_dataclass_populates_all_fields(att_id: str, filename: str, page_id: str):
    """Attachment dataclass populates id, filename, media_type, file_size, download_url.

    For any valid API response, _list_attachments() SHALL return Attachment objects
    with all required fields populated.

    **Validates: Requirements 3.6**
    """
    base_url = "https://example.atlassian.net"
    api_response = {
        "results": [
            {
                "id": att_id,
                "title": f"{filename}.pdf",
                "mediaType": "application/pdf",
                "extensions": {"fileSize": 2048},
                "_links": {
                    "download": f"/download/attachments/{page_id}/{filename}.pdf",
                },
            }
        ],
        "_links": {},
    }

    session = MagicMock()

    with patch(
        "ckn_ingestion.confluence_extractor._get_with_retry",
        return_value=api_response,
    ):
        attachments = _list_attachments(session, base_url, page_id)

    assert len(attachments) == 1
    attachment = attachments[0]

    # Verify all fields are populated
    assert attachment.id == att_id
    assert attachment.filename == f"{filename}.pdf"
    assert isinstance(attachment.media_type, str)
    assert isinstance(attachment.file_size, int)
    assert attachment.file_size == 2048
    assert isinstance(attachment.download_url, str)
    assert len(attachment.download_url) > 0
