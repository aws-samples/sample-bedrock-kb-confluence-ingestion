# Feature: attachment-download-fix, Property 1: Bug Condition
"""Property-based tests for attachment download pipeline bug conditions.

Property 1: Bug Condition — Attachment Download Pipeline Failures
(Empty mediaType, Legacy URL, Archived Status)

These tests encode the EXPECTED (correct) behavior. They are designed to
FAIL on unfixed code, confirming the bugs exist. After the fix is applied,
these tests will PASS, validating the fix.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5**
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

from ckn_ingestion.confluence_extractor import _list_attachments

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Random image extensions that should be processable
_IMAGE_EXTENSIONS = st.sampled_from([".png", ".jpeg", ".jpg", ".gif", ".svg"])

# Random base filenames (alphanumeric, reasonable length)
_BASE_FILENAMES = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), max_codepoint=127),
    min_size=3,
    max_size=30,
)

# Random image filenames: base + image extension
_IMAGE_FILENAMES = st.builds(lambda base, ext: f"{base}{ext}", _BASE_FILENAMES, _IMAGE_EXTENSIONS)

# Random page IDs (numeric strings like Confluence uses)
_PAGE_IDS = st.integers(min_value=1000, max_value=9999999999).map(str)

# Random attachment IDs (Confluence format: "att" + digits)
_ATTACHMENT_IDS = st.integers(min_value=100000, max_value=9999999999).map(lambda n: f"att{n}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api_response_bug_condition(
    att_id: str,
    filename: str,
    page_id: str,
    status: str = "current",
) -> dict:
    """Build a Confluence API attachment response that triggers the bug condition.

    This response:
    - Has NO `expand=extensions` (so mediaType is empty at top level)
    - Has `_links.download` with legacy path format
    - Has configurable status field
    """
    return {
        "results": [
            {
                "id": att_id,
                "title": filename,
                "mediaType": "",  # Bug 1: empty because expand=extensions not requested
                "status": status,
                "extensions": {"fileSize": 1024},
                "_links": {
                    "download": f"/download/attachments/{page_id}/{filename}",  # Bug 2: legacy path
                },
            }
        ],
        "_links": {},  # No pagination
    }


def _mock_get_with_retry(api_response: dict):
    """Return a side_effect function that returns the given API response."""

    def _side_effect(fn, **kwargs):
        return api_response

    return _side_effect


# ---------------------------------------------------------------------------
# Property 1 - Bug 2: Legacy URL (blocking bug)
# ---------------------------------------------------------------------------
# **Validates: Requirements 1.3, 1.4**


@given(
    filename=_IMAGE_FILENAMES,
    page_id=_PAGE_IDS,
    att_id=_ATTACHMENT_IDS,
)
@settings(max_examples=50)
def test_bug2_download_url_uses_rest_api_format(filename: str, page_id: str, att_id: str):
    """Bug 2: download_url MUST use REST API format, not legacy /download/attachments/ path.

    For any image attachment, the download_url SHALL use the REST API endpoint
    format: /wiki/rest/api/content/{pageId}/child/attachment/{attachmentId}/download

    This test will FAIL on unfixed code because the current implementation
    constructs URLs from _links.download which uses the legacy path.

    **Validates: Requirements 1.3, 1.4**
    """
    base_url = "https://example.atlassian.net"
    api_response = _make_api_response_bug_condition(att_id, filename, page_id)

    session = MagicMock()

    with patch(
        "ckn_ingestion.confluence_extractor._get_with_retry",
        return_value=api_response,
    ):
        attachments = _list_attachments(session, base_url, page_id)

    assert len(attachments) >= 1, "Expected at least one attachment returned"
    attachment = attachments[0]

    # Assert REST API URL format (expected behavior after fix)
    expected_url_fragment = f"/wiki/rest/api/content/{page_id}/child/attachment/{att_id}/download"
    assert expected_url_fragment in attachment.download_url, (
        f"download_url should use REST API format.\n"
        f"Expected URL to contain: {expected_url_fragment}\n"
        f"Actual download_url: {attachment.download_url}\n"
        f"Bug: URL uses legacy /download/attachments/ path instead of REST API endpoint"
    )


# ---------------------------------------------------------------------------
# Property 1 - Bug 1: Empty mediaType
# ---------------------------------------------------------------------------
# **Validates: Requirements 1.1, 1.2**


@given(
    filename=_IMAGE_FILENAMES,
    page_id=_PAGE_IDS,
    att_id=_ATTACHMENT_IDS,
)
@settings(max_examples=50)
def test_bug1_media_type_is_non_empty_for_image_files(filename: str, page_id: str, att_id: str):
    """Bug 1: media_type MUST be non-empty for image file attachments.

    For any attachment with an image file extension (.png, .jpeg, .jpg, .gif, .svg),
    the Attachment.media_type SHALL be a non-empty string containing the correct
    MIME type.

    This test will FAIL on unfixed code because the current implementation reads
    mediaType from the top-level API response (which is empty without expand=extensions).

    **Validates: Requirements 1.1, 1.2**
    """
    base_url = "https://example.atlassian.net"
    api_response = _make_api_response_bug_condition(att_id, filename, page_id)

    session = MagicMock()

    with patch(
        "ckn_ingestion.confluence_extractor._get_with_retry",
        return_value=api_response,
    ):
        attachments = _list_attachments(session, base_url, page_id)

    assert len(attachments) >= 1, "Expected at least one attachment returned"
    attachment = attachments[0]

    # Assert media_type is non-empty (expected behavior after fix)
    assert attachment.media_type != "", (
        f"media_type should be non-empty for image file '{filename}'.\n"
        f"Actual media_type: '{attachment.media_type}'\n"
        f"Bug: mediaType is empty because expand=extensions is not requested"
    )


# ---------------------------------------------------------------------------
# Property 1 - Bug 3: Archived attachments not filtered
# ---------------------------------------------------------------------------
# **Validates: Requirements 1.5**


@given(
    filename=_IMAGE_FILENAMES,
    page_id=_PAGE_IDS,
    att_id=_ATTACHMENT_IDS,
)
@settings(max_examples=50)
def test_bug3_archived_attachments_excluded_from_list(filename: str, page_id: str, att_id: str):
    """Bug 3: Archived attachments MUST be excluded from the returned list.

    For any attachment with status "archived", the _list_attachments() function
    SHALL NOT include it in the returned attachment list.

    This test will FAIL on unfixed code because the current implementation
    does not filter by status field.

    **Validates: Requirements 1.5**
    """
    base_url = "https://example.atlassian.net"
    # Create response with archived status
    api_response = _make_api_response_bug_condition(att_id, filename, page_id, status="archived")

    session = MagicMock()

    with patch(
        "ckn_ingestion.confluence_extractor._get_with_retry",
        return_value=api_response,
    ):
        attachments = _list_attachments(session, base_url, page_id)

    # Assert archived attachments are excluded (expected behavior after fix)
    assert len(attachments) == 0, (
        f"Archived attachment should be excluded from list.\n"
        f"Attachment '{filename}' with status='archived' was included.\n"
        f"Number of attachments returned: {len(attachments)}\n"
        f"Bug: No status filtering exists in _list_attachments()"
    )
