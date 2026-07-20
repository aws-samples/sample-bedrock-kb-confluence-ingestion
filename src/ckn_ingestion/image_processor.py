"""Image processor — Bedrock Vision for Confluence image attachments."""

# Note: SVG and Draw.io attachment processing is not included in this sample due to dependency license constraints.

from __future__ import annotations

import logging
from typing import Any

import requests

from ckn_ingestion.models import Attachment, PageContent
from ckn_ingestion.retry import retry_with_backoff

logger = logging.getLogger(__name__)

_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
_MAX_RETRIES = 3

MAX_ATTACHMENT_BYTES: int = 50 * 1024 * 1024  # 50 MB default

PROCESSABLE_MEDIA_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
}

_VISION_PROMPT = (
    "Analyze this image across the following six dimensions and provide a comprehensive description:\n"
    "1. Image type (diagram, screenshot, chart, architecture drawing, etc.)\n"
    "2. Visual elements and UI components present\n"
    "3. Text content transcription (all visible text)\n"
    "4. Technical details and functionality depicted\n"
    "5. Context and purpose of the image\n"
    "6. Key data points, metrics, or values shown\n\n"
    "Provide a thorough, structured description that would allow someone to understand "
    "the full content and meaning of this image without seeing it."
)


# ---------------------------------------------------------------------------
# Filename sanitization helpers
# ---------------------------------------------------------------------------


def _sanitize_filename(filename: str) -> str:
    """Strip path separators and parent directory references from *filename*.

    Removes ``/``, ``\\``, and ``..`` sequences so the result is safe for use
    as a plain filename inside a temporary directory.

    Uses iterative replacement to handle nested patterns like ``....`` which
    collapse to ``..`` after a single pass.
    """
    # Strip null bytes — pathlib.Path.resolve() raises ValueError on embedded nulls
    cleaned = filename.replace("\x00", "")
    # Remove path separators
    cleaned = cleaned.replace("/", "").replace("\\", "")
    # Iteratively remove '..' until stable (handles '....' → '..' → '')
    while ".." in cleaned:
        cleaned = cleaned.replace("..", "")
    return cleaned


# ---------------------------------------------------------------------------
# ThrottlingException detection
# ---------------------------------------------------------------------------


def _is_throttling(exc: Exception) -> bool:
    """Return True if *exc* is a Bedrock ThrottlingException."""
    try:
        from botocore.exceptions import ClientError

        if isinstance(exc, ClientError):
            return exc.response["Error"]["Code"] == "ThrottlingException"
    except ImportError:
        pass
    return False


class _ThrottlingException(Exception):
    """Wrapper so retry_with_backoff can match on a concrete type."""


def _call_with_throttle_retry(fn: Any) -> Any:
    """Wrap *fn* so ThrottlingException triggers retry_with_backoff."""

    def _wrapped() -> Any:
        try:
            return fn()
        except Exception as exc:
            if _is_throttling(exc):
                raise _ThrottlingException(str(exc)) from exc
            raise

    return retry_with_backoff(
        _wrapped,
        max_retries=_MAX_RETRIES,
        retryable_exceptions=(_ThrottlingException,),
    )


# ---------------------------------------------------------------------------
# Bedrock Vision call
# ---------------------------------------------------------------------------


def _call_bedrock_vision(image_bytes: bytes, media_format: str, bedrock_client: Any) -> str:
    """Send *image_bytes* to Bedrock Converse API and return the description text.

    Args:
        image_bytes: Raw image bytes (PNG/JPG/GIF).
        media_format: Converse API format string: "png", "jpeg", or "gif".
        bedrock_client: Boto3 Bedrock Runtime client.

    Returns:
        Vision description text from Claude.
    """

    def _invoke() -> str:
        response = bedrock_client.converse(
            modelId=_MODEL_ID,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"text": _VISION_PROMPT},
                        {
                            "image": {
                                "format": media_format,
                                "source": {"bytes": image_bytes},
                            }
                        },
                    ],
                }
            ],
            inferenceConfig={"maxTokens": 1000},
        )
        return response["output"]["message"]["content"][0]["text"]

    return _call_with_throttle_retry(_invoke)


# ---------------------------------------------------------------------------
# Attachment download
# ---------------------------------------------------------------------------


class _AttachmentTooLargeError(Exception):
    """Raised when an attachment exceeds *MAX_ATTACHMENT_BYTES*."""


# Allowlisted domains for attachment download redirects (Atlassian CDN).
_ALLOWED_REDIRECT_DOMAINS = {
    "atlassian.net",
    "atl-paas.net",
    "atlassian.com",
    "amazonaws.com",
}

# Blocked IP ranges for SSRF prevention
_BLOCKED_IP_PREFIXES = (
    "169.254.",  # Link-local / EC2 metadata
    "127.",  # Loopback
    "10.",  # Private RFC1918
    "172.16.",
    "172.17.",
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
    "192.168.",  # Private RFC1918
    "0.",  # Current network
    "[",  # IPv6 (block all for simplicity)
)


def _validate_redirect_url(url: str) -> None:
    """Validate a redirect URL to prevent SSRF (SEC-050).

    Only allows HTTPS URLs whose hostname ends with an allowlisted domain.
    Blocks internal/private IP ranges and non-HTTPS schemes.

    Raises:
        ValueError: If the URL fails validation.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)

    # Must be HTTPS
    if parsed.scheme != "https":
        raise ValueError(f"Redirect URL uses disallowed scheme '{parsed.scheme}': {url!r}")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError(f"Redirect URL has no hostname: {url!r}")

    # Block IP addresses (private ranges, metadata service, etc.)
    if any(hostname.startswith(prefix) for prefix in _BLOCKED_IP_PREFIXES):
        raise ValueError(f"Redirect URL points to a blocked IP range: {url!r}")

    # Check against allowlisted domains
    if not any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in _ALLOWED_REDIRECT_DOMAINS
    ):
        raise ValueError(f"Redirect URL hostname '{hostname}' is not in the allowlist: {url!r}")


def _download_attachment(attachment: Attachment, token: str) -> bytes:
    """Download attachment bytes using Basic auth from *token* ("email:api_token").

    Enforces *MAX_ATTACHMENT_BYTES*:
    - If the ``Content-Length`` header is present and exceeds the limit the
      body is **not** read and the download is aborted immediately.
    - If the header is absent the response is streamed in chunks and aborted
      once accumulated bytes exceed the limit.

    Raises:
        _AttachmentTooLargeError: When the attachment exceeds the size limit.
    """
    if ":" in token:
        email, api_token = token.split(":", 1)
    else:
        email = ""
        api_token = token

    # Phase 1: Hit the REST API download endpoint (returns 302 redirect).
    # Do NOT follow redirects here — the redirect target is a signed CDN URL
    # that does not accept Basic auth and may have different latency.
    response = requests.get(
        attachment.download_url,
        auth=(email, api_token),
        timeout=30,
        allow_redirects=False,
        stream=True,
    )
    try:
        if response.status_code == 302:
            # Phase 2: Download binary from the CDN (no auth, longer timeout).
            # Atlassian's media CDN may reject requests without standard headers.
            redirect_url = response.headers["Location"]
            # SEC-050: Validate redirect URL to prevent SSRF
            _validate_redirect_url(redirect_url)
            response = requests.get(
                redirect_url,
                timeout=(10, 120),  # 10s connect, 120s read
                stream=True,
            )
        response.raise_for_status()
    except requests.exceptions.HTTPError:
        if response.status_code == 404:
            logger.warning(
                "Attachment '%s' returned 404 — may be archived or deleted.",
                attachment.filename,
            )
        raise

    # --- Content-Length present: check before reading body ----------------
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            reported_size = int(content_length)
        except (ValueError, TypeError):
            reported_size = -1

        if reported_size > MAX_ATTACHMENT_BYTES:
            response.close()
            logger.warning(
                "Attachment '%s' exceeds size limit: Content-Length %d bytes "
                "(limit %d bytes); skipping download.",
                attachment.id,
                reported_size,
                MAX_ATTACHMENT_BYTES,
            )
            raise _AttachmentTooLargeError(
                f"Content-Length {reported_size} exceeds limit " f"{MAX_ATTACHMENT_BYTES}"
            )

    # --- Stream and enforce limit when Content-Length is absent -----------
    chunks: list[bytes] = []
    accumulated = 0
    for chunk in response.iter_content(chunk_size=8192):
        accumulated += len(chunk)
        if accumulated > MAX_ATTACHMENT_BYTES:
            response.close()
            logger.warning(
                "Attachment '%s' exceeds size limit during streaming: "
                "accumulated %d bytes (limit %d bytes); aborting download.",
                attachment.id,
                accumulated,
                MAX_ATTACHMENT_BYTES,
            )
            raise _AttachmentTooLargeError(
                f"Accumulated {accumulated} bytes exceeds limit " f"{MAX_ATTACHMENT_BYTES}"
            )
        chunks.append(chunk)

    return b"".join(chunks)


# ---------------------------------------------------------------------------
# Per-type processors
# ---------------------------------------------------------------------------


def process_image_attachment(
    attachment: Attachment,
    image_bytes: bytes,
    bedrock_client: Any,
) -> str:
    """Process a PNG/JPG/GIF image via Bedrock Vision.

    Args:
        attachment: Attachment metadata (filename, media_type, etc.).
        image_bytes: Raw image bytes.
        bedrock_client: Boto3 Bedrock Runtime client.

    Returns:
        Vision_Description text from Claude.
    """
    media_type = attachment.media_type.lower()
    if "jpeg" in media_type or "jpg" in media_type:
        fmt = "jpeg"
    elif "gif" in media_type:
        fmt = "gif"
    else:
        fmt = "png"

    return _call_bedrock_vision(image_bytes, fmt, bedrock_client)


# ---------------------------------------------------------------------------
# Injection helpers
# ---------------------------------------------------------------------------


def _success_block(filename: str, description: str) -> str:
    return f"> **[FIGURA: {filename}]**\n> {description}"


def _failure_block(filename: str) -> str:
    return f"> **[FIGURA: {filename} — não processável]**"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def process_page_images(
    page: PageContent,
    token: str,
    bedrock_client: Any,
) -> str:
    """Process all image attachments on *page* and return enriched markdown.

    Iterates attachments sequentially. For each processable attachment:
    - Downloads bytes
    - Dispatches to the PNG/JPG/GIF processor
    - Appends a Vision_Description block to the markdown

    On any failure, appends a non-processable placeholder and logs a warning.

    Args:
        page: PageContent with markdown and attachment list.
        token: Confluence API token ("email:api_token").
        bedrock_client: Boto3 Bedrock Runtime client.

    Returns:
        Page markdown with Vision_Description blocks appended.
    """
    markdown = page.markdown
    injections: list[str] = []

    for attachment in page.attachments:
        media_type = attachment.media_type.lower()
        filename = _sanitize_filename(attachment.filename)
        if not filename:
            logger.warning(
                "Attachment '%s' on page %s has empty filename after sanitization; skipping.",
                attachment.id,
                page.page_id,
            )
            continue

        if media_type not in PROCESSABLE_MEDIA_TYPES:
            continue

        try:
            # Download
            image_bytes = _download_attachment(attachment, token)

            # Dispatch (PNG/JPG/GIF)
            description = process_image_attachment(attachment, image_bytes, bedrock_client)

            injections.append(_success_block(filename, description))

        except Exception as exc:
            logger.warning(
                "Could not process attachment '%s' on page %s (%s); inserting placeholder.",
                filename,
                page.page_id,
                type(exc).__name__,
                stack_info=False,
            )
            injections.append(_failure_block(filename))

    if injections:
        markdown = markdown.rstrip("\n") + "\n\n" + "\n\n".join(injections)

    return markdown
