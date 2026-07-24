"""Ingestion size policy for oversized page bodies.

Under F9 Option A the pipeline owns chunking (KB data source ``chunkingStrategy:
NONE``) and ``content_splitter.split_markdown`` size-caps every chunk. That keeps
individual vectors within the embedding model's input limit, but it does *not*
help pages whose body has near-zero retrieval value at any size — e.g. the
row-by-row billing/usage table dumps that motivated F5 (1–2 MB each). Force-
chunking such a page produces dozens or hundreds of near-identical vectors that
add embedding noise and dilute retrieval without ever being a useful answer.

The policy here is deliberate and cheap: if a page body exceeds a configurable
byte threshold, index a compact placeholder (title + classification summary +
source link) instead of the full body, and emit a metric-friendly log line so
the drop is observable rather than silent (the F5 bug was that these pages
vanished from the index with only a KB-side warning nobody watched).
"""

from __future__ import annotations

from ckn_ingestion.models import Classification, PageContent

# Default per-page body cap in *bytes* (UTF-8). Chosen below the historical KB
# semantic-chunking limit (1,000,000 bytes) with headroom. Pages over this are
# summarized rather than indexed whole. Configurable via AppConfig.
DEFAULT_MAX_BODY_BYTES = 900_000


def body_byte_size(body: str) -> int:
    """Return the UTF-8 byte length of *body* (the unit the size limit is measured in)."""
    return len(body.encode("utf-8"))


def is_body_oversize(body: str, max_body_bytes: int = DEFAULT_MAX_BODY_BYTES) -> bool:
    """True if *body* exceeds *max_body_bytes* UTF-8 bytes.

    Uses a strict ``>`` so a body exactly at the threshold is still indexed whole.
    """
    return body_byte_size(body) > max_body_bytes


def build_oversize_placeholder(page: PageContent, classification: Classification) -> str:
    """Build the placeholder chunk indexed in place of an oversized page body.

    Preserves discoverability — a responder searching for the topic finds the
    summary and a link to the source — without embedding the low-value body. The
    chunk starts with ``# {title}`` to match ``split_markdown``'s output shape.
    """
    summary = classification.summary.strip() or "No summary available."
    return (
        f"# {page.title}\n\n"
        f"{summary}\n\n"
        f"**Source:** {page.url}\n\n"
        "_Note: the full page body was omitted from the index because it exceeded "
        "the ingestion size limit (large low-signal content such as row-by-row "
        "table exports). See the source page for the complete content._"
    )
