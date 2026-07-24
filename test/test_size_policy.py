"""Unit tests for the F5 ingestion size policy (size_policy module)."""

from __future__ import annotations

from ckn_ingestion.models import Classification, PageContent
from ckn_ingestion.size_policy import (
    DEFAULT_MAX_BODY_BYTES,
    body_byte_size,
    build_oversize_placeholder,
    is_body_oversize,
)


def _page(**overrides) -> PageContent:
    base = dict(
        page_id="123456",
        title="Q3 CI-Minutes Billing Export",
        space_key="OPS",
        author="a@example.com",
        last_modified="2026-01-01T00:00:00Z",
        url="https://acme.atlassian.net/wiki/spaces/OPS/pages/123456",
        markdown="body",
    )
    base.update(overrides)
    return PageContent(**base)


def _classification(**overrides) -> Classification:
    base = dict(
        doc_type="reference",
        service="ci",
        severity_relevance="all",
        owner_team="devtools",
        region="us-east-1",
        summary="Per-row CI minutes and cost breakdown for Q3.",
    )
    base.update(overrides)
    return Classification(**base)


class TestByteSize:
    def test_ascii_byte_size_equals_length(self):
        assert body_byte_size("hello") == 5

    def test_multibyte_utf8_counts_bytes_not_chars(self):
        # "€" is 3 UTF-8 bytes; the limit is measured in bytes, not code points.
        assert body_byte_size("€") == 3


class TestIsBodyOversize:
    def test_small_body_is_not_oversize(self):
        assert is_body_oversize("small", max_body_bytes=1000) is False

    def test_body_over_limit_is_oversize(self):
        assert is_body_oversize("x" * 1001, max_body_bytes=1000) is True

    def test_body_exactly_at_limit_is_not_oversize(self):
        # Strict `>`: a body exactly at the threshold is still indexed whole.
        assert is_body_oversize("x" * 1000, max_body_bytes=1000) is False

    def test_default_limit_is_applied_when_omitted(self):
        assert is_body_oversize("x" * (DEFAULT_MAX_BODY_BYTES + 1)) is True
        assert is_body_oversize("x" * DEFAULT_MAX_BODY_BYTES) is False

    def test_multibyte_body_measured_in_bytes(self):
        # 400 "€" = 1200 bytes > 1000, even though only 400 characters.
        assert is_body_oversize("€" * 400, max_body_bytes=1000) is True


class TestPlaceholder:
    def test_placeholder_starts_with_title_heading(self):
        chunk = build_oversize_placeholder(_page(), _classification())
        assert chunk.startswith("# Q3 CI-Minutes Billing Export\n\n")

    def test_placeholder_includes_summary_and_source_link(self):
        chunk = build_oversize_placeholder(_page(), _classification())
        assert "Per-row CI minutes and cost breakdown for Q3." in chunk
        assert "https://acme.atlassian.net/wiki/spaces/OPS/pages/123456" in chunk

    def test_placeholder_notes_body_was_omitted(self):
        chunk = build_oversize_placeholder(_page(), _classification())
        assert "omitted from the index" in chunk

    def test_placeholder_is_far_smaller_than_limit(self):
        # The whole point: the placeholder must be small enough to embed cleanly.
        chunk = build_oversize_placeholder(_page(), _classification())
        assert body_byte_size(chunk) < DEFAULT_MAX_BODY_BYTES

    def test_placeholder_handles_blank_summary(self):
        chunk = build_oversize_placeholder(_page(), _classification(summary="   "))
        assert "No summary available." in chunk
