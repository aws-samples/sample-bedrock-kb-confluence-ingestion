"""Unit tests for cli.py — argument parsing, space validation, orchestration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import ckn_ingestion.cli as cli_module

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(spaces: list[str] | None = None, kb_last_synced: str | None = None) -> MagicMock:
    """Return a mock AppConfig."""
    config = MagicMock()
    config.confluence.spaces = spaces or ["SPACE1", "SPACE2"]
    config.confluence.kms_secret_id = "arn:aws:secretsmanager:us-east-1:123:secret:test"
    config.kb_last_synced = kb_last_synced
    return config


def _make_page(
    page_id: str = "p1", title: str = "Test Page", space_key: str = "SPACE1"
) -> MagicMock:
    """Return a mock PageContent with no attachments."""
    page = MagicMock()
    page.page_id = page_id
    page.title = title
    page.space_key = space_key
    page.markdown = "# Test"
    page.attachments = []
    return page


def _make_classification() -> MagicMock:
    cls = MagicMock()
    cls.doc_type = "runbook"
    cls.service = "ec2"
    cls.severity_relevance = "sev1"
    return cls


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class TestArgParsing:
    def test_defaults(self):
        from ckn_ingestion.cli import _parse_args

        args = _parse_args([])
        assert args.dry_run is False
        assert args.space is None
        assert args.config == "./client.json"

    def test_dry_run_flag(self):
        from ckn_ingestion.cli import _parse_args

        args = _parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_space_argument(self):
        from ckn_ingestion.cli import _parse_args

        args = _parse_args(["--space", "MYSPACE"])
        assert args.space == "MYSPACE"

    def test_config_argument(self):
        from ckn_ingestion.cli import _parse_args

        args = _parse_args(["--config", "/tmp/custom.json"])
        assert args.config == "/tmp/custom.json"

    def test_all_flags_combined(self):
        from ckn_ingestion.cli import _parse_args

        args = _parse_args(["--dry-run", "--space", "SPACE1", "--config", "/etc/client.json"])
        assert args.dry_run is True
        assert args.space == "SPACE1"
        assert args.config == "/etc/client.json"


# ---------------------------------------------------------------------------
# Space validation
# ---------------------------------------------------------------------------


class TestSpaceValidation:
    def _run_main_with_mocks(self, argv: list[str], config: MagicMock, pages=None):
        """Run main() with all external dependencies mocked."""
        pages = pages or []
        with (
            patch("ckn_ingestion.cli.load_config", return_value=config),
            patch("ckn_ingestion.cli.update_last_synced"),
            patch("ckn_ingestion.cli.get_confluence_token", return_value="user:token"),
            patch("ckn_ingestion.cli.extract_pages", return_value=iter(pages)),
            patch("ckn_ingestion.cli.process_page_images", return_value="# enriched"),
            patch("ckn_ingestion.cli.classify_page", return_value=_make_classification()),
            patch("ckn_ingestion.cli.enrich_metadata", return_value=MagicMock()),
            patch("ckn_ingestion.cli.upload_page"),
            patch("boto3.client", return_value=MagicMock()),
        ):
            cli_module.main(argv)

    def test_valid_space_does_not_exit(self):
        config = _make_config(spaces=["SPACE1", "SPACE2"])
        # Should not raise SystemExit
        self._run_main_with_mocks(["--space", "SPACE1"], config)

    def test_invalid_space_exits_nonzero(self):
        config = _make_config(spaces=["SPACE1", "SPACE2"])
        with (
            patch("ckn_ingestion.cli.load_config", return_value=config),
            patch("boto3.client", return_value=MagicMock()),
        ):
            with pytest.raises(SystemExit) as exc_info:
                cli_module.main(["--space", "NONEXISTENT"])
            assert exc_info.value.code != 0

    def test_no_space_filter_processes_all_spaces(self):
        config = _make_config(spaces=["SPACE1", "SPACE2"])
        extract_mock = MagicMock(return_value=iter([]))

        with (
            patch("ckn_ingestion.cli.load_config", return_value=config),
            patch("ckn_ingestion.cli.update_last_synced"),
            patch("ckn_ingestion.cli.get_confluence_token", return_value="user:token"),
            patch("ckn_ingestion.cli.extract_pages", extract_mock),
            patch("ckn_ingestion.cli.process_page_images", return_value="# enriched"),
            patch("ckn_ingestion.cli.classify_page", return_value=_make_classification()),
            patch("ckn_ingestion.cli.enrich_metadata", return_value=MagicMock()),
            patch("ckn_ingestion.cli.upload_page"),
            patch("boto3.client", return_value=MagicMock()),
        ):
            cli_module.main([])

        # extract_pages called once per space
        assert extract_mock.call_count == 2
        called_space_filters = [c.kwargs.get("space_filter") for c in extract_mock.call_args_list]
        assert "SPACE1" in called_space_filters
        assert "SPACE2" in called_space_filters


# ---------------------------------------------------------------------------
# Dry-run behaviour
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_skips_upload(self):
        config = _make_config(spaces=["SPACE1"])
        page = _make_page()
        upload_mock = MagicMock()
        update_mock = MagicMock()

        with (
            patch("ckn_ingestion.cli.load_config", return_value=config),
            patch("ckn_ingestion.cli.update_last_synced", update_mock),
            patch("ckn_ingestion.cli.get_confluence_token", return_value="user:token"),
            patch("ckn_ingestion.cli.extract_pages", return_value=iter([page])),
            patch("ckn_ingestion.cli.process_page_images", return_value="# enriched"),
            patch("ckn_ingestion.cli.classify_page", return_value=_make_classification()),
            patch("ckn_ingestion.cli.enrich_metadata", return_value=MagicMock()),
            patch("ckn_ingestion.cli.upload_page", upload_mock),
            patch("boto3.client", return_value=MagicMock()),
        ):
            cli_module.main(["--dry-run"])

        upload_mock.assert_not_called()

    def test_dry_run_skips_kb_last_synced_update(self):
        config = _make_config(spaces=["SPACE1"])
        update_mock = MagicMock()

        with (
            patch("ckn_ingestion.cli.load_config", return_value=config),
            patch("ckn_ingestion.cli.update_last_synced", update_mock),
            patch("ckn_ingestion.cli.get_confluence_token", return_value="user:token"),
            patch("ckn_ingestion.cli.extract_pages", return_value=iter([])),
            patch("boto3.client", return_value=MagicMock()),
        ):
            cli_module.main(["--dry-run"])

        update_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Successful run updates kb_last_synced
# ---------------------------------------------------------------------------


class TestSuccessfulRun:
    def test_successful_run_updates_kb_last_synced(self):
        config = _make_config(spaces=["SPACE1"])
        page = _make_page()
        update_mock = MagicMock()

        with (
            patch("ckn_ingestion.cli.load_config", return_value=config),
            patch("ckn_ingestion.cli.update_last_synced", update_mock),
            patch("ckn_ingestion.cli.get_confluence_token", return_value="user:token"),
            patch("ckn_ingestion.cli.extract_pages", return_value=iter([page])),
            patch("ckn_ingestion.cli.process_page_images", return_value="# enriched"),
            patch("ckn_ingestion.cli.classify_page", return_value=_make_classification()),
            patch("ckn_ingestion.cli.enrich_metadata", return_value=MagicMock()),
            patch("ckn_ingestion.cli.upload_page"),
            patch("boto3.client", return_value=MagicMock()),
        ):
            cli_module.main([])

        update_mock.assert_called_once()
        # Verify the timestamp argument ends with 'Z' (ISO 8601 UTC)
        _, timestamp_arg = update_mock.call_args.args
        assert timestamp_arg.endswith("Z")

    def test_no_pages_still_updates_kb_last_synced(self):
        config = _make_config(spaces=["SPACE1"])
        update_mock = MagicMock()

        with (
            patch("ckn_ingestion.cli.load_config", return_value=config),
            patch("ckn_ingestion.cli.update_last_synced", update_mock),
            patch("ckn_ingestion.cli.get_confluence_token", return_value="user:token"),
            patch("ckn_ingestion.cli.extract_pages", return_value=iter([])),
            patch("boto3.client", return_value=MagicMock()),
        ):
            cli_module.main([])

        update_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Upload failure prevents kb_last_synced update
# ---------------------------------------------------------------------------


class TestUploadFailure:
    def test_upload_failure_prevents_kb_last_synced_update(self):
        config = _make_config(spaces=["SPACE1"])
        page = _make_page()
        update_mock = MagicMock()

        with (
            patch("ckn_ingestion.cli.load_config", return_value=config),
            patch("ckn_ingestion.cli.update_last_synced", update_mock),
            patch("ckn_ingestion.cli.get_confluence_token", return_value="user:token"),
            patch("ckn_ingestion.cli.extract_pages", return_value=iter([page])),
            patch("ckn_ingestion.cli.process_page_images", return_value="# enriched"),
            patch("ckn_ingestion.cli.classify_page", return_value=_make_classification()),
            patch("ckn_ingestion.cli.enrich_metadata", return_value=MagicMock()),
            patch("ckn_ingestion.cli.upload_page", side_effect=RuntimeError("S3 error")),
            patch("boto3.client", return_value=MagicMock()),
        ):
            cli_module.main([])

        update_mock.assert_not_called()

    def test_partial_upload_failure_prevents_kb_last_synced_update(self):
        """Even if only one page fails to upload, kb_last_synced must not be updated."""
        config = _make_config(spaces=["SPACE1"])
        page1 = _make_page(page_id="p1")
        page2 = _make_page(page_id="p2")

        upload_calls = []

        def _upload_side_effect(*args, **kwargs):
            # Fail on second call
            upload_calls.append(args)
            if len(upload_calls) == 2:
                raise RuntimeError("S3 error on second page")

        update_mock = MagicMock()

        with (
            patch("ckn_ingestion.cli.load_config", return_value=config),
            patch("ckn_ingestion.cli.update_last_synced", update_mock),
            patch("ckn_ingestion.cli.get_confluence_token", return_value="user:token"),
            patch("ckn_ingestion.cli.extract_pages", return_value=iter([page1, page2])),
            patch("ckn_ingestion.cli.process_page_images", return_value="# enriched"),
            patch("ckn_ingestion.cli.classify_page", return_value=_make_classification()),
            patch("ckn_ingestion.cli.enrich_metadata", return_value=MagicMock()),
            patch("ckn_ingestion.cli.upload_page", side_effect=_upload_side_effect),
            patch("boto3.client", return_value=MagicMock()),
        ):
            cli_module.main([])

        update_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Run-completion marker — F4 operational alerting heartbeat
# ---------------------------------------------------------------------------


class TestRunCompletionMarker:
    """The end-of-run marker `INGESTION_RUN_COMPLETE` drives the CloudWatch
    absence-of-success (heartbeat) alarm. It must be emitted on a clean run and
    suppressed on dry runs and on runs with upload failures.
    """

    _MARKER = "INGESTION_RUN_COMPLETE"

    def _run_and_capture(self, argv, *, upload_side_effect=None, caplog):
        config = _make_config(spaces=["SPACE1"])
        page = _make_page()
        with (
            patch("ckn_ingestion.cli.load_config", return_value=config),
            patch("ckn_ingestion.cli.update_last_synced"),
            patch("ckn_ingestion.cli.get_confluence_token", return_value="user:token"),
            patch("ckn_ingestion.cli.extract_pages", return_value=iter([page])),
            patch("ckn_ingestion.cli.process_page_images", return_value="# enriched"),
            patch("ckn_ingestion.cli.classify_page", return_value=_make_classification()),
            patch("ckn_ingestion.cli.enrich_metadata", return_value=MagicMock()),
            patch("ckn_ingestion.cli.upload_page", side_effect=upload_side_effect),
            patch("boto3.client", return_value=MagicMock()),
            caplog.at_level("INFO", logger="ckn_ingestion.cli"),
        ):
            cli_module.main(argv)
        return caplog.text

    def test_marker_emitted_on_clean_run(self, caplog):
        text = self._run_and_capture([], caplog=caplog)
        assert self._MARKER in text

    def test_marker_suppressed_on_dry_run(self, caplog):
        text = self._run_and_capture(["--dry-run"], caplog=caplog)
        assert self._MARKER not in text

    def test_marker_suppressed_on_upload_failure(self, caplog):
        text = self._run_and_capture(
            [], upload_side_effect=RuntimeError("S3 error"), caplog=caplog
        )
        assert self._MARKER not in text


# ---------------------------------------------------------------------------
# Flatten / Split integration in the pipeline
# ---------------------------------------------------------------------------


class TestFlattenSplitIntegration:
    """Verify flatten_tables and split_markdown are wired correctly in the
    orchestration loop, including fallback behaviour on errors.

    Requirements: 1.1, 2.1
    """

    def _run_pipeline(
        self,
        *,
        flatten_return="# flattened",
        flatten_side_effect=None,
        split_return=None,
        split_side_effect=None,
    ):
        """Run main() for a single page with controllable flatten/split mocks.

        Returns a dict of the key mocks so callers can assert on them.
        """
        if split_return is None:
            split_return = ["chunk-0", "chunk-1"]

        config = _make_config(spaces=["SPACE1"])
        page = _make_page()
        classification = _make_classification()

        flatten_mock = MagicMock(return_value=flatten_return, side_effect=flatten_side_effect)
        split_mock = MagicMock(return_value=split_return, side_effect=split_side_effect)
        upload_mock = MagicMock()
        classify_mock = MagicMock(return_value=classification)
        enrich_mock = MagicMock(return_value=MagicMock())

        with (
            patch("ckn_ingestion.cli.load_config", return_value=config),
            patch("ckn_ingestion.cli.update_last_synced"),
            patch("ckn_ingestion.cli.get_confluence_token", return_value="user:token"),
            patch("ckn_ingestion.cli.extract_pages", return_value=iter([page])),
            patch("ckn_ingestion.cli.process_page_images", return_value="# enriched"),
            patch("ckn_ingestion.cli.classify_page", classify_mock),
            patch("ckn_ingestion.cli.flatten_tables", flatten_mock),
            patch("ckn_ingestion.cli.split_markdown", split_mock),
            patch("ckn_ingestion.cli.enrich_metadata", enrich_mock),
            patch("ckn_ingestion.cli.upload_page", upload_mock),
            patch("boto3.client", return_value=MagicMock()),
        ):
            cli_module.main([])

        return {
            "flatten": flatten_mock,
            "split": split_mock,
            "upload": upload_mock,
            "classify": classify_mock,
            "enrich": enrich_mock,
            "page": page,
        }

    # 1. flatten_tables receives enriched markdown and page title
    def test_flatten_tables_called_with_enriched_markdown_and_title(self):
        mocks = self._run_pipeline()
        mocks["flatten"].assert_called_once_with("# enriched", "Test Page")

    # 2. split_markdown receives the output of flatten_tables and page title
    def test_split_markdown_called_with_flattened_output_and_title(self):
        mocks = self._run_pipeline(flatten_return="# flattened-output")
        mocks["split"].assert_called_once_with("# flattened-output", "Test Page")

    # 3. upload_page receives the chunks list from split_markdown
    def test_upload_receives_chunks_from_split_markdown(self):
        chunks = ["sec-A", "sec-B", "sec-C"]
        mocks = self._run_pipeline(split_return=chunks)
        # upload_page positional args: s3_client, account_id, space_key, page_id, chunks, sidecar
        upload_call_args = mocks["upload"].call_args
        assert upload_call_args.args[4] == chunks

    # 4. flatten_tables failure → fallback to [enriched_markdown]
    def test_flatten_failure_falls_back_to_single_chunk(self):
        mocks = self._run_pipeline(flatten_side_effect=RuntimeError("bad table"))
        upload_call_args = mocks["upload"].call_args
        assert upload_call_args.args[4] == ["# enriched"]

    # 5. split_markdown failure → fallback to [enriched_markdown]
    def test_split_failure_falls_back_to_single_chunk(self):
        mocks = self._run_pipeline(split_side_effect=RuntimeError("bad split"))
        upload_call_args = mocks["upload"].call_args
        assert upload_call_args.args[4] == ["# enriched"]

    # 6. classify_page is called before flatten_tables
    def test_pipeline_order_classify_before_flatten(self):
        call_order: list[str] = []

        def _track_classify(*args, **kwargs):
            call_order.append("classify")
            return _make_classification()

        def _track_flatten(*args, **kwargs):
            call_order.append("flatten")
            return "# flattened"

        config = _make_config(spaces=["SPACE1"])
        page = _make_page()

        with (
            patch("ckn_ingestion.cli.load_config", return_value=config),
            patch("ckn_ingestion.cli.update_last_synced"),
            patch("ckn_ingestion.cli.get_confluence_token", return_value="user:token"),
            patch("ckn_ingestion.cli.extract_pages", return_value=iter([page])),
            patch("ckn_ingestion.cli.process_page_images", return_value="# enriched"),
            patch("ckn_ingestion.cli.classify_page", side_effect=_track_classify),
            patch("ckn_ingestion.cli.flatten_tables", side_effect=_track_flatten),
            patch("ckn_ingestion.cli.split_markdown", return_value=["chunk"]),
            patch("ckn_ingestion.cli.enrich_metadata", return_value=MagicMock()),
            patch("ckn_ingestion.cli.upload_page"),
            patch("boto3.client", return_value=MagicMock()),
        ):
            cli_module.main([])

        assert call_order == ["classify", "flatten"]


# ---------------------------------------------------------------------------
# PII masking — F8 regression: page IDs must survive log scrubbing
# ---------------------------------------------------------------------------


class TestPIIMasking:
    """Validate the log PII scrubber.

    F8: 9-digit Confluence page IDs were matched by the SSN pattern and
    clobbered to [REDACTED_SSN] in every log line, destroying the ability to
    correlate a warning/error to its page. The SSN pattern now requires
    explicit separators, so bare digit runs (page IDs) survive.
    """

    @staticmethod
    def _scrub(msg: str) -> str:
        for pattern, replacement in cli_module._PII_PATTERNS:
            msg = pattern.sub(replacement, msg)
        return msg

    @pytest.mark.parametrize("page_id", ["66024", "163934", "12345678", "123456789"])
    def test_page_ids_survive_scrubbing(self, page_id: str):
        """Bare digit runs (Confluence page IDs, up to 9 digits) are not redacted."""
        out = self._scrub(f"attachment 404 on page {page_id}")
        assert page_id in out
        assert "[REDACTED_SSN]" not in out

    @pytest.mark.parametrize("ssn", ["123-45-6789", "123 45 6789", "123.45.6789"])
    def test_separator_formatted_ssns_still_redacted(self, ssn: str):
        """Canonically-formatted SSNs (with separators) are still redacted."""
        out = self._scrub(f"user ssn {ssn} leaked")
        assert "[REDACTED_SSN]" in out
        assert ssn not in out

    def test_account_id_still_redacted(self):
        """12-digit account IDs remain redacted (unaffected by the SSN change)."""
        out = self._scrub("account 123456789012 accessed")
        assert "[REDACTED_ACCOUNT_ID]" in out
        assert "123456789012" not in out

    def test_email_still_redacted(self):
        """Emails remain redacted (unaffected by the SSN change)."""
        out = self._scrub("contact user@example.com for details")
        assert "[REDACTED_EMAIL]" in out
        assert "user@example.com" not in out

    def test_filter_scrubs_log_record(self):
        """The _PIIMaskingFilter rewrites a record's message in place."""
        import logging

        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname=__file__, lineno=1,
            msg="attachment 404 on page %s (ssn 123-45-6789)", args=("163934",),
            exc_info=None,
        )
        assert cli_module._PIIMaskingFilter().filter(record) is True
        assert "163934" in record.msg
        assert "[REDACTED_SSN]" in record.msg
