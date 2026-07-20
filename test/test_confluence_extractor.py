"""Unit tests for confluence_extractor module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from ckn_ingestion.config import ConfluenceConfig
from ckn_ingestion.confluence_extractor import (
    _html_to_markdown,
    _list_attachments,
    extract_pages,
    get_confluence_token,
)
from ckn_ingestion.models import PageContent

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def confluence_config() -> ConfluenceConfig:
    return ConfluenceConfig(
        base_url="https://example.atlassian.net",
        kms_key_arn="arn:aws:kms:us-east-1:123456789012:key/test",
        kms_secret_id="test/confluence/token",
        spaces=["ENG", "OPS"],
    )


def _make_page_result(
    page_id: str = "123",
    title: str = "Test Page",
    space_key: str = "ENG",
    html_body: str = "<p>Hello</p>",
    author: str = "Alice",
    last_modified: str = "2024-01-01T00:00:00Z",
    webui: str = "/wiki/spaces/ENG/pages/123",
) -> dict:
    return {
        "id": page_id,
        "title": title,
        "space": {"key": space_key},
        "version": {
            "by": {"displayName": author},
            "when": last_modified,
        },
        "_links": {"webui": webui},
        "body": {"export_view": {"value": html_body}},
    }


def _make_attachment_result(
    att_id: str = "att1",
    title: str = "diagram.png",
    media_type: str = "image/png",
    file_size: int = 1024,
    download: str = "/download/attachments/123/diagram.png",
) -> dict:
    return {
        "id": att_id,
        "title": title,
        "mediaType": media_type,
        "extensions": {"fileSize": file_size},
        "_links": {"download": download},
    }


# ---------------------------------------------------------------------------
# get_confluence_token
# ---------------------------------------------------------------------------


class TestGetConfluenceToken:
    def test_returns_secret_string_on_success(self):
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {"SecretString": "user@example.com:mytoken"}

        with patch("ckn_ingestion.confluence_extractor.boto3.client", return_value=mock_client):
            token = get_confluence_token("test/secret")

        assert token == "user@example.com:mytoken"

    def test_raises_on_failure(self):
        mock_client = MagicMock()
        mock_client.get_secret_value.side_effect = RuntimeError("access denied")

        with patch("ckn_ingestion.confluence_extractor.boto3.client", return_value=mock_client):
            with pytest.raises(RuntimeError):
                get_confluence_token("test/secret")

    def test_logs_only_error_type_not_message(self, caplog):
        """Ensure the secret value / message is never logged."""
        mock_client = MagicMock()
        mock_client.get_secret_value.side_effect = ValueError("SECRET_VALUE_SHOULD_NOT_APPEAR")

        with patch("ckn_ingestion.confluence_extractor.boto3.client", return_value=mock_client):
            with pytest.raises(ValueError):
                with caplog.at_level("ERROR"):
                    get_confluence_token("test/secret")

        # The secret message must not appear in any log record.
        assert "SECRET_VALUE_SHOULD_NOT_APPEAR" not in caplog.text
        # The error type should appear.
        assert "ValueError" in caplog.text


# ---------------------------------------------------------------------------
# _html_to_markdown
# ---------------------------------------------------------------------------


class TestHtmlToMarkdown:
    def test_converts_paragraph(self):
        result = _html_to_markdown("<p>Hello world</p>")
        assert "Hello world" in result

    def test_converts_heading(self):
        result = _html_to_markdown("<h1>Title</h1>")
        assert "Title" in result

    def test_empty_html_returns_empty_or_whitespace(self):
        result = _html_to_markdown("")
        assert result.strip() == ""


# ---------------------------------------------------------------------------
# _list_attachments
# ---------------------------------------------------------------------------


class TestListAttachments:
    def test_returns_attachments(self):
        session = MagicMock()
        att = _make_attachment_result()
        session.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": [att], "_links": {}},
        )
        session.get.return_value.raise_for_status = MagicMock()

        with patch(
            "ckn_ingestion.confluence_extractor.retry_with_backoff",
            side_effect=lambda fn, **kw: fn(),
        ):
            result = _list_attachments(session, "https://example.atlassian.net", "123")

        assert len(result) == 1
        assert result[0].id == "att1"
        assert result[0].filename == "diagram.png"
        assert result[0].media_type == "image/png"
        assert result[0].file_size == 1024
        assert (
            result[0].download_url
            == "https://example.atlassian.net/wiki/rest/api/content/123/child/attachment/att1/download"
        )

    def test_returns_empty_on_api_error(self):
        session = MagicMock()
        session.get.side_effect = requests.ConnectionError("network error")

        with patch(
            "ckn_ingestion.confluence_extractor.retry_with_backoff",
            side_effect=lambda fn, **kw: fn(),
        ):
            result = _list_attachments(session, "https://example.atlassian.net", "123")

        assert result == []

    def test_skips_malformed_attachment(self):
        session = MagicMock()
        # Missing 'title' key — causes KeyError in the fixed code
        bad_att = {"id": "x"}
        session.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": [bad_att], "_links": {}},
        )
        session.get.return_value.raise_for_status = MagicMock()

        with patch(
            "ckn_ingestion.confluence_extractor.retry_with_backoff",
            side_effect=lambda fn, **kw: fn(),
        ):
            result = _list_attachments(session, "https://example.atlassian.net", "123")

        assert result == []

    def test_reads_media_type_from_extensions_object(self):
        """mediaType is read from extensions.mediaType, not top-level."""
        session = MagicMock()
        att = {
            "id": "att1",
            "title": "image.png",
            "mediaType": "",  # top-level is empty
            "extensions": {"mediaType": "image/png", "fileSize": 2048},
            "_links": {"download": "/download/attachments/123/image.png"},
        }
        session.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": [att], "_links": {}},
        )
        session.get.return_value.raise_for_status = MagicMock()

        with patch(
            "ckn_ingestion.confluence_extractor.retry_with_backoff",
            side_effect=lambda fn, **kw: fn(),
        ):
            result = _list_attachments(session, "https://example.atlassian.net", "123")

        assert len(result) == 1
        assert result[0].media_type == "image/png"

    def test_media_type_fallback_to_mimetypes_guess(self):
        """When extensions.mediaType is empty, falls back to mimetypes.guess_type()."""
        session = MagicMock()
        att = {
            "id": "att1",
            "title": "screenshot.jpeg",
            "mediaType": "",
            "extensions": {"mediaType": "", "fileSize": 1024},
            "_links": {"download": "/download/attachments/123/screenshot.jpeg"},
        }
        session.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": [att], "_links": {}},
        )
        session.get.return_value.raise_for_status = MagicMock()

        with patch(
            "ckn_ingestion.confluence_extractor.retry_with_backoff",
            side_effect=lambda fn, **kw: fn(),
        ):
            result = _list_attachments(session, "https://example.atlassian.net", "123")

        assert len(result) == 1
        assert result[0].media_type == "image/jpeg"

    def test_media_type_fallback_for_octet_stream(self):
        """When extensions.mediaType is application/octet-stream, falls back to filename."""
        session = MagicMock()
        att = {
            "id": "att1",
            "title": "diagram.png",
            "mediaType": "",
            "extensions": {"mediaType": "application/octet-stream", "fileSize": 1024},
            "_links": {"download": "/download/attachments/123/diagram.png"},
        }
        session.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": [att], "_links": {}},
        )
        session.get.return_value.raise_for_status = MagicMock()

        with patch(
            "ckn_ingestion.confluence_extractor.retry_with_backoff",
            side_effect=lambda fn, **kw: fn(),
        ):
            result = _list_attachments(session, "https://example.atlassian.net", "123")

        assert len(result) == 1
        assert result[0].media_type == "image/png"

    def test_filters_archived_attachments(self):
        """Attachments with status 'archived' are excluded from the list."""
        session = MagicMock()
        current_att = {
            "id": "att1",
            "title": "current.png",
            "mediaType": "image/png",
            "status": "current",
            "extensions": {"mediaType": "image/png", "fileSize": 1024},
            "_links": {"download": "/download/attachments/123/current.png"},
        }
        archived_att = {
            "id": "att2",
            "title": "archived.png",
            "mediaType": "image/png",
            "status": "archived",
            "extensions": {"mediaType": "image/png", "fileSize": 1024},
            "_links": {"download": "/download/attachments/123/archived.png"},
        }
        session.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": [current_att, archived_att], "_links": {}},
        )
        session.get.return_value.raise_for_status = MagicMock()

        with patch(
            "ckn_ingestion.confluence_extractor.retry_with_backoff",
            side_effect=lambda fn, **kw: fn(),
        ):
            result = _list_attachments(session, "https://example.atlassian.net", "123")

        assert len(result) == 1
        assert result[0].filename == "current.png"

    def test_includes_page_id_in_attachment(self):
        """Attachment objects include the page_id field."""
        session = MagicMock()
        att = _make_attachment_result()
        session.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": [att], "_links": {}},
        )
        session.get.return_value.raise_for_status = MagicMock()

        with patch(
            "ckn_ingestion.confluence_extractor.retry_with_backoff",
            side_effect=lambda fn, **kw: fn(),
        ):
            result = _list_attachments(session, "https://example.atlassian.net", "456")

        assert len(result) == 1
        assert result[0].page_id == "456"

    def test_expand_extensions_param_sent(self):
        """The API request includes expand=extensions in params."""
        session = MagicMock()
        session.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": [], "_links": {}},
        )
        session.get.return_value.raise_for_status = MagicMock()

        with patch(
            "ckn_ingestion.confluence_extractor.retry_with_backoff",
            side_effect=lambda fn, **kw: fn(),
        ):
            _list_attachments(session, "https://example.atlassian.net", "123")

        # Check that the params include expand=extensions
        call_kwargs = session.get.call_args[1] if session.get.call_args[1] else {}
        params = call_kwargs.get("params", {})
        assert params.get("expand") == "extensions"


# ---------------------------------------------------------------------------
# extract_pages
# ---------------------------------------------------------------------------


class TestExtractPages:
    def _mock_session_get(self, pages_data: dict, attachments_data: dict | None = None):
        """Return a mock session whose .get() returns pages then attachments."""
        att_data = attachments_data or {"results": [], "_links": {}}

        def _get(url, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            if "child/attachment" in url:
                resp.json = lambda: att_data
            else:
                resp.json = lambda: pages_data
            return resp

        session = MagicMock()
        session.get.side_effect = _get
        return session

    def test_yields_page_content(self, confluence_config):
        page = _make_page_result()
        pages_data = {"results": [page], "_links": {}}

        with (
            patch("ckn_ingestion.confluence_extractor._make_session") as mock_make_session,
            patch(
                "ckn_ingestion.confluence_extractor.retry_with_backoff",
                side_effect=lambda fn, **kw: fn(),
            ),
        ):
            mock_make_session.return_value = self._mock_session_get(pages_data)
            results = list(extract_pages(confluence_config, "user@x.com:tok", space_filter="ENG"))

        assert len(results) == 1
        pc = results[0]
        assert isinstance(pc, PageContent)
        assert pc.page_id == "123"
        assert pc.title == "Test Page"
        assert pc.space_key == "ENG"
        assert pc.author == "Alice"
        assert pc.last_modified == "2024-01-01T00:00:00Z"
        assert "https://example.atlassian.net" in pc.url
        assert "Hello" in pc.markdown

    def test_space_filter_restricts_to_one_space(self, confluence_config):
        """When space_filter is set, only that space is queried."""
        pages_data = {"results": [], "_links": {}}
        queried_spaces: list[str] = []

        def _get(url, params=None, timeout=None):
            if params and "spaceKey" in params:
                queried_spaces.append(params["spaceKey"])
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json = lambda: pages_data
            return resp

        session = MagicMock()
        session.get.side_effect = _get

        with (
            patch("ckn_ingestion.confluence_extractor._make_session", return_value=session),
            patch(
                "ckn_ingestion.confluence_extractor.retry_with_backoff",
                side_effect=lambda fn, **kw: fn(),
            ),
        ):
            list(extract_pages(confluence_config, "user@x.com:tok", space_filter="ENG"))

        assert queried_spaces == ["ENG"]

    def test_incremental_crawl_adds_last_modified_param(self, confluence_config):
        """When `since` is provided, lastModified param is sent."""
        pages_data = {"results": [], "_links": {}}
        captured_params: list[dict] = []

        def _get(url, params=None, timeout=None):
            if params:
                captured_params.append(dict(params))
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json = lambda: pages_data
            return resp

        session = MagicMock()
        session.get.side_effect = _get

        with (
            patch("ckn_ingestion.confluence_extractor._make_session", return_value=session),
            patch(
                "ckn_ingestion.confluence_extractor.retry_with_backoff",
                side_effect=lambda fn, **kw: fn(),
            ),
        ):
            list(
                extract_pages(
                    confluence_config,
                    "user@x.com:tok",
                    space_filter="ENG",
                    since="2024-06-01T00:00:00Z",
                )
            )

        page_params = [p for p in captured_params if "spaceKey" in p]
        assert page_params, "No page-list requests were made"
        assert page_params[0]["lastModified"] == "2024-06-01T00:00:00Z"

    def test_skips_page_on_exhausted_retries(self, confluence_config):
        """If retry_with_backoff raises, the page is skipped and iteration continues."""
        call_count = {"n": 0}

        def _failing_retry(fn, **kw):
            call_count["n"] += 1
            # Fail on the first content call (page list), succeed on subsequent
            if call_count["n"] == 1:
                raise requests.HTTPError("429 Too Many Requests")
            return fn()

        session = MagicMock()
        session.get.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=lambda: {"results": [], "_links": {}},
        )

        with (
            patch("ckn_ingestion.confluence_extractor._make_session", return_value=session),
            patch(
                "ckn_ingestion.confluence_extractor.retry_with_backoff", side_effect=_failing_retry
            ),
        ):
            # Should not raise; the space is skipped gracefully
            results = list(extract_pages(confluence_config, "user@x.com:tok", space_filter="ENG"))

        # No pages yielded because the page-list call failed
        assert results == []

    def test_token_without_colon_uses_empty_email(self, confluence_config):
        """A bare token (no colon) should not crash — email defaults to empty."""
        pages_data = {"results": [], "_links": {}}

        session = MagicMock()
        session.get.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=lambda: pages_data,
        )

        with (
            patch("ckn_ingestion.confluence_extractor._make_session") as mock_make_session,
            patch(
                "ckn_ingestion.confluence_extractor.retry_with_backoff",
                side_effect=lambda fn, **kw: fn(),
            ),
        ):
            mock_make_session.return_value = session
            list(extract_pages(confluence_config, "baretoken", space_filter="ENG"))

        # _make_session called with empty email and the bare token
        mock_make_session.assert_called_once_with("", "baretoken")

    def test_attachments_populated_on_page(self, confluence_config):
        page = _make_page_result()
        pages_data = {"results": [page], "_links": {}}
        att = _make_attachment_result()
        att_data = {"results": [att], "_links": {}}

        with (
            patch("ckn_ingestion.confluence_extractor._make_session") as mock_make_session,
            patch(
                "ckn_ingestion.confluence_extractor.retry_with_backoff",
                side_effect=lambda fn, **kw: fn(),
            ),
        ):
            mock_make_session.return_value = self._mock_session_get(pages_data, att_data)
            results = list(extract_pages(confluence_config, "user@x.com:tok", space_filter="ENG"))

        assert len(results) == 1
        assert len(results[0].attachments) == 1
        assert results[0].attachments[0].filename == "diagram.png"

    def test_sanitize_html_invoked_before_html_to_markdown(self, confluence_config):
        """Verify sanitize_html is called before _html_to_markdown (Requirement 5.5)."""
        page = _make_page_result(html_body="<p>Content</p>")
        pages_data = {"results": [page], "_links": {}}

        call_order: list[str] = []

        def _track_sanitize(html):
            call_order.append("sanitize_html")
            return html  # pass-through

        def _track_markdown(html):
            call_order.append("_html_to_markdown")
            return "converted"

        with (
            patch("ckn_ingestion.confluence_extractor._make_session") as mock_make_session,
            patch(
                "ckn_ingestion.confluence_extractor.retry_with_backoff",
                side_effect=lambda fn, **kw: fn(),
            ),
            patch(
                "ckn_ingestion.confluence_extractor.sanitize_html", side_effect=_track_sanitize
            ) as mock_sanitize,
            patch(
                "ckn_ingestion.confluence_extractor._html_to_markdown", side_effect=_track_markdown
            ) as mock_md,
        ):
            mock_make_session.return_value = self._mock_session_get(pages_data)
            list(extract_pages(confluence_config, "user@x.com:tok", space_filter="ENG"))

        # Both must have been called
        mock_sanitize.assert_called_once()
        mock_md.assert_called_once()

        # sanitize_html must appear before _html_to_markdown
        assert call_order == ["sanitize_html", "_html_to_markdown"]
