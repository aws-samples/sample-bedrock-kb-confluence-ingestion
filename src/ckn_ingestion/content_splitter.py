"""Split markdown content into chunks at top-level heading boundaries.

When the pipeline owns chunking (Bedrock KB data source configured with
``chunkingStrategy: NONE``), each emitted chunk becomes exactly one vector, so
the splitter must also cap chunk size — a large section or a heading-less page
would otherwise produce a single chunk exceeding the embedding model's input
limit. Heading boundaries (H1/H2) are the primary split; any resulting chunk
over ``max_chunk_chars`` is split further on paragraph (blank-line) boundaries.
"""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^#{1,2} ", re.MULTILINE)
# Capturing group keeps the blank-line delimiter so the body can be reassembled
# byte-for-byte from the split pieces (content-preservation invariant).
_PARAGRAPH_SPLIT_RE = re.compile(r"(\n[ \t]*\n)")

# Default per-chunk character cap. Titan Text Embeddings v2 accepts ~8k tokens;
# at a conservative ~4 chars/token this leaves generous headroom (and room for
# the title prefix) while keeping chunks retrieval-sized. Tunable per call.
DEFAULT_MAX_CHUNK_CHARS = 20_000


def _split_oversize_body(body: str, budget: int) -> list[str]:
    """Split *body* into pieces no larger than *budget* chars, on paragraph
    boundaries where possible.

    Content-preserving: ``"".join(_split_oversize_body(body, budget)) == body``
    for any input. Paragraphs (and their trailing blank-line delimiters) are
    accumulated greedily up to *budget*. A token larger than *budget* on its own
    is hard-split at *budget*-char slices as a last resort, so no piece ever
    exceeds the cap.
    """
    if len(body) <= budget:
        return [body]

    # Guard against a degenerate budget so the hard-split loop always advances.
    budget = max(budget, 1)

    # With the capturing group, split yields alternating [para, delim, para, ...];
    # concatenating all tokens reproduces `body` exactly.
    tokens = [t for t in _PARAGRAPH_SPLIT_RE.split(body) if t]
    pieces: list[str] = []
    current = ""

    def _flush() -> None:
        nonlocal current
        if current:
            pieces.append(current)
            current = ""

    for token in tokens:
        if len(current) + len(token) <= budget:
            current += token
            continue
        # token doesn't fit onto current — flush and start fresh with it.
        _flush()
        if len(token) <= budget:
            current = token
        else:
            # Single token larger than the whole budget: hard-split it.
            for i in range(0, len(token), budget):
                slice_ = token[i : i + budget]
                if len(slice_) == budget:
                    pieces.append(slice_)
                else:
                    # Trailing remainder < budget: carry as current so a
                    # following small token can still coalesce onto it.
                    current = slice_
    _flush()

    return pieces or [body]


def split_markdown(
    markdown: str,
    page_title: str,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
) -> list[str]:
    """Split markdown into chunks at top-level heading boundaries, then cap size.

    Args:
        markdown: Full page markdown content.
        page_title: Original Confluence page title, prefixed to each chunk.
        max_chunk_chars: Maximum characters per chunk *including* the title
            prefix. Chunks larger than this are split further on paragraph
            boundaries. Must be positive.

    Returns:
        List of markdown chunks. Single-element list if no splitting needed.
        Each chunk is prefixed with ``# {page_title}\\n\\n``.
    """
    prefix = f"# {page_title}\n\n"
    # Budget for the body portion of each chunk (prefix always occupies space).
    body_budget = max(max_chunk_chars - len(prefix), 1)

    def _finalize(sections: list[str]) -> list[str]:
        """Apply the size cap to each section body and attach the prefix."""
        chunks: list[str] = []
        for section in sections:
            for piece in _split_oversize_body(section, body_budget):
                chunks.append(prefix + piece)
        return chunks

    # Edge cases: empty or whitespace-only input → one logical section, still
    # routed through _finalize so the size cap applies (a pathological, very
    # long whitespace run must not produce an uncapped chunk).
    if not markdown or not markdown.strip():
        return _finalize([markdown])

    # Find all heading positions
    matches = list(_HEADING_RE.finditer(markdown))

    if len(matches) < 2:
        # Fewer than 2 heading-delimited sections → single logical section,
        # but still size-capped so a huge heading-less page cannot become one
        # oversized chunk.
        return _finalize([markdown])

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

    # If after dropping empties we have fewer than 2 sections, treat the whole
    # page as one logical section (still size-capped).
    if len(sections) < 2:
        return _finalize([markdown])

    return _finalize(sections)
