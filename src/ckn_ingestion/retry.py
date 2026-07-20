"""Shared exponential backoff retry utility."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def retry_with_backoff(
    fn: Callable[[], Any],
    max_retries: int = 3,
    retryable_exceptions: tuple[type[Exception], ...] = (),
) -> Any:
    """Execute fn with exponential backoff with jitter on retryable exceptions.

    Retries up to max_retries times when fn raises one of retryable_exceptions.
    Wait time between attempts: 2^attempt seconds + random jitter in [0, 1).

    Raises:
        The last exception raised by fn if all retries are exhausted.
        Any non-retryable exception immediately without retrying.
    """
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return fn()
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt == max_retries:
                logger.error(
                    "All %d retries exhausted (%s).",
                    max_retries,
                    type(exc).__name__,
                )
                raise

            wait = (2**attempt) + random.random()  # noqa: S311 — not crypto
            logger.warning(
                "Attempt %d/%d failed (%s). Retrying in %.2fs.",
                attempt + 1,
                max_retries,
                type(exc).__name__,
                wait,
            )
            time.sleep(wait)

    # Should be unreachable, but satisfies type checkers.
    raise last_exc  # type: ignore[misc]
