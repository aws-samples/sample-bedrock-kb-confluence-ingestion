"""Data models for the CKN Ingestion Pipeline."""

from __future__ import annotations

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

FALLBACK_CLASSIFICATION = Classification(
    doc_type="reference",
    service="general",
    severity_relevance="all",
    owner_team="unknown",
    region="all",
    summary="Classification unavailable — fallback applied.",
)
