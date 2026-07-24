"""Unit tests for content_splitter module.

Requirements: 1.1, 1.2, 1.3, 1.7
"""

from __future__ import annotations

from ckn_ingestion.content_splitter import split_markdown

TITLE = "Technology Stack"
PREFIX = f"# {TITLE}\n\n"


# ---------------------------------------------------------------------------
# No headings → single chunk (Req 1.3)
# ---------------------------------------------------------------------------


class TestNoHeadings:
    def test_plain_text_returns_single_chunk(self):
        md = "Just some plain text without any headings."
        result = split_markdown(md, TITLE)
        assert len(result) == 1
        assert result[0] == PREFIX + md

    def test_single_chunk_starts_with_title_prefix(self):
        md = "No headings here.\n\nAnother paragraph."
        result = split_markdown(md, TITLE)
        assert result[0].startswith(PREFIX)


# ---------------------------------------------------------------------------
# One heading → single chunk (Req 1.3)
# ---------------------------------------------------------------------------


class TestOneHeading:
    def test_single_h1_returns_single_chunk(self):
        md = "# Only Heading\n\nSome content."
        result = split_markdown(md, TITLE)
        assert len(result) == 1
        assert result[0] == PREFIX + md

    def test_single_h2_returns_single_chunk(self):
        md = "## Only Heading\n\nSome content."
        result = split_markdown(md, TITLE)
        assert len(result) == 1
        assert result[0] == PREFIX + md


# ---------------------------------------------------------------------------
# Preamble before first heading (Req 1.1, 1.2)
# ---------------------------------------------------------------------------


class TestPreamble:
    def test_preamble_becomes_chunk_zero(self):
        md = (
            "Intro paragraph before any heading.\n"
            "\n"
            "# Section A\n"
            "\n"
            "Content A.\n"
            "\n"
            "# Section B\n"
            "\n"
            "Content B.\n"
        )
        result = split_markdown(md, TITLE)
        assert len(result) >= 3
        # Chunk 0 should contain the preamble
        assert "Intro paragraph before any heading." in result[0]
        # Chunk 0 should NOT contain heading content
        assert "# Section A" not in result[0]

    def test_preamble_chunk_has_title_prefix(self):
        md = "Preamble text.\n" "\n" "# First\n" "\n" "Body 1.\n" "\n" "# Second\n" "\n" "Body 2.\n"
        result = split_markdown(md, TITLE)
        assert result[0].startswith(PREFIX)


# ---------------------------------------------------------------------------
# Empty input (Req 1.3)
# ---------------------------------------------------------------------------


class TestEmptyInput:
    def test_empty_string_returns_single_chunk(self):
        result = split_markdown("", TITLE)
        assert len(result) == 1
        assert result[0] == PREFIX + ""

    def test_empty_string_chunk_starts_with_prefix(self):
        result = split_markdown("", TITLE)
        assert result[0].startswith(PREFIX)


# ---------------------------------------------------------------------------
# Headings at different levels: h1, h2, h3 (Req 1.1)
# ---------------------------------------------------------------------------


class TestHeadingLevels:
    def test_h1_headings_are_split_boundaries(self):
        md = "# Section One\n" "\n" "Content one.\n" "\n" "# Section Two\n" "\n" "Content two.\n"
        result = split_markdown(md, TITLE)
        assert len(result) == 2
        assert "# Section One" in result[0]
        assert "# Section Two" in result[1]

    def test_h2_headings_are_split_boundaries(self):
        md = "## Part A\n" "\n" "Content A.\n" "\n" "## Part B\n" "\n" "Content B.\n"
        result = split_markdown(md, TITLE)
        assert len(result) == 2
        assert "## Part A" in result[0]
        assert "## Part B" in result[1]

    def test_h3_headings_are_not_split_boundaries(self):
        """h3 headings should NOT cause splitting — only h1 and h2."""
        md = "### Sub A\n" "\n" "Content A.\n" "\n" "### Sub B\n" "\n" "Content B.\n"
        result = split_markdown(md, TITLE)
        # h3 only → fewer than 2 h1/h2 sections → single chunk
        assert len(result) == 1
        assert result[0] == PREFIX + md

    def test_mixed_h1_h2_h3_only_splits_on_h1_h2(self):
        md = (
            "# Main\n"
            "\n"
            "Intro.\n"
            "\n"
            "### Detail\n"
            "\n"
            "Detail text.\n"
            "\n"
            "## Secondary\n"
            "\n"
            "Secondary content.\n"
        )
        result = split_markdown(md, TITLE)
        # Should split at "# Main" and "## Secondary" (2 chunks)
        assert len(result) == 2
        # First chunk contains h1 and h3 (h3 stays within the section)
        assert "# Main" in result[0]
        assert "### Detail" in result[0]
        # Second chunk contains h2
        assert "## Secondary" in result[1]

    def test_malformed_heading_no_space_not_a_boundary(self):
        """#no space should not be treated as a heading boundary."""
        md = (
            "#no space heading\n" "\n" "Some text.\n" "\n" "#another no space\n" "\n" "More text.\n"
        )
        result = split_markdown(md, TITLE)
        # Malformed headings → no valid h1/h2 boundaries → single chunk
        assert len(result) == 1
        assert result[0] == PREFIX + md


# ---------------------------------------------------------------------------
# Heading-only content (heading with no body) (Req 1.1)
# ---------------------------------------------------------------------------


class TestHeadingOnlyContent:
    def test_headings_with_no_body_are_dropped(self):
        md = "# Empty Section\n" "# Another Empty\n" "# Has Content\n" "\n" "Actual body text.\n"
        result = split_markdown(md, TITLE)
        # Empty sections (heading with no body) should be dropped
        # Only the section with body content should remain
        for chunk in result:
            stripped = chunk.removeprefix(PREFIX)
            # Each non-empty chunk should have some non-heading content
            if stripped.strip():
                # At least one non-heading line with content, or it's a preamble.
                # The key point: empty heading-only chunks are not in the result.
                pass
        # The result should not contain chunks that are just a heading with no body
        for chunk in result:
            content_after_prefix = chunk.removeprefix(PREFIX)
            # If it starts with a heading, there should be body content
            if content_after_prefix.strip().startswith("#"):
                first_newline = content_after_prefix.find("\n")
                if first_newline != -1:
                    body = content_after_prefix[first_newline + 1 :]
                    assert body.strip(), "Empty heading-only chunk should have been dropped"


# ---------------------------------------------------------------------------
# Whitespace-only input (Req 1.3)
# ---------------------------------------------------------------------------


class TestWhitespaceInput:
    def test_whitespace_only_returns_single_chunk(self):
        md = "   \n\n  \t  \n"
        result = split_markdown(md, TITLE)
        assert len(result) == 1
        assert result[0] == PREFIX + md


# ---------------------------------------------------------------------------
# Title prefix invariant (Req 1.2)
# ---------------------------------------------------------------------------


class TestTitlePrefix:
    def test_every_chunk_starts_with_title_prefix(self):
        md = (
            "Preamble.\n"
            "\n"
            "# Section A\n"
            "\n"
            "Content A.\n"
            "\n"
            "## Section B\n"
            "\n"
            "Content B.\n"
            "\n"
            "# Section C\n"
            "\n"
            "Content C.\n"
        )
        result = split_markdown(md, TITLE)
        for chunk in result:
            assert chunk.startswith(PREFIX), f"Chunk missing title prefix: {chunk[:50]!r}"

    def test_title_with_special_characters(self):
        title = "AWS: Lambda & API Gateway (v2)"
        prefix = f"# {title}\n\n"
        md = "# Intro\n\nText.\n\n## Details\n\nMore text.\n"
        result = split_markdown(md, title)
        for chunk in result:
            assert chunk.startswith(prefix)


# ---------------------------------------------------------------------------
# Intra-section size cap (F9 Option A prerequisite, issue #20)
# ---------------------------------------------------------------------------


class TestSizeCap:
    def _reassemble(self, chunks: list[str], title: str) -> str:
        """Reconstruct the prefixed original from chunks (strip prefix on 1..N)."""
        prefix = f"# {title}\n\n"
        parts = [chunks[0]] + [c[len(prefix) :] for c in chunks[1:]]
        return "".join(parts)

    def test_small_input_not_split_by_cap(self):
        md = "# A\n\nshort\n\n# B\n\nalso short\n"
        result = split_markdown(md, TITLE, max_chunk_chars=20_000)
        # 2 headings, both tiny → 2 chunks, cap has no effect
        assert len(result) == 2

    def test_headingless_oversize_page_is_split(self):
        # No headings at all: previously one chunk regardless of size.
        body = "\n\n".join([f"Paragraph {i} " + "x" * 200 for i in range(50)])
        cap = 2_000
        result = split_markdown(body, TITLE, max_chunk_chars=cap)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= cap
            assert chunk.startswith(PREFIX)

    def test_single_oversize_section_is_split(self):
        # One H1 section whose body far exceeds the cap.
        big = "# Big Section\n\n" + "\n\n".join("word " * 100 for _ in range(40))
        small = "# Small\n\ntiny body\n"
        md = big + "\n\n" + small
        cap = 3_000
        result = split_markdown(md, TITLE, max_chunk_chars=cap)
        for chunk in result:
            assert len(chunk) <= cap
            assert chunk.startswith(PREFIX)

    def test_oversize_split_preserves_content(self):
        # Round-trip: reassembling chunks reproduces "# TITLE\n\n" + original.
        body = "\n\n".join([f"Para {i}: " + "data " * 80 for i in range(30)])
        cap = 2_500
        result = split_markdown(body, TITLE, max_chunk_chars=cap)
        assert self._reassemble(result, TITLE) == PREFIX + body

    def test_single_paragraph_larger_than_cap_hard_split(self):
        # A single unbroken paragraph bigger than the cap must still be split
        # and never exceed it.
        body = "z" * 10_000
        cap = 1_500
        result = split_markdown(body, TITLE, max_chunk_chars=cap)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= cap
            assert chunk.startswith(PREFIX)
        # Content preserved despite hard-splitting.
        assert self._reassemble(result, TITLE) == PREFIX + body

    def test_every_oversize_chunk_within_cap(self):
        md = (
            "# Section One\n\n" + "alpha " * 500 + "\n\n"
            "# Section Two\n\n" + "beta " * 500 + "\n\n"
            "# Section Three\n\n" + "gamma " * 500
        )
        cap = 1_800
        result = split_markdown(md, TITLE, max_chunk_chars=cap)
        for chunk in result:
            assert len(chunk) <= cap

    def test_oversize_whitespace_only_input_is_capped(self):
        # Pathological: a whitespace run longer than the cap must NOT bypass
        # the size cap via the empty/whitespace early-return.
        md = " " * 30_000
        cap = 5_000
        result = split_markdown(md, TITLE, max_chunk_chars=cap)
        for chunk in result:
            assert len(chunk) <= cap
            assert chunk.startswith(PREFIX)
        # Content still preserved.
        assert self._reassemble(result, TITLE) == PREFIX + md
