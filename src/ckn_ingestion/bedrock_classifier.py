"""Bedrock classifier — Claude document classification via InvokeModel."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ckn_ingestion.models import (
    _NORMALIZED_FIELDS,
    FALLBACK_CLASSIFICATION,
    VALID_DOC_TYPES,
    VALID_SEVERITY,
    Classification,
    normalize_vocab,
)
from ckn_ingestion.retry import retry_with_backoff

logger = logging.getLogger(__name__)

_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
_MAX_RETRIES = 3
_REQUIRED_FIELDS = {"doc_type", "service", "severity_relevance", "owner_team", "region", "summary"}

# Patterns commonly used in indirect prompt injection attempts
_INJECTION_PATTERNS = [
    "ignore all previous instructions",
    "ignore the above instructions",
    "disregard previous instructions",
    "forget your instructions",
    "you are now",
    "new instructions:",
    "system prompt:",
    "override:",
]

# Load prompt template once at module level.
# Path is relative to this file: ckn_ingestion/bedrock_classifier.py → ckn_ingestion/doc/prompts/
_PROMPT_PATH = Path(__file__).parent / "doc" / "prompts" / "classification_prompt.txt"
_PROMPT_TEMPLATE: str = _PROMPT_PATH.read_text(encoding="utf-8")


def _sanitize_prompt_input(text: str) -> str:
    """Sanitize untrusted text before interpolation into the classification prompt.

    Applies two defenses against indirect prompt injection (SEC-049):
    1. Strips known prompt injection keyword patterns (case-insensitive).
    2. Wraps the text in XML-style delimiters so the model treats it as data,
       not instructions.

    This does NOT guarantee immunity to all prompt injection attacks, but
    raises the bar significantly for automated/opportunistic attempts.
    """
    import re as _re

    sanitized = text
    for pattern in _INJECTION_PATTERNS:
        sanitized = _re.sub(_re.escape(pattern), "[REDACTED]", sanitized, flags=_re.IGNORECASE)
    return f"<user_document>\n{sanitized}\n</user_document>"


# ---------------------------------------------------------------------------
# ThrottlingException helpers (mirrors image_processor pattern)
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
# Core functions
# ---------------------------------------------------------------------------


def extract_json(raw: str) -> str:
    """Extract a JSON substring from a model response.

    Handles markdown code fences (bare or json-tagged), preamble text,
    and combinations thereof.  Raw JSON strings pass through unchanged;
    strings with no extractable JSON are returned as-is so that the
    downstream ``json.loads`` call raises ``ValueError`` as before.
    """
    text = raw.strip()

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    if text.startswith("```"):
        # Remove the opening fence line
        first_newline = text.index("\n") if "\n" in text else len(text)
        text = text[first_newline + 1 :]
        # Remove the trailing fence
        if text.rstrip().endswith("```"):
            text = text.rstrip()
            text = text[:-3].rstrip()

    # If the string still doesn't start with '{', extract the first JSON object
    text = text.strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]

    return text


def parse_classification(raw_json: str) -> Classification:
    """Parse and validate classification JSON. Raises ValueError on invalid/missing fields.

    Args:
        raw_json: JSON string returned by Claude.

    Returns:
        Validated Classification dataclass.

    Raises:
        ValueError: If JSON is malformed, required fields are missing, values are
                    not strings, or enum fields contain invalid values.
    """
    raw_json = extract_json(raw_json)
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object, got {type(data).__name__}")

    missing = _REQUIRED_FIELDS - data.keys()
    if missing:
        raise ValueError(f"Missing required fields: {sorted(missing)}")

    for field in _REQUIRED_FIELDS:
        if not isinstance(data[field], str):
            raise ValueError(f"Field '{field}' must be a string, got {type(data[field]).__name__}")

    if data["doc_type"] not in VALID_DOC_TYPES:
        raise ValueError(
            f"Invalid doc_type '{data['doc_type']}'. Must be one of {sorted(VALID_DOC_TYPES)}"
        )

    if data["severity_relevance"] not in VALID_SEVERITY:
        raise ValueError(
            f"Invalid severity_relevance '{data['severity_relevance']}'. "
            f"Must be one of {sorted(VALID_SEVERITY)}"
        )

    # F6: normalize the free-text fields (_NORMALIZED_FIELDS: owner_team,
    # service) to a canonical lowercase, hyphen-separated form so equality
    # filters at query time are reliable. doc_type/severity are already closed
    # enums; region and summary are left verbatim (region casing is
    # conventionally lowercase already, and summary is prose, not a filter key).
    normalized = {field: normalize_vocab(data[field]) for field in _NORMALIZED_FIELDS}
    return Classification(
        doc_type=data["doc_type"],
        service=normalized["service"],
        severity_relevance=data["severity_relevance"],
        owner_team=normalized["owner_team"],
        region=data["region"],
        summary=data["summary"],
    )


def classify_page(
    title: str,
    markdown: str,
    bedrock_client: Any,
) -> Classification:
    """Invoke Claude via Bedrock InvokeModel with the AMS classification prompt.

    Loads the classification prompt template, substitutes title and content,
    calls Bedrock InvokeModel with retry on ThrottlingException, then parses
    and validates the JSON response.

    Falls back to FALLBACK_CLASSIFICATION on:
    - JSON parse errors
    - Validation failures
    - ThrottlingException exhaustion

    Args:
        title: Page title (used in prompt substitution).
        markdown: Page markdown content (used in prompt substitution).
        bedrock_client: Boto3 Bedrock Runtime client.

    Returns:
        Validated Classification, or FALLBACK_CLASSIFICATION on any failure.
    """
    # SEC-049: Wrap untrusted content in delimiters and strip known prompt injection patterns
    safe_title = _sanitize_prompt_input(title)
    safe_content = _sanitize_prompt_input(markdown[:600_000])
    prompt = _PROMPT_TEMPLATE.replace("{title}", safe_title).replace("{content}", safe_content)

    def _invoke() -> str:
        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            }
        )
        response = bedrock_client.invoke_model(
            modelId=_MODEL_ID,
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        response_body = json.loads(response["body"].read())
        return response_body["content"][0]["text"]

    # Invoke with throttle retry
    try:
        raw_json = _call_with_throttle_retry(_invoke)
    except _ThrottlingException:
        logger.error(
            "Throttle retries exhausted for page '%s': ThrottlingException — applying fallback.",
            title,
        )
        return FALLBACK_CLASSIFICATION
    except Exception as exc:
        logger.error(
            "Classification failed for page '%s': %s — applying fallback.",
            title,
            type(exc).__name__,
        )
        return FALLBACK_CLASSIFICATION

    # Parse and validate
    try:
        return parse_classification(raw_json)
    except ValueError as exc:
        logger.error(
            "Classification parse/validation failed for page '%s': %s — applying fallback.",
            title,
            exc,
        )
        return FALLBACK_CLASSIFICATION
