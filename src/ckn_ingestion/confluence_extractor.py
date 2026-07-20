"""Confluence Cloud extractor — authentication, page extraction, attachment listing."""

from __future__ import annotations

import logging
import mimetypes
from collections.abc import Iterator
from typing import Any

import boto3
import requests
from botocore.config import Config as BotoConfig

try:
    from markdownify import markdownify as _md_convert

    def _html_to_markdown(html: str) -> str:
        return _md_convert(html, heading_style="ATX")

except ImportError:
    # Stdlib fallback: strip HTML tags, preserving text content.
    import re as _re

    def _html_to_markdown(html: str) -> str:  # type: ignore[misc]
        """Minimal HTML → plain-text fallback using stdlib only."""
        # Replace block-level tags with newlines before stripping.
        text = _re.sub(r"<(br|p|div|li|h[1-6])[^>]*>", "\n", html, flags=_re.IGNORECASE)
        # Strip remaining tags.
        text = _re.sub(r"<[^>]+>", "", text)
        # Collapse excessive blank lines.
        text = _re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


from ckn_ingestion.config import ConfluenceConfig
from ckn_ingestion.models import Attachment, PageContent
from ckn_ingestion.retry import retry_with_backoff
from ckn_ingestion.sanitizer import sanitize_html

logger = logging.getLogger(__name__)

_PAGE_LIMIT = 25  # Confluence Cloud caps at 25 when expand=body.export_view
_MAX_RETRIES = 3

MAX_PAGE_BODY_BYTES: int = 10 * 1024 * 1024  # 10 MB default

_BOTO_CONFIG = BotoConfig(
    connect_timeout=5,
    read_timeout=5,
    retries={"max_attempts": 2},
)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def get_confluence_token(secret_id: str) -> str:
    """Retrieve Confluence API token from Secrets Manager.

    Raises:
        Exception: re-raises the original exception after logging only the
                   error *type* (never the secret value or full message).
    """
    client = boto3.client("secretsmanager", config=_BOTO_CONFIG)
    try:
        response = client.get_secret_value(SecretId=secret_id)
        # SECURITY: The token value (response["SecretString"]) is returned to the
        # caller but never logged, stored to disk, or written to environment variables.
        return response["SecretString"]
    except Exception as exc:
        # SECURITY: Only the exception *type* is logged — never exc.args or str(exc),
        # which may contain partial secret material from the Secrets Manager response.
        logger.error(
            "Failed to retrieve secret '%s': %s",
            secret_id,
            type(exc).__name__,
        )
        raise


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_session(email: str, token: str) -> requests.Session:
    """Return a requests Session pre-configured with Basic auth."""
    session = requests.Session()
    session.auth = (email, token)
    session.headers.update({"Accept": "application/json"})
    return session


def _get_with_retry(session: requests.Session, url: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET *url* with *params*, retrying on HTTP 429."""

    class TooManyRequests(Exception):
        pass

    def _do_request() -> dict[str, Any]:
        resp = session.get(url, params=params, timeout=30)
        if resp.status_code == 429:
            raise TooManyRequests(f"HTTP 429 from {url}")
        resp.raise_for_status()
        return resp.json()

    return retry_with_backoff(
        _do_request,
        max_retries=_MAX_RETRIES,
        retryable_exceptions=(TooManyRequests,),
    )


def _list_attachments(
    session: requests.Session,
    base_url: str,
    page_id: str,
) -> list[Attachment]:
    """Return all attachments for *page_id*."""
    url = f"{base_url}/wiki/rest/api/content/{page_id}/child/attachment"
    params: dict[str, Any] = {"limit": _PAGE_LIMIT, "start": 0, "expand": "extensions"}
    attachments: list[Attachment] = []

    while True:
        try:
            data = _get_with_retry(session, url, params)
        except Exception as exc:
            logger.error(
                "Failed to list attachments for page %s (%s); skipping attachments.",
                page_id,
                type(exc).__name__,
            )
            break

        for result in data.get("results", []):
            if result.get("status") == "archived":
                continue
            try:
                filename = result["title"]
                media_type = result.get("extensions", {}).get("mediaType", "")
                if not media_type or media_type == "application/octet-stream":
                    media_type = mimetypes.guess_type(filename)[0] or ""
                attachments.append(
                    Attachment(
                        id=result["id"],
                        filename=filename,
                        media_type=media_type,
                        file_size=result.get("extensions", {}).get("fileSize", 0),
                        download_url=f"{base_url}/wiki/rest/api/content/{page_id}/child/attachment/{result['id']}/download",
                        page_id=page_id,
                    )
                )
            except (KeyError, TypeError) as exc:
                logger.warning(
                    "Skipping malformed attachment on page %s: %s",
                    page_id,
                    type(exc).__name__,
                )

        # Pagination
        next_link = data.get("_links", {}).get("next")
        if not next_link:
            break
        params["start"] = params["start"] + _PAGE_LIMIT

    return attachments


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_pages(
    config: ConfluenceConfig,
    token: str,
    space_filter: str | None = None,
    since: str | None = None,
) -> Iterator[PageContent]:
    """Yield :class:`PageContent` objects from Confluence.

    Args:
        config: Confluence connection configuration.
        token: API token retrieved from Secrets Manager.
        space_filter: If set, restrict extraction to this single space key.
        since: ISO 8601 timestamp; when provided only pages modified after
               this timestamp are returned (incremental crawl).

    Yields:
        :class:`PageContent` for each successfully extracted page.

    Notes:
        HTTP 429 responses are retried up to ``_MAX_RETRIES`` times with
        exponential backoff.  If retries are exhausted the page is skipped
        and extraction continues with the next page.
    """
    # Determine which spaces to crawl.
    spaces = [space_filter] if space_filter else config.spaces

    # The token stored in Secrets Manager is expected to be
    # "<email>:<api_token>" or just the raw API token.  Confluence Cloud
    # Basic auth requires email + API token.  We store them as
    # "<email>:<token>" so we can split here.
    if ":" in token:
        email, api_token = token.split(":", 1)
    else:
        # Fallback: treat the whole value as the token; email unknown.
        # Callers should store credentials as "email:token".
        email = ""
        api_token = token

    session = _make_session(email, api_token)
    base_url = config.base_url.rstrip("/")
    url = f"{base_url}/wiki/rest/api/content"

    for space_key in spaces:
        logger.info("Extracting pages from space '%s'", space_key)
        start = 0

        while True:
            params: dict[str, Any] = {
                "spaceKey": space_key,
                "expand": "body.export_view,version,space",
                "limit": _PAGE_LIMIT,
                "start": start,
                "type": "page",
            }
            if since:
                params["lastModified"] = since

            try:
                data = _get_with_retry(session, url, params)
            except Exception as exc:
                logger.error(
                    "Failed to fetch page list for space '%s' at offset %d "
                    "(%s); stopping pagination for this space.",
                    space_key,
                    start,
                    type(exc).__name__,
                )
                break

            results = data.get("results", [])
            for result in results:
                page_id = result.get("id", "unknown")
                try:
                    html_body = result.get("body", {}).get("export_view", {}).get("value", "")

                    # --- Size guard (Requirement 3) ---
                    if html_body:
                        body_size = len(html_body.encode("utf-8"))
                        if body_size > MAX_PAGE_BODY_BYTES:
                            logger.warning(
                                "Skipping page %s: body size %d bytes exceeds limit %d bytes",
                                page_id,
                                body_size,
                                MAX_PAGE_BODY_BYTES,
                            )
                            continue

                    if html_body:
                        html_body = sanitize_html(html_body)
                    markdown = _html_to_markdown(html_body) if html_body else ""

                    version_info = result.get("version", {})
                    author = version_info.get("by", {}).get("displayName", "")
                    last_modified = version_info.get("when", "")

                    webui_path = result.get("_links", {}).get("webui", "")
                    page_url = f"{base_url}{webui_path}" if webui_path else ""

                    space_key_val = result.get("space", {}).get("key", space_key)

                    attachments = _list_attachments(session, base_url, page_id)

                    yield PageContent(
                        page_id=page_id,
                        title=result.get("title", ""),
                        space_key=space_key_val,
                        author=author,
                        last_modified=last_modified,
                        url=page_url,
                        markdown=markdown,
                        attachments=attachments,
                    )

                except Exception as exc:
                    logger.error(
                        "Failed to process page %s (%s); skipping.",
                        page_id,
                        type(exc).__name__,
                    )
                    continue

            # Pagination: stop when fewer results than the page limit were returned.
            if len(results) < _PAGE_LIMIT:
                break
            start += _PAGE_LIMIT
