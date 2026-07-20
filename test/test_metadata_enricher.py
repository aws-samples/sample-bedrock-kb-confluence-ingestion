"""Unit tests for metadata_enricher module."""

from __future__ import annotations

from unittest.mock import patch

from ckn_ingestion.metadata_enricher import enrich_metadata
from ckn_ingestion.models import Classification, MetadataSidecar, PageContent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_page(**overrides) -> PageContent:
    base = PageContent(
        page_id="12345",
        title="RDS Failover Runbook",
        space_key="OPS",
        author="jsmith",
        last_modified="2026-03-22T12:00:00Z",
        url="https://acme.atlassian.net/wiki/spaces/OPS/pages/12345",
        markdown="## Steps\n1. Do this.",
        attachments=[],
    )
    for k, v in overrides.items():
        object.__setattr__(base, k, v)
    return base


def _make_classification(**overrides) -> Classification:
    base = Classification(
        doc_type="runbook",
        service="rds",
        severity_relevance="sev1",
        owner_team="database-ops",
        region="us-east-1",
        summary="Procedure for failing over RDS instances.",
    )
    for k, v in overrides.items():
        object.__setattr__(base, k, v)
    return base


# ---------------------------------------------------------------------------
# Return type and structure
# ---------------------------------------------------------------------------


class TestEnrichMetadataReturnType:
    def test_returns_metadata_sidecar(self):
        result = enrich_metadata(_make_page(), _make_classification(), has_images=True)
        assert isinstance(result, MetadataSidecar)

    def test_metadata_attributes_is_dict(self):
        result = enrich_metadata(_make_page(), _make_classification(), has_images=False)
        assert isinstance(result.metadata_attributes, dict)

    def test_all_expected_keys_present(self):
        result = enrich_metadata(_make_page(), _make_classification(), has_images=False)
        expected_keys = {
            "doc_type",
            "service",
            "severity_relevance",
            "owner_team",
            "region",
            "summary",
            "source_url",
            "confluence_space",
            "confluence_author",
            "last_synced",
            "has_images",
            "page_title",
        }
        assert expected_keys == set(result.metadata_attributes.keys())

    def test_all_values_are_strings(self):
        result = enrich_metadata(_make_page(), _make_classification(), has_images=True)
        for key, value in result.metadata_attributes.items():
            assert isinstance(value, str), f"Value for '{key}' is not a string: {type(value)}"


# ---------------------------------------------------------------------------
# Classification fields
# ---------------------------------------------------------------------------


class TestClassificationFields:
    def test_doc_type_mapped(self):
        result = enrich_metadata(_make_page(), _make_classification(doc_type="architecture"), False)
        assert result.metadata_attributes["doc_type"] == "architecture"

    def test_service_mapped(self):
        result = enrich_metadata(_make_page(), _make_classification(service="lambda"), False)
        assert result.metadata_attributes["service"] == "lambda"

    def test_severity_relevance_mapped(self):
        result = enrich_metadata(
            _make_page(), _make_classification(severity_relevance="sev2"), False
        )
        assert result.metadata_attributes["severity_relevance"] == "sev2"

    def test_owner_team_mapped(self):
        result = enrich_metadata(
            _make_page(), _make_classification(owner_team="platform-team"), False
        )
        assert result.metadata_attributes["owner_team"] == "platform-team"

    def test_region_mapped(self):
        result = enrich_metadata(_make_page(), _make_classification(region="eu-west-1"), False)
        assert result.metadata_attributes["region"] == "eu-west-1"

    def test_summary_mapped(self):
        summary = "Detailed procedure for RDS failover."
        result = enrich_metadata(_make_page(), _make_classification(summary=summary), False)
        assert result.metadata_attributes["summary"] == summary


# ---------------------------------------------------------------------------
# Confluence metadata fields
# ---------------------------------------------------------------------------


class TestConfluenceMetadataFields:
    def test_source_url_from_page(self):
        page = _make_page(url="https://acme.atlassian.net/wiki/spaces/OPS/pages/12345")
        result = enrich_metadata(page, _make_classification(), False)
        assert (
            result.metadata_attributes["source_url"]
            == "https://acme.atlassian.net/wiki/spaces/OPS/pages/12345"
        )

    def test_confluence_space_from_page(self):
        page = _make_page(space_key="INFRA")
        result = enrich_metadata(page, _make_classification(), False)
        assert result.metadata_attributes["confluence_space"] == "INFRA"

    def test_confluence_author_from_page(self):
        page = _make_page(author="alice")
        result = enrich_metadata(page, _make_classification(), False)
        assert result.metadata_attributes["confluence_author"] == "alice"


# ---------------------------------------------------------------------------
# has_images field
# ---------------------------------------------------------------------------


class TestHasImagesField:
    def test_has_images_true_becomes_string_true(self):
        result = enrich_metadata(_make_page(), _make_classification(), has_images=True)
        assert result.metadata_attributes["has_images"] == "true"

    def test_has_images_false_becomes_string_false(self):
        result = enrich_metadata(_make_page(), _make_classification(), has_images=False)
        assert result.metadata_attributes["has_images"] == "false"

    def test_has_images_is_not_boolean(self):
        for val in (True, False):
            result = enrich_metadata(_make_page(), _make_classification(), has_images=val)
            assert isinstance(result.metadata_attributes["has_images"], str)
            assert result.metadata_attributes["has_images"] in ("true", "false")


# ---------------------------------------------------------------------------
# last_synced field
# ---------------------------------------------------------------------------


class TestLastSyncedField:
    def test_last_synced_ends_with_z(self):
        result = enrich_metadata(_make_page(), _make_classification(), False)
        assert result.metadata_attributes["last_synced"].endswith("Z")

    def test_last_synced_is_iso8601(self):
        from datetime import datetime

        result = enrich_metadata(_make_page(), _make_classification(), False)
        ts = result.metadata_attributes["last_synced"]
        # Should parse without error after stripping trailing Z
        datetime.fromisoformat(ts.rstrip("Z"))

    def test_last_synced_uses_current_utc_time(self):
        fixed_ts = "2026-03-22T14:00:00"
        with patch("ckn_ingestion.metadata_enricher.datetime") as mock_dt:
            mock_dt.now.return_value.replace.return_value.isoformat.return_value = fixed_ts
            result = enrich_metadata(_make_page(), _make_classification(), False)
        assert result.metadata_attributes["last_synced"] == fixed_ts + "Z"


# ---------------------------------------------------------------------------
# page_title field
# ---------------------------------------------------------------------------


class TestPageTitleField:
    """Validates: Requirements 3.1, 3.2, 3.3"""

    def test_page_title_present_with_correct_value(self):
        page = _make_page(title="RDS Failover Runbook")
        result = enrich_metadata(page, _make_classification(), has_images=False)
        assert result.metadata_attributes["page_title"] == "RDS Failover Runbook"

    def test_empty_title_becomes_untitled(self):
        page = _make_page(title="")
        result = enrich_metadata(page, _make_classification(), has_images=False)
        assert result.metadata_attributes["page_title"] == "untitled"

    def test_page_title_is_string(self):
        result = enrich_metadata(_make_page(), _make_classification(), has_images=False)
        assert isinstance(result.metadata_attributes["page_title"], str)

    def test_all_existing_keys_still_present_with_page_title(self):
        result = enrich_metadata(_make_page(), _make_classification(), has_images=False)
        expected_keys = {
            "doc_type",
            "service",
            "severity_relevance",
            "owner_team",
            "region",
            "summary",
            "source_url",
            "confluence_space",
            "confluence_author",
            "last_synced",
            "has_images",
            "page_title",
        }
        assert expected_keys == set(result.metadata_attributes.keys())
