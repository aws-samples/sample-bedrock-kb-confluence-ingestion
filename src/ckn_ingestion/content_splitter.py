"""Split markdown content into chunks at top-level heading boundaries."""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^#{1,2} ", re.MULTILINE)


def split_markdown(markdown: str, page_title: str) -> list[str]:
    """Split markdown into chunks at top-level heading boundaries.

    Args:
        markdown: Full page markdown content.
        page_title: Original Confluence page title, prefixed to each chunk.

    Returns:
        List of markdown chunks. Single-element list if no splitting needed.
        Each chunk is prefixed with ``# {page_title}\\n\\n``.
    """
    prefix = f"# {page_title}\n\n"

    # Edge cases: empty or whitespace-only input
    if not markdown or not markdown.strip():
        return [prefix + markdown]

    # Find all heading positions
    matches = list(_HEADING_RE.finditer(markdown))

    if len(matches) < 2:
        # Fewer than 2 heading-delimited sections → return as single chunk
        return [prefix + markdown]

    # Build sections by splitting at each heading boundary
    sections: list[str] = []

    # Preamble: content before the first heading
    preamble = markdown[: matches[0].start()]
    if preamble.strip():
        sections.append(preamble)

    # Each heading-delimited section
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        section = markdown[start:end]
        # Drop empty chunks: a heading with no body content after it
        first_newline = section.find("\n")
        body = section[first_newline + 1 :] if first_newline != -1 else ""
        if body.strip():
            sections.append(section)

    # If after dropping empties we have fewer than 2 sections, return single chunk
    if len(sections) < 2:
        return [prefix + markdown]

    return [prefix + section for section in sections]
