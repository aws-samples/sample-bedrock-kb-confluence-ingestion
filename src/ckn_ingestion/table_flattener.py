"""Normalize markdown tables for retrieval-friendly indexing.

The HTML→markdown step (``markdownify``) already emits GitHub-flavored markdown
tables. The pipeline's job here is *not* to flatten every row into prose — the
old behavior prefixed every row with the page title and an ``is <col>`` pattern,
which embedded the title string hundreds of times, diluted chunk semantics, and
inflated table-heavy pages into 1–2 MB of near-duplicate text (F7; it was also
the direct amplifier behind F5's oversized pages).

Instead:

- **Small tables** are preserved as proper GFM (header row once, no per-row
  prefix) — normalized so a single well-formed separator row is always present,
  which keeps them renderable and retrievable as a unit.
- **Large tables** (more data rows than ``max_table_rows``) are replaced with a
  compact generated summary — column names, row count, and a source link — so a
  responder still discovers the table exists and where to read it, without
  embedding hundreds of low-signal rows.

The function name ``flatten_tables`` is retained for call-site compatibility.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_SEPARATOR_RE = re.compile(r"^\|\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
# Fenced code block delimiter: ``` or ~~~ (optionally indented, with an info string).
_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")

# Tables with more data rows than this are summarized instead of embedded whole.
# Chosen so ordinary reference tables pass through untouched while billing/usage
# dumps (hundreds of rows) collapse to a summary. Tunable per call.
DEFAULT_MAX_TABLE_ROWS = 50


def _parse_cells(line: str) -> list[str]:
    """Extract cell values from a markdown table row."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _render_gfm_row(cells: list[str]) -> str:
    """Render a list of cell values as a GFM table row."""
    return "| " + " | ".join(cells) + " |"


def _render_gfm_separator(width: int) -> str:
    """Render a GFM separator row for a table of *width* columns."""
    return "| " + " | ".join(["---"] * max(width, 1)) + " |"


def _summarize_table(
    page_title: str,
    source_url: str | None,
    headers: list[str],
    data_rows: list[list[str]],
) -> list[str]:
    """Build a compact summary of an oversized table (columns, row count, link).

    Emitted in place of the full rows so the table remains discoverable without
    embedding its low-signal body.
    """
    cols = ", ".join(h for h in headers if h) or "(unlabeled columns)"
    lines = [
        f"**Table** on _{page_title}_ — {len(data_rows)} rows, columns: {cols}. "
        "Full table omitted from the index (too many rows); see the source page."
    ]
    if source_url:
        lines.append(f"**Source:** {source_url}")
    return lines


def _emit_table(
    page_title: str,
    source_url: str | None,
    headers: list[str],
    data_rows: list[list[str]],
    max_table_rows: int,
) -> list[str]:
    """Render an accumulated table as GFM, or as a summary if it is too large."""
    if not headers:
        return []
    if len(data_rows) > max_table_rows:
        # Observability: a summarized table drops its rows from the index. Emit a
        # stable metric token (mirrors cli.py's PAGE_BODY_OVERSIZE) so the drop is
        # not silent — F5's core complaint was silent content loss.
        logger.warning(
            "TABLE_SUMMARIZED page_title=%s rows=%d limit=%d — full table omitted, "
            "summary indexed",
            page_title,
            len(data_rows),
            max_table_rows,
        )
        return _summarize_table(page_title, source_url, headers, data_rows)

    # Separator width tracks the widest row (header or a malformed over-wide data
    # row) so no data cell overflows a too-narrow separator and renders broken.
    width = max([len(headers)] + [len(row) for row in data_rows])
    out = [_render_gfm_row(headers), _render_gfm_separator(width)]
    for row in data_rows:
        out.append(_render_gfm_row(row))
    return out


def flatten_tables(
    markdown: str,
    page_title: str,
    source_url: str | None = None,
    max_table_rows: int = DEFAULT_MAX_TABLE_ROWS,
) -> str:
    """Normalize markdown tables: preserve small ones as GFM, summarize large ones.

    Args:
        markdown: Markdown content potentially containing tables.
        page_title: Page title, used in the summary of oversized tables.
        source_url: Optional page URL, linked in the summary of oversized tables.
        max_table_rows: Data-row threshold above which a table is summarized
            instead of preserved.

    Returns:
        Markdown with each table either preserved as normalized GFM (header +
        single separator + rows) or replaced by a compact summary. Non-table
        content is preserved in place, in order.
    """
    lines = markdown.split("\n")
    result: list[str] = []
    headers: list[str] | None = None
    data_rows: list[list[str]] = []
    in_code_fence = False

    def _flush_table() -> None:
        nonlocal headers, data_rows
        if headers is not None:
            result.extend(
                _emit_table(page_title, source_url, headers, data_rows, max_table_rows)
            )
        headers = None
        data_rows = []

    for line in lines:
        stripped = line.strip()

        # Track fenced code blocks so pipe-prefixed lines inside a ``` fence are
        # passed through verbatim, never parsed/reformatted as a table.
        if _FENCE_RE.match(line):
            if headers is not None:
                _flush_table()
            in_code_fence = not in_code_fence
            result.append(line)
            continue

        if in_code_fence:
            result.append(line)
            continue

        is_table_line = stripped.startswith("|")

        if is_table_line:
            if headers is None:
                # First table line → header row
                headers = _parse_cells(stripped)
            elif _SEPARATOR_RE.match(stripped):
                # Separator row → dropped; a canonical one is re-rendered on emit
                continue
            else:
                # Data row
                data_rows.append(_parse_cells(stripped))
        else:
            if headers is not None:
                _flush_table()
            result.append(line)

    # Flush any trailing table
    _flush_table()

    return "\n".join(result)
