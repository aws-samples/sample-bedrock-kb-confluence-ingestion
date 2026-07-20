"""Unit tests for table_flattener module.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6
"""

from __future__ import annotations

from ckn_ingestion.table_flattener import flatten_tables

TITLE = "Technology Stack"


# ---------------------------------------------------------------------------
# Single table (Req 2.1, 2.2)
# ---------------------------------------------------------------------------


class TestSingleTable:
    def test_single_table_produces_sentences(self):
        md = "| Library | Version |\n" "| --- | --- |\n" "| React | 18.2 |\n" "| Vue | 3.3 |\n"
        result = flatten_tables(md, TITLE)
        assert "Technology Stack: Library is React, Version is 18.2" in result
        assert "Technology Stack: Library is Vue, Version is 3.3" in result

    def test_single_table_sentence_count_equals_data_rows(self):
        md = (
            "| Name | Role |\n"
            "| --- | --- |\n"
            "| Alice | Engineer |\n"
            "| Bob | Manager |\n"
            "| Carol | Designer |\n"
        )
        result = flatten_tables(md, TITLE)
        sentences = [ln for ln in result.strip().split("\n") if ln.startswith("Technology Stack:")]
        assert len(sentences) == 3

    def test_separator_row_not_in_output(self):
        """Req 2.3: separator rows are skipped."""
        md = "| A | B |\n" "| --- | --- |\n" "| 1 | 2 |\n"
        result = flatten_tables(md, TITLE)
        assert "---" not in result


# ---------------------------------------------------------------------------
# Multiple tables (Req 2.1, 2.6)
# ---------------------------------------------------------------------------


class TestMultipleTables:
    def test_multiple_tables_all_flattened(self):
        md = (
            "| Col1 |\n"
            "| --- |\n"
            "| val1 |\n"
            "\n"
            "Some text\n"
            "\n"
            "| Col2 |\n"
            "| --- |\n"
            "| val2 |\n"
        )
        result = flatten_tables(md, TITLE)
        assert "Technology Stack: Col1 is val1" in result
        assert "Technology Stack: Col2 is val2" in result


# ---------------------------------------------------------------------------
# No tables (Req 2.4)
# ---------------------------------------------------------------------------


class TestNoTables:
    def test_no_tables_returns_unchanged(self):
        md = "# Heading\n\nSome paragraph text.\n\n- list item\n"
        result = flatten_tables(md, TITLE)
        assert result == md

    def test_empty_string_returns_empty(self):
        assert flatten_tables("", TITLE) == ""


# ---------------------------------------------------------------------------
# Empty cells (Req 2.5)
# ---------------------------------------------------------------------------


class TestEmptyCells:
    def test_empty_cell_omitted_from_sentence(self):
        md = "| Name | Notes |\n" "| --- | --- |\n" "| Alice |  |\n"
        result = flatten_tables(md, TITLE)
        assert "Technology Stack: Name is Alice" in result
        assert "Notes is" not in result

    def test_all_cells_empty_produces_no_sentence(self):
        md = "| A | B |\n" "| --- | --- |\n" "|  |  |\n"
        result = flatten_tables(md, TITLE)
        lines = [ln for ln in result.strip().split("\n") if ln]
        assert all(not ln.startswith("Technology Stack:") for ln in lines)


# ---------------------------------------------------------------------------
# Separator-only table (header + separator, no data rows) (Req 2.3)
# ---------------------------------------------------------------------------


class TestSeparatorOnlyTable:
    def test_header_and_separator_only_produces_no_sentences(self):
        md = "| Col1 | Col2 |\n" "| --- | --- |\n"
        result = flatten_tables(md, TITLE)
        assert "Technology Stack:" not in result


# ---------------------------------------------------------------------------
# Table at start / end of document (Req 2.1, 2.6)
# ---------------------------------------------------------------------------


class TestTablePosition:
    def test_table_at_start_of_document(self):
        md = "| Key | Value |\n" "| --- | --- |\n" "| foo | bar |\n" "\n" "Trailing paragraph.\n"
        result = flatten_tables(md, TITLE)
        assert "Technology Stack: Key is foo, Value is bar" in result
        assert "Trailing paragraph." in result

    def test_table_at_end_of_document(self):
        md = "Leading paragraph.\n" "\n" "| Key | Value |\n" "| --- | --- |\n" "| foo | bar |\n"
        result = flatten_tables(md, TITLE)
        assert "Leading paragraph." in result
        assert "Technology Stack: Key is foo, Value is bar" in result


# ---------------------------------------------------------------------------
# Mixed content (Req 2.6)
# ---------------------------------------------------------------------------


class TestMixedContent:
    def test_non_table_content_preserved(self):
        md = (
            "# Overview\n"
            "\n"
            "Intro paragraph.\n"
            "\n"
            "| Lib | Ver |\n"
            "| --- | --- |\n"
            "| React | 18 |\n"
            "\n"
            "## Notes\n"
            "\n"
            "Closing text.\n"
        )
        result = flatten_tables(md, TITLE)
        assert "# Overview" in result
        assert "Intro paragraph." in result
        assert "## Notes" in result
        assert "Closing text." in result
        assert "Technology Stack: Lib is React, Ver is 18" in result

    def test_non_table_lines_order_preserved(self):
        md = "Line A\n| H |\n| - |\n| v |\nLine B\n"
        result = flatten_tables(md, TITLE)
        lines = result.split("\n")
        non_table = [ln for ln in lines if not ln.startswith("Technology Stack:") and ln]
        assert non_table == ["Line A", "Line B"]


# ---------------------------------------------------------------------------
# Header-only table (no separator, no data) (edge case)
# ---------------------------------------------------------------------------


class TestHeaderOnlyTable:
    def test_header_row_only_no_crash(self):
        """A single pipe-line with no separator or data rows should not crash."""
        md = "| Just | Headers |\nSome text after.\n"
        result = flatten_tables(md, TITLE)
        # No sentences produced, non-table text preserved
        assert "Some text after." in result
        assert "Technology Stack:" not in result
