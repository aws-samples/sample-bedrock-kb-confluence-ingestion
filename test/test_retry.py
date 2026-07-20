"""Tests for retry.py — retry_with_backoff."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ckn_ingestion.retry import retry_with_backoff


class _Retryable(Exception):
    pass


class _NonRetryable(Exception):
    pass


class TestRetryWithBackoff:
    def test_returns_result_on_first_success(self):
        result = retry_with_backoff(lambda: 42, retryable_exceptions=(_Retryable,))
        assert result == 42

    def test_retries_on_retryable_exception_and_succeeds(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 2:
                raise _Retryable("first fail")
            return "ok"

        with patch("time.sleep"):
            result = retry_with_backoff(fn, max_retries=3, retryable_exceptions=(_Retryable,))

        assert result == "ok"
        assert calls["n"] == 2

    def test_retries_exactly_max_retries_times_then_raises(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise _Retryable("always fails")

        with patch("time.sleep"):
            with pytest.raises(_Retryable):
                retry_with_backoff(fn, max_retries=3, retryable_exceptions=(_Retryable,))

        # 1 initial attempt + 3 retries = 4 total calls
        assert calls["n"] == 4

    def test_non_retryable_exception_propagates_immediately(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise _NonRetryable("not retryable")

        with patch("time.sleep"):
            with pytest.raises(_NonRetryable):
                retry_with_backoff(fn, max_retries=3, retryable_exceptions=(_Retryable,))

        assert calls["n"] == 1

    def test_default_max_retries_is_3(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise _Retryable("always fails")

        with patch("time.sleep"):
            with pytest.raises(_Retryable):
                retry_with_backoff(fn, retryable_exceptions=(_Retryable,))

        assert calls["n"] == 4  # 1 + 3 retries

    def test_max_retries_zero_raises_immediately(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise _Retryable("fail")

        with patch("time.sleep"):
            with pytest.raises(_Retryable):
                retry_with_backoff(fn, max_retries=0, retryable_exceptions=(_Retryable,))

        assert calls["n"] == 1

    def test_retry_count_via_closure_counter(self):
        attempt_log: list[int] = []

        def fn():
            attempt_log.append(len(attempt_log) + 1)
            if len(attempt_log) < 3:
                raise _Retryable("not yet")
            return "done"

        with patch("time.sleep"):
            result = retry_with_backoff(fn, max_retries=5, retryable_exceptions=(_Retryable,))

        assert result == "done"
        assert len(attempt_log) == 3
