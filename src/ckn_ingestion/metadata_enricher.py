"""Metadata enricher — merge classification + Confluence metadata into Bedrock KB sidecar."""

from __future__ import annotations

from datetime import datetime, timezone

from ckn_ingestion.models import Classification, MetadataSidecar, PageContent


def enrich_metadata(
    page: PageContent,
    classification: Classification,
    has_images: bool,
) -> MetadataSidecar:
    """Merge classification fields and Confluence metadata into a Bedrock KB sidecar.

    All values are serialized as strings per Bedrock KB flat key-value requirement.

    Args:
        page: Confluence page content with source metadata.
        classification: Claude classification result.
        has_images: Whether the page has processable image attachments.

    Returns:
        MetadataSidecar with metadata_attributes dict matching the Bedrock KB schema.
    """
    last_synced = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"

    attributes: dict[str, str] = {
        # Classification fields
        "doc_type": classification.doc_type,
        "service": classification.service,
        "severity_relevance": classification.severity_relevance,
        "owner_team": classification.owner_team,
        "region": classification.region,
        "summary": classification.summary,
        # Confluence metadata
        "source_url": page.url,
        "confluence_space": page.space_key,
        "confluence_author": page.author,
        # Pipeline metadata
        "last_synced": last_synced,
        "has_images": "true" if has_images else "false",
        # Page context
        "page_title": page.title if page.title else "untitled",
    }

    return MetadataSidecar(metadata_attributes=attributes)
