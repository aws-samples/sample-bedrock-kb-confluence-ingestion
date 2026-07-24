"""Unit tests for table_flattener module.

F7: tables are preserved as proper GitHub-flavored markdown (header row once,
no per-row title prefix). Tables over a row threshold are replaced by a compact
summary (columns + row count + source link) instead of embedding every row.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6
"""

from __future__ import annotations

from ckn_ingestion.table_flattener import DEFAULT_MAX_TABLE_ROWS, flatten_tables

TITLE = "Technology Stack"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines) + "\n"


def _is_sep(line: str) -> bool:
    body = line.replace("|", "").replace(" ", "").replace(":", "")
    return len(body) > 0 and set(body) == {"-"}


# ---------------------------------------------------------------------------
# Small tables are preserved as GFM (Req 2.1, 2.2)
# ---------------------------------------------------------------------------


class TestGfmPreservation:
    def test_table_kept_as_gfm_not_flattened_to_prose(self):
        md = _table(["Library", "Version"], [["React", "18.2"], ["Vue", "3.3"]])
        result = flatten_tables(md, TITLE)
        # No per-row title prefix, no "is <col>" prose (the F7 anti-pattern).
        assert "Technology Stack:" not in result
        assert " is " not in result
        # Header and both data rows survive as pipe-delimited GFM.
        assert "| Library | Version |" in result
        assert "| React | 18.2 |" in result
        assert "| Vue | 3.3 |" in result

    def test_title_not_repeated_per_row(self):
        md = _table(["Name", "Role"], [["Alice", "Eng"], ["Bob", "Mgr"], ["Carol", "Design"]])
        result = flatten_tables(md, TITLE)
        assert result.count(TITLE) == 0

    def test_exactly_one_separator_row_emitted(self):
        md = _table(["A", "B"], [["1", "2"]])
        result = flatten_tables(md, TITLE)
        sep_lines = [ln for ln in result.split("\n") if _is_sep(ln)]
        assert len(sep_lines) == 1

    def test_duplicate_separator_rows_are_normalized_to_one(self):
        md = (
            "| A | B |\n"
            "| --- | --- |\n"
            "| :-: | :-: |\n"  # a second separator-looking row
            "| 1 | 2 |\n"
        )
        result = flatten_tables(md, TITLE)
        assert "| 1 | 2 |" in result
        sep_lines = [ln for ln in result.split("\n") if _is_sep(ln)]
        assert len(sep_lines) == 1


# ---------------------------------------------------------------------------
# Large tables are summarized (Req 2.4, 2.5)
# ---------------------------------------------------------------------------


class TestLargeTableSummary:
    def test_table_over_threshold_is_summarized(self):
        rows = [[f"item{i}", str(i)] for i in range(DEFAULT_MAX_TABLE_ROWS + 5)]
        md = _table(["Item", "Cost"], rows)
        result = flatten_tables(md, TITLE, source_url="https://x.atlassian.net/wiki/p/1")
        # Individual rows are NOT embedded.
        assert "| item0 | 0 |" not in result
        # Summary mentions column names, the row count, and the source link.
        assert "Item" in result and "Cost" in result
        assert str(len(rows)) in result
        assert "https://x.atlassian.net/wiki/p/1" in result
        assert "omitted from the index" in result

    def test_table_at_threshold_is_preserved_whole(self):
        rows = [[f"item{i}", str(i)] for i in range(DEFAULT_MAX_TABLE_ROWS)]
        md = _table(["Item", "Cost"], rows)
        result = flatten_tables(md, TITLE, max_table_rows=DEFAULT_MAX_TABLE_ROWS)
        # Exactly at the threshold → kept as GFM.
        assert "| item0 | 0 |" in result
        assert "omitted from the index" not in result

    def test_summary_without_source_url_omits_link(self):
        rows = [["a", "b"] for _ in range(10)]
        md = _table(["X", "Y"], rows)
        result = flatten_tables(md, TITLE, max_table_rows=3)
        assert "**Source:**" not in result
        assert "omitted from the index" in result

    def test_custom_threshold_is_honored(self):
        md = _table(["A", "B"], [["1", "2"], ["3", "4"]])
        result = flatten_tables(md, TITLE, max_table_rows=1)
        assert "| 1 | 2 |" not in result
        assert "omitted from the index" in result


# ---------------------------------------------------------------------------
# Multiple tables (Req 2.1)
# ---------------------------------------------------------------------------


class TestMultipleTables:
    def test_each_table_handled_independently(self):
        small = _table(["A", "B"], [["1", "2"]])
        big = _table(["C", "D"], [[str(i), str(i)] for i in range(DEFAULT_MAX_TABLE_ROWS + 1)])
        md = small + "\nSome prose.\n\n" + big
        result = flatten_tables(md, TITLE, max_table_rows=DEFAULT_MAX_TABLE_ROWS)
        # Small preserved, big summarized, prose intact.
        assert "| 1 | 2 |" in result
        assert "Some prose." in result
        assert "omitted from the index" in result


# ---------------------------------------------------------------------------
# No tables / edge cases (Req 2.3, 2.6)
# ---------------------------------------------------------------------------


class TestNoTables:
    def test_no_tables_returns_unchanged(self):
        md = "# Heading\n\nJust prose, no tables here.\n\n- a list item\n"
        assert flatten_tables(md, TITLE) == md

    def test_empty_string_returns_empty(self):
        assert flatten_tables("", TITLE) == ""


class TestMixedContent:
    def test_non_table_content_preserved_in_order(self):
        md = (
            "Intro paragraph.\n"
            "\n"
            "| A | B |\n"
            "| --- | --- |\n"
            "| 1 | 2 |\n"
            "\n"
            "Closing paragraph.\n"
        )
        result = flatten_tables(md, TITLE)
        lines = result.split("\n")
        assert lines.index("Intro paragraph.") < lines.index("| 1 | 2 |")
        assert lines.index("| 1 | 2 |") < lines.index("Closing paragraph.")


class TestHeaderOnlyTable:
    def test_header_and_separator_only_no_crash(self):
        md = "| A | B |\n| --- | --- |\n"
        result = flatten_tables(md, TITLE)
        # Header preserved; no data rows.
        assert "| A | B |" in result

    def test_header_row_only_no_separator_no_crash(self):
        md = "| A | B |\n"
        result = flatten_tables(md, TITLE)
        assert "| A | B |" in result


# ---------------------------------------------------------------------------
# Code fences: pipe lines inside ``` must not be parsed as tables
# ---------------------------------------------------------------------------


class TestCodeFence:
    def test_pipe_lines_in_code_fence_preserved_verbatim(self):
        md = (
            "Intro.\n"
            "\n"
            "```\n"
            "| not | a | table |\n"
            "|-----|---|-------|\n"
            "```\n"
            "\n"
            "Outro.\n"
        )
        result = flatten_tables(md, TITLE)
        # The fenced lines must survive byte-for-byte (alignment/separator intact).
        assert "| not | a | table |" in result
        assert "|-----|---|-------|" in result
        assert "```" in result

    def test_real_table_after_fence_still_normalized(self):
        md = (
            "```\n"
            "| code | block |\n"
            "```\n"
            "\n"
            "| Real | Table |\n"
            "| --- | --- |\n"
            "| a | b |\n"
        )
        result = flatten_tables(md, TITLE)
        assert "| code | block |" in result  # fenced, preserved
        assert "| a | b |" in result  # real table row preserved


# ---------------------------------------------------------------------------
# Cell handling / width
# ---------------------------------------------------------------------------


class TestCellHandling:
    def test_empty_cells_are_retained_in_gfm(self):
        # Unlike the old prose flattener (which omitted empty cells), GFM keeps
        # the column positions so the table stays aligned.
        md = "| A | B | C |\n| --- | --- | --- |\n| 1 |  | 3 |\n"
        result = flatten_tables(md, TITLE)
        assert "| 1 |  | 3 |" in result

    def test_over_wide_data_row_gets_wide_enough_separator(self):
        # A malformed row wider than the header must not overflow the separator.
        md = "| A | B |\n| --- | --- |\n| 1 | 2 | 3 |\n"
        result = flatten_tables(md, TITLE)
        # Separator should have 3 columns (widest row), not 2.
        assert "| --- | --- | --- |" in result
        assert "| 1 | 2 | 3 |" in result


# ---------------------------------------------------------------------------
# Observability: summarized tables emit a metric token
# ---------------------------------------------------------------------------


class TestSummaryLogging:
    def test_summarized_table_logs_metric_token(self, caplog):
        import logging

        md = _table(["A", "B"], [["1", "2"], ["3", "4"], ["5", "6"]])
        with caplog.at_level(logging.WARNING, logger="ckn_ingestion.table_flattener"):
            flatten_tables(md, TITLE, max_table_rows=2)
        assert "TABLE_SUMMARIZED" in caplog.text

    def test_preserved_table_does_not_log(self, caplog):
        import logging

        md = _table(["A", "B"], [["1", "2"]])
        with caplog.at_level(logging.WARNING, logger="ckn_ingestion.table_flattener"):
            flatten_tables(md, TITLE)
        assert "TABLE_SUMMARIZED" not in caplog.text
