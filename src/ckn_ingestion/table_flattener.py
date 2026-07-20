"""Convert markdown tables into natural language sentences.

Replaces each table row with a self-contained sentence that includes the page
title and column-value pairs, making table data retrievable without needing
the full table context.
"""

from __future__ import annotations

import re

_SEPARATOR_RE = re.compile(r"^\|\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def _parse_cells(line: str) -> list[str]:
    """Extract cell values from a markdown table row."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _row_to_sentence(page_title: str, headers: list[str], cells: list[str]) -> str:
    """Build a natural language sentence from a data row.

    Empty cells are omitted. Columns and cells are zipped so that malformed
    rows with fewer/more cells than headers are handled gracefully.
    """
    pairs = [f"{col} is {val}" for col, val in zip(headers, cells) if val]
    if not pairs:
        return ""
    return f"{page_title}: {', '.join(pairs)}"


def flatten_tables(markdown: str, page_title: str) -> str:
    """Convert markdown tables into natural language sentences.

    Args:
        markdown: Markdown content potentially containing tables.
        page_title: Page title used as sentence prefix.

    Returns:
        Markdown with tables replaced by newline-separated sentences.
        Non-table content is preserved in place.
    """
    lines = markdown.split("\n")
    result: list[str] = []
    headers: list[str] | None = None
    sentences: list[str] = []

    def _flush_table() -> None:
        """Emit accumulated sentences and reset table state."""
        nonlocal headers, sentences
        result.extend(sentences)
        headers = None
        sentences = []

    for line in lines:
        stripped = line.strip()
        is_table_line = stripped.startswith("|")

        if is_table_line:
            if headers is None:
                # First table line → header row
                headers = _parse_cells(stripped)
            elif _SEPARATOR_RE.match(stripped):
                # Separator row → skip
                continue
            else:
                # Data row
                cells = _parse_cells(stripped)
                sentence = _row_to_sentence(page_title, headers, cells)
                if sentence:
                    sentences.append(sentence)
        else:
            if headers is not None:
                # We just left a table block
                _flush_table()
            result.append(line)

    # Flush any trailing table
    if headers is not None:
        _flush_table()

    return "\n".join(result)
