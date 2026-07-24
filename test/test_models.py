"""Tests for models.py — constants, dataclasses, and FALLBACK_CLASSIFICATION."""

from __future__ import annotations

import pytest

from ckn_ingestion.models import (
    FALLBACK_CLASSIFICATION,
    VALID_DOC_TYPES,
    VALID_SEVERITY,
    Attachment,
    Classification,
    MetadataSidecar,
    PageContent,
    normalize_vocab,
)


class TestConstants:
    def test_valid_doc_types_exact_set(self):
        assert VALID_DOC_TYPES == {"runbook", "architecture", "postmortem", "contact", "reference"}

    def test_valid_severity_exact_set(self):
        assert VALID_SEVERITY == {"sev1", "sev2", "all"}


class TestNormalizeVocab:
    """F6: canonicalize free-text classification fields for reliable filtering."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # Casing / spacing / separator drift observed in the real corpus
            ("System Operations", "system-operations"),
            ("system operations", "system-operations"),
            ("network_security", "network-security"),
            ("Network Security", "network-security"),
            ("network-security", "network-security"),
            ("DevOps", "devops"),
            ("SRE", "sre"),
            ("platform-engineering", "platform-engineering"),
            ("  Data  Engineering  ", "data-engineering"),
            ("Step Functions", "step-functions"),
            # Sentinels / canonical values pass through unchanged
            ("general", "general"),
            ("unknown", "unknown"),
        ],
    )
    def test_normalization_cases(self, raw, expected):
        assert normalize_vocab(raw) == expected

    def test_idempotent(self):
        for raw in ["System Operations", "network_security", "GTIO — Platform", "sre"]:
            once = normalize_vocab(raw)
            assert normalize_vocab(once) == once

    def test_empty_and_whitespace_become_unknown(self):
        assert normalize_vocab("") == "unknown"
        assert normalize_vocab("   ") == "unknown"

    def test_output_is_a_valid_filter_key(self):
        # Every output matches ^[a-z0-9-]+$ (no spaces, no uppercase, no stray punctuation)
        import re

        for raw in ["System Operations", "GTIO — Platform", "a/b\\c", "Team (Prod)!"]:
            out = normalize_vocab(raw)
            assert re.fullmatch(r"[a-z0-9-]+", out), out
            assert not out.startswith("-") and not out.endswith("-")


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
