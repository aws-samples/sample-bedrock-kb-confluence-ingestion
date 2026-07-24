"""Data models for the CKN Ingestion Pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Attachment:
    id: str
    filename: str
    media_type: str
    file_size: int
    download_url: str
    page_id: str = ""


@dataclass
class PageContent:
    page_id: str
    title: str
    space_key: str
    author: str
    last_modified: str
    url: str
    markdown: str
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class Classification:
    doc_type: str  # runbook | architecture | postmortem | contact | reference
    service: str
    severity_relevance: str  # sev1 | sev2 | all
    owner_team: str
    region: str
    summary: str


@dataclass
class MetadataSidecar:
    """Bedrock KB flat key-value metadata."""

    metadata_attributes: dict[str, str]


VALID_DOC_TYPES = {"runbook", "architecture", "postmortem", "contact", "reference"}
VALID_SEVERITY = {"sev1", "sev2", "all"}

# Free-text classification fields that need normalization for reliable
# structured filtering (F6). The classifier infers the correct team/service but
# emits inconsistent casing/spacing/separators ("System Operations" vs.
# "system operations", "network_security" vs. "Network Security"), which breaks
# equality filters at query time. Normalizing to a single canonical form
# (lowercase, hyphen-separated) makes those filters reliable.
_NORMALIZED_FIELDS = ("owner_team", "service")
_NORMALIZE_SEP_RE = re.compile(r"[\s_]+")
_NORMALIZE_STRIP_RE = re.compile(r"[^a-z0-9-]")
_NORMALIZE_COLLAPSE_RE = re.compile(r"-+")


def normalize_vocab(value: str) -> str:
    """Normalize a free-text classification value to a canonical filter key.

    Lowercase, collapse runs of whitespace/underscores to a single hyphen, drop
    any remaining non-``[a-z0-9-]`` characters, collapse repeated hyphens, and
    trim leading/trailing hyphens. Empty or whitespace-only input normalizes to
    ``"unknown"`` (matching the classifier's own sentinel for owner_team).

    Examples:
        "System Operations" -> "system-operations"
        "network_security"  -> "network-security"
        "GTIO — Platform"   -> "gtio-platform"
        ""                  -> "unknown"
    """
    lowered = value.strip().lower()
    hyphenated = _NORMALIZE_SEP_RE.sub("-", lowered)
    cleaned = _NORMALIZE_STRIP_RE.sub("", hyphenated)
    collapsed = _NORMALIZE_COLLAPSE_RE.sub("-", cleaned).strip("-")
    return collapsed or "unknown"

FALLBACK_CLASSIFICATION = Classification(
    doc_type="reference",
    service="general",
    severity_relevance="all",
    owner_team="unknown",
    region="all",
    summary="Classification unavailable — fallback applied.",
)
