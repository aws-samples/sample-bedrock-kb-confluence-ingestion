"""Tests for models.py — constants, dataclasses, and FALLBACK_CLASSIFICATION."""

from __future__ import annotations

from ckn_ingestion.models import (
    FALLBACK_CLASSIFICATION,
    VALID_DOC_TYPES,
    VALID_SEVERITY,
    Attachment,
    Classification,
    MetadataSidecar,
    PageContent,
)


class TestConstants:
    def test_valid_doc_types_exact_set(self):
        assert VALID_DOC_TYPES == {"runbook", "architecture", "postmortem", "contact", "reference"}

    def test_valid_severity_exact_set(self):
        assert VALID_SEVERITY == {"sev1", "sev2", "all"}


class TestFallbackClassification:
    def test_doc_type(self):
        assert FALLBACK_CLASSIFICATION.doc_type == "reference"

    def test_service(self):
        assert FALLBACK_CLASSIFICATION.service == "general"

    def test_severity_relevance(self):
        assert FALLBACK_CLASSIFICATION.severity_relevance == "all"

    def test_owner_team(self):
        assert FALLBACK_CLASSIFICATION.owner_team == "unknown"

    def test_region(self):
        assert FALLBACK_CLASSIFICATION.region == "all"

    def test_summary_is_non_empty_string(self):
        assert isinstance(FALLBACK_CLASSIFICATION.summary, str)
        assert len(FALLBACK_CLASSIFICATION.summary) > 0


class TestAttachmentDataclass:
    def test_instantiation_with_all_fields(self):
        att = Attachment(
            id="att-1",
            filename="diagram.png",
            media_type="image/png",
            file_size=2048,
            download_url="https://example.com/att-1",
        )
        assert att.id == "att-1"
        assert att.filename == "diagram.png"
        assert att.media_type == "image/png"
        assert att.file_size == 2048
        assert att.download_url == "https://example.com/att-1"


class TestPageContentDataclass:
    def test_attachments_defaults_to_empty_list(self):
        page = PageContent(
            page_id="p1",
            title="My Page",
            space_key="OPS",
            author="alice",
            last_modified="2024-01-01T00:00:00Z",
            url="https://example.com/p1",
            markdown="# Hello",
        )
        assert page.attachments == []

    def test_attachments_can_be_set(self):
        att = Attachment("a1", "file.pdf", "application/pdf", 512, "https://x.com/a1")
        page = PageContent(
            page_id="p2",
            title="Page 2",
            space_key="ENG",
            author="bob",
            last_modified="2024-02-01T00:00:00Z",
            url="https://example.com/p2",
            markdown="content",
            attachments=[att],
        )
        assert len(page.attachments) == 1
        assert page.attachments[0].id == "a1"


class TestMetadataSidecarDataclass:
    def test_holds_dict_in_metadata_attributes(self):
        attrs = {"doc_type": "runbook", "service": "payments"}
        sidecar = MetadataSidecar(metadata_attributes=attrs)
        assert sidecar.metadata_attributes == attrs
        assert isinstance(sidecar.metadata_attributes, dict)


class TestClassificationDataclass:
    def test_instantiation_with_all_six_fields(self):
        cls = Classification(
            doc_type="runbook",
            service="payments",
            severity_relevance="sev1",
            owner_team="platform",
            region="us-east-1",
            summary="Handles payment processing runbook.",
        )
        assert cls.doc_type == "runbook"
        assert cls.service == "payments"
        assert cls.severity_relevance == "sev1"
        assert cls.owner_team == "platform"
        assert cls.region == "us-east-1"
        assert cls.summary == "Handles payment processing runbook."
