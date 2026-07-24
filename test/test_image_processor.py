"""Unit tests for image_processor module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from ckn_ingestion.image_processor import (
    PROCESSABLE_MEDIA_TYPES,
    _MODEL_ID,
    _failure_block,
    _success_block,
    process_image_attachment,
    process_page_images,
)
from ckn_ingestion.models import Attachment, PageContent

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_attachment(
    filename: str = "diagram.png",
    media_type: str = "image/png",
    download_url: str = "https://example.atlassian.net/download/attachments/123/diagram.png",
) -> Attachment:
    return Attachment(
        id="att1",
        filename=filename,
        media_type=media_type,
        file_size=1024,
        download_url=download_url,
    )


def _make_page(
    markdown: str = "# Page\n\nSome content.",
    attachments: list[Attachment] | None = None,
) -> PageContent:
    return PageContent(
        page_id="123",
        title="Test Page",
        space_key="ENG",
        author="Alice",
        last_modified="2024-01-01T00:00:00Z",
        url="https://example.atlassian.net/wiki/spaces/ENG/pages/123",
        markdown=markdown,
        attachments=attachments or [],
    )


def _make_bedrock_client(
    description: str = "A flowchart showing the deployment pipeline.",
) -> MagicMock:
    """Return a mock Bedrock client whose converse() returns *description*."""
    client = MagicMock()
    client.converse.return_value = {"output": {"message": {"content": [{"text": description}]}}}
    return client


# ---------------------------------------------------------------------------
# PROCESSABLE_MEDIA_TYPES
# ---------------------------------------------------------------------------


class TestProcessableMediaTypes:
    def test_contains_expected_types(self):
        assert "image/png" in PROCESSABLE_MEDIA_TYPES
        assert "image/jpeg" in PROCESSABLE_MEDIA_TYPES
        assert "image/gif" in PROCESSABLE_MEDIA_TYPES

    def test_does_not_contain_pdf(self):
        assert "application/pdf" not in PROCESSABLE_MEDIA_TYPES

    def test_does_not_contain_svg(self):
        # SVG processing was removed due to dependency (cairosvg) license constraints.
        assert "image/svg+xml" not in PROCESSABLE_MEDIA_TYPES


# ---------------------------------------------------------------------------
# Injection format helpers
# ---------------------------------------------------------------------------


class TestInjectionBlocks:
    def test_success_block_format(self):
        block = _success_block("diagram.png", "A flowchart.")
        assert block == "> **[FIGURA: diagram.png]**\n> A flowchart."

    def test_failure_block_format(self):
        block = _failure_block("diagram.png")
        assert block == "> **[FIGURA: diagram.png — não processável]**"

    def test_success_block_with_special_chars(self):
        block = _success_block("arch & design.png", "Shows AWS services.")
        assert "arch & design.png" in block
        assert "Shows AWS services." in block


# ---------------------------------------------------------------------------
# process_image_attachment
# ---------------------------------------------------------------------------


class TestProcessImageAttachment:
    def test_calls_bedrock_converse_with_png(self):
        attachment = _make_attachment("img.png", "image/png")
        client = _make_bedrock_client("A PNG diagram.")

        with patch(
            "ckn_ingestion.image_processor._call_with_throttle_retry", side_effect=lambda fn: fn()
        ):
            result = process_image_attachment(attachment, b"fake-png-bytes", client)

        assert result == "A PNG diagram."
        client.converse.assert_called_once()
        call_kwargs = client.converse.call_args[1]
        # Assert against the module constant so a future model bump doesn't
        # re-stale this test (the pipeline uses a cross-region inference profile).
        assert call_kwargs["modelId"] == _MODEL_ID
        # Verify image format is png
        content = call_kwargs["messages"][0]["content"]
        image_block = next(c for c in content if "image" in c)
        assert image_block["image"]["format"] == "png"
        assert image_block["image"]["source"]["bytes"] == b"fake-png-bytes"

    def test_calls_bedrock_converse_with_jpeg(self):
        attachment = _make_attachment("photo.jpg", "image/jpeg")
        client = _make_bedrock_client("A JPEG photo.")

        with patch(
            "ckn_ingestion.image_processor._call_with_throttle_retry", side_effect=lambda fn: fn()
        ):
            result = process_image_attachment(attachment, b"fake-jpg-bytes", client)

        assert result == "A JPEG photo."
        call_kwargs = client.converse.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        image_block = next(c for c in content if "image" in c)
        assert image_block["image"]["format"] == "jpeg"

    def test_calls_bedrock_converse_with_gif(self):
        attachment = _make_attachment("anim.gif", "image/gif")
        client = _make_bedrock_client("An animated GIF.")

        with patch(
            "ckn_ingestion.image_processor._call_with_throttle_retry", side_effect=lambda fn: fn()
        ):
            result = process_image_attachment(attachment, b"fake-gif-bytes", client)

        assert result == "An animated GIF."
        call_kwargs = client.converse.call_args[1]
        content = call_kwargs["messages"][0]["content"]
        image_block = next(c for c in content if "image" in c)
        assert image_block["image"]["format"] == "gif"

    def test_max_tokens_is_1000(self):
        attachment = _make_attachment()
        client = _make_bedrock_client()

        with patch(
            "ckn_ingestion.image_processor._call_with_throttle_retry", side_effect=lambda fn: fn()
        ):
            process_image_attachment(attachment, b"bytes", client)

        call_kwargs = client.converse.call_args[1]
        assert call_kwargs["inferenceConfig"]["maxTokens"] == 1000


# ---------------------------------------------------------------------------
# process_svg_attachment
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="SVG attachment processing was removed due to dependency (cairosvg) license constraints."
)
class TestProcessSvgAttachment:
    """SVG processing (process_svg_attachment) was removed; tests retained as skipped stubs."""

    def test_converts_svg_to_png_and_calls_vision(self):
        pass


# ---------------------------------------------------------------------------
# process_page_images — dispatch and injection
# ---------------------------------------------------------------------------


class TestProcessPageImages:
    def test_skips_non_processable_attachments(self):
        att = _make_attachment("doc.pdf", "application/pdf")
        page = _make_page(attachments=[att])
        client = _make_bedrock_client()

        result = process_page_images(page, "user@x.com:tok", client)

        assert result == page.markdown
        client.converse.assert_not_called()

    def test_injects_success_block_for_png(self):
        att = _make_attachment("diagram.png", "image/png")
        page = _make_page(attachments=[att])
        client = _make_bedrock_client("A deployment diagram.")

        with (
            patch("ckn_ingestion.image_processor._download_attachment", return_value=b"png-bytes"),
            patch(
                "ckn_ingestion.image_processor._call_with_throttle_retry",
                side_effect=lambda fn: fn(),
            ),
        ):
            result = process_page_images(page, "user@x.com:tok", client)

        assert "> **[FIGURA: diagram.png]**" in result
        assert "> A deployment diagram." in result

    def test_injects_failure_block_on_download_error(self):
        att = _make_attachment("diagram.png", "image/png")
        page = _make_page(attachments=[att])
        client = _make_bedrock_client()

        with patch(
            "ckn_ingestion.image_processor._download_attachment",
            side_effect=ConnectionError("network error"),
        ):
            result = process_page_images(page, "user@x.com:tok", client)

        assert "> **[FIGURA: diagram.png — não processável]**" in result

    def test_injects_failure_block_on_bedrock_error(self):
        att = _make_attachment("diagram.png", "image/png")
        page = _make_page(attachments=[att])
        client = MagicMock()
        client.converse.side_effect = RuntimeError("Bedrock error")

        with patch("ckn_ingestion.image_processor._download_attachment", return_value=b"bytes"):
            result = process_page_images(page, "user@x.com:tok", client)

        assert "> **[FIGURA: diagram.png — não processável]**" in result

    def test_processes_multiple_attachments_sequentially(self):
        att1 = _make_attachment("img1.png", "image/png")
        att2 = _make_attachment("img2.png", "image/png")
        page = _make_page(attachments=[att1, att2])

        call_order: list[str] = []

        def fake_download(attachment, token):
            call_order.append(attachment.filename)
            return b"bytes"

        client = _make_bedrock_client("A diagram.")

        with (
            patch("ckn_ingestion.image_processor._download_attachment", side_effect=fake_download),
            patch(
                "ckn_ingestion.image_processor._call_with_throttle_retry",
                side_effect=lambda fn: fn(),
            ),
        ):
            result = process_page_images(page, "user@x.com:tok", client)

        assert call_order == ["img1.png", "img2.png"]
        assert "> **[FIGURA: img1.png]**" in result
        assert "> **[FIGURA: img2.png]**" in result

    @pytest.mark.skip(
        reason="SVG dispatch removed due to dependency (cairosvg) license constraints."
    )
    def test_dispatches_svg_to_svg_processor(self):
        pass

    @pytest.mark.skip(
        reason="Draw.io dispatch removed due to dependency (drawio/cairosvg) license constraints."
    )
    def test_dispatches_drawio_to_drawio_processor(self):
        pass

    def test_no_attachments_returns_original_markdown(self):
        page = _make_page(markdown="# Title\n\nContent.", attachments=[])
        client = _make_bedrock_client()

        result = process_page_images(page, "user@x.com:tok", client)

        assert result == "# Title\n\nContent."
        client.converse.assert_not_called()

    def test_one_failure_does_not_stop_other_attachments(self):
        att1 = _make_attachment("bad.png", "image/png")
        att2 = _make_attachment("good.png", "image/png")
        page = _make_page(attachments=[att1, att2])
        client = _make_bedrock_client("Good image.")

        def fake_download(attachment, token):
            if attachment.filename == "bad.png":
                raise ConnectionError("network error")
            return b"bytes"

        with (
            patch("ckn_ingestion.image_processor._download_attachment", side_effect=fake_download),
            patch(
                "ckn_ingestion.image_processor._call_with_throttle_retry",
                side_effect=lambda fn: fn(),
            ),
        ):
            result = process_page_images(page, "user@x.com:tok", client)

        assert "> **[FIGURA: bad.png — não processável]**" in result
        assert "> **[FIGURA: good.png]**" in result
        assert "> Good image." in result

    def test_token_split_on_first_colon(self):
        """Token with multiple colons splits on first colon only."""
        att = _make_attachment("img.png", "image/png")
        page = _make_page(attachments=[att])
        client = _make_bedrock_client("desc")

        captured: list[tuple] = []

        def fake_download(attachment, token):
            captured.append(token)
            return b"bytes"

        with (
            patch("ckn_ingestion.image_processor._download_attachment", side_effect=fake_download),
            patch(
                "ckn_ingestion.image_processor._call_with_throttle_retry",
                side_effect=lambda fn: fn(),
            ),
        ):
            process_page_images(page, "user@x.com:tok:extra", client)

        assert captured[0] == "user@x.com:tok:extra"

    def test_warning_logged_on_failure(self, caplog):
        att = _make_attachment("fail.png", "image/png")
        page = _make_page(attachments=[att])
        client = _make_bedrock_client()

        with (
            patch(
                "ckn_ingestion.image_processor._download_attachment",
                side_effect=RuntimeError("boom"),
            ),
            caplog.at_level("WARNING", logger="ckn_ingestion.image_processor"),
        ):
            process_page_images(page, "user@x.com:tok", client)

        assert "fail.png" in caplog.text
        # Binary data must not appear in logs
        assert "boom" not in caplog.text or "fail.png" in caplog.text

    def test_no_binary_data_in_logs(self, caplog):
        """Image bytes must never appear in log output."""
        att = _make_attachment("img.png", "image/png")
        page = _make_page(attachments=[att])
        client = _make_bedrock_client("desc")

        with (
            patch(
                "ckn_ingestion.image_processor._download_attachment",
                return_value=b"\x89PNG\r\n\x1a\n",
            ),
            patch(
                "ckn_ingestion.image_processor._call_with_throttle_retry",
                side_effect=lambda fn: fn(),
            ),
            caplog.at_level("DEBUG", logger="ckn_ingestion.image_processor"),
        ):
            process_page_images(page, "user@x.com:tok", client)

        assert b"\x89PNG".decode(errors="replace") not in caplog.text


# ---------------------------------------------------------------------------
# Throttle retry integration
# ---------------------------------------------------------------------------


class TestThrottleRetry:
    def test_throttling_exception_triggers_retry(self):
        """ThrottlingException from Bedrock should be retried."""
        from botocore.exceptions import ClientError

        attachment = _make_attachment("img.png", "image/png")
        client = MagicMock()

        throttle_error = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
            "Converse",
        )
        # Fail twice, succeed on third
        client.converse.side_effect = [
            throttle_error,
            throttle_error,
            {"output": {"message": {"content": [{"text": "Success after retries."}]}}},
        ]

        result = process_image_attachment(attachment, b"bytes", client)

        assert result == "Success after retries."
        assert client.converse.call_count == 3

    def test_throttling_exhaustion_raises(self):
        """After max retries, ThrottlingException propagates."""
        from botocore.exceptions import ClientError

        attachment = _make_attachment("img.png", "image/png")
        client = MagicMock()

        throttle_error = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
            "Converse",
        )
        client.converse.side_effect = throttle_error

        with pytest.raises(Exception):
            process_image_attachment(attachment, b"bytes", client)

    def test_throttle_exhaustion_treated_as_non_processable(self):
        """When throttle retries exhaust in process_page_images, placeholder is injected."""
        from botocore.exceptions import ClientError

        att = _make_attachment("img.png", "image/png")
        page = _make_page(attachments=[att])
        client = MagicMock()

        throttle_error = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
            "Converse",
        )
        client.converse.side_effect = throttle_error

        with patch("ckn_ingestion.image_processor._download_attachment", return_value=b"bytes"):
            result = process_page_images(page, "user@x.com:tok", client)

        assert "> **[FIGURA: img.png — não processável]**" in result


# ---------------------------------------------------------------------------
# _download_attachment — 2-phase download and 404 handling
# ---------------------------------------------------------------------------


class TestDownloadAttachment:
    """Tests for the 2-phase download flow (302 → CDN) and error handling."""

    def test_follows_302_redirect_to_cdn_without_auth(self):
        """Phase 1 returns 302; Phase 2 fetches from CDN without Basic auth."""
        from ckn_ingestion.image_processor import _download_attachment

        att = _make_attachment(
            download_url="https://example.atlassian.net/wiki/rest/api/content/123/child/attachment/att1/download"
        )

        # Phase 1: 302 with Location header
        phase1_response = MagicMock()
        phase1_response.status_code = 302
        phase1_response.headers = {
            "Location": "https://media-cdn.atlassian.net/signed/image.png?token=abc"
        }

        # Phase 2: 200 from CDN
        phase2_response = MagicMock()
        phase2_response.status_code = 200
        phase2_response.headers = {"Content-Length": "1024"}
        phase2_response.raise_for_status = MagicMock()
        phase2_response.iter_content = MagicMock(return_value=iter([b"image-bytes"]))

        with patch("ckn_ingestion.image_processor.requests.get") as mock_get:
            mock_get.side_effect = [phase1_response, phase2_response]
            result = _download_attachment(att, "user@example.com:api_token")

        assert result == b"image-bytes"

        # Verify Phase 1 call: Basic auth, allow_redirects=False
        call1 = mock_get.call_args_list[0]
        assert call1[0][0] == att.download_url
        assert call1[1]["auth"] == ("user@example.com", "api_token")
        assert call1[1]["allow_redirects"] is False

        # Verify Phase 2 call: NO auth, stream=True
        call2 = mock_get.call_args_list[1]
        assert call2[0][0] == "https://media-cdn.atlassian.net/signed/image.png?token=abc"
        assert "auth" not in call2[1]
        assert call2[1]["stream"] is True

    def test_direct_200_without_redirect(self):
        """If Phase 1 returns 200 directly (no redirect), content is read normally."""
        from ckn_ingestion.image_processor import _download_attachment

        att = _make_attachment(
            download_url="https://example.atlassian.net/wiki/rest/api/content/123/child/attachment/att1/download"
        )

        # Direct 200 response (no redirect)
        response = MagicMock()
        response.status_code = 200
        response.headers = {"Content-Length": "512"}
        response.raise_for_status = MagicMock()
        response.iter_content = MagicMock(return_value=iter([b"direct-bytes"]))

        with patch("ckn_ingestion.image_processor.requests.get", return_value=response):
            result = _download_attachment(att, "user@example.com:token")

        assert result == b"direct-bytes"

    def test_404_logs_specific_warning_and_raises(self, caplog):
        """HTTP 404 logs a specific archived/deleted warning before re-raising."""
        from ckn_ingestion.image_processor import _download_attachment

        att = _make_attachment(
            filename="archived_image.png",
            download_url="https://example.atlassian.net/wiki/rest/api/content/123/child/attachment/att1/download",
        )

        response = MagicMock()
        response.status_code = 404
        response.headers = {}
        response.raise_for_status = MagicMock(
            side_effect=requests.exceptions.HTTPError(response=response)
        )

        with (
            patch("ckn_ingestion.image_processor.requests.get", return_value=response),
            caplog.at_level("WARNING", logger="ckn_ingestion.image_processor"),
            pytest.raises(requests.exceptions.HTTPError),
        ):
            _download_attachment(att, "user@example.com:token")

        assert "archived_image.png" in caplog.text
        assert "404" in caplog.text
        assert "archived or deleted" in caplog.text

    def test_401_raises_without_archived_warning(self, caplog):
        """HTTP 401 raises without the specific archived/deleted warning."""
        from ckn_ingestion.image_processor import _download_attachment

        att = _make_attachment(
            filename="secret_image.png",
            download_url="https://example.atlassian.net/wiki/rest/api/content/123/child/attachment/att1/download",
        )

        response = MagicMock()
        response.status_code = 401
        response.headers = {}
        response.raise_for_status = MagicMock(
            side_effect=requests.exceptions.HTTPError(response=response)
        )

        with (
            patch("ckn_ingestion.image_processor.requests.get", return_value=response),
            caplog.at_level("WARNING", logger="ckn_ingestion.image_processor"),
            pytest.raises(requests.exceptions.HTTPError),
        ):
            _download_attachment(att, "user@example.com:token")

        assert "archived or deleted" not in caplog.text

    def test_phase2_timeout_is_longer_than_phase1(self):
        """CDN download (Phase 2) uses a longer timeout than the API call (Phase 1)."""
        from ckn_ingestion.image_processor import _download_attachment

        att = _make_attachment(
            download_url="https://example.atlassian.net/wiki/rest/api/content/123/child/attachment/att1/download"
        )

        phase1_response = MagicMock()
        phase1_response.status_code = 302
        # Redirect target must be an allowlisted host (SEC-050 redirect hardening);
        # real Confluence Cloud redirects to *.atlassian.net. Matches the sibling
        # test_follows_302_redirect_to_cdn_without_auth fixture.
        phase1_response.headers = {
            "Location": "https://media-cdn.atlassian.net/signed/image.png?token=abc"
        }

        phase2_response = MagicMock()
        phase2_response.status_code = 200
        phase2_response.headers = {"Content-Length": "100"}
        phase2_response.raise_for_status = MagicMock()
        phase2_response.iter_content = MagicMock(return_value=iter([b"x"]))

        with patch("ckn_ingestion.image_processor.requests.get") as mock_get:
            mock_get.side_effect = [phase1_response, phase2_response]
            _download_attachment(att, "user@x.com:tok")

        # Phase 1 timeout
        phase1_timeout = mock_get.call_args_list[0][1]["timeout"]
        assert phase1_timeout == 30

        # Phase 2 timeout — tuple (connect, read) with longer read timeout
        phase2_timeout = mock_get.call_args_list[1][1]["timeout"]
        assert isinstance(phase2_timeout, tuple)
        assert phase2_timeout[1] >= 60  # CDN read timeout should be generous


# ---------------------------------------------------------------------------
# Bedrock timeout configuration
# ---------------------------------------------------------------------------


class TestBedrockTimeoutConfig:
    """Tests for the Bedrock boto3 client timeout configuration."""

    def test_boto_config_read_timeout_sufficient_for_bedrock_vision(self):
        """read_timeout must be >= 60s for Bedrock Vision API (10-15s per image)."""
        pytest.importorskip("pythonjsonlogger", reason="pythonjsonlogger not available in test env")
        from ckn_ingestion.cli import _BOTO_CONFIG

        # BotoConfig stores values in _user_provided_options
        assert _BOTO_CONFIG.connect_timeout >= 5
        assert _BOTO_CONFIG.read_timeout >= 60

    def test_boto_config_has_retries(self):
        """Boto config should include retry configuration."""
        pytest.importorskip("pythonjsonlogger", reason="pythonjsonlogger not available in test env")
        from ckn_ingestion.cli import _BOTO_CONFIG

        # Verify retries are configured (max_attempts >= 2)
        assert _BOTO_CONFIG.retries["max_attempts"] >= 2
