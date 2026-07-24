"""CLI entry point and orchestration loop for the CKN Ingestion Pipeline."""

from __future__ import annotations

import argparse
import logging
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from pythonjsonlogger import jsonlogger

from ckn_ingestion.bedrock_classifier import classify_page
from ckn_ingestion.config import load_config, update_last_synced
from ckn_ingestion.confluence_extractor import extract_pages, get_confluence_token
from ckn_ingestion.content_splitter import split_markdown
from ckn_ingestion.image_processor import PROCESSABLE_MEDIA_TYPES, process_page_images
from ckn_ingestion.metadata_enricher import enrich_metadata
from ckn_ingestion.s3_uploader import upload_page
from ckn_ingestion.table_flattener import flatten_tables

logger = logging.getLogger(__name__)

# --- Correlation ID context variable ---
correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

MAX_CONSECUTIVE_FAILURES: int = 10

# --- Boto3 client timeout configuration ---
_BOTO_CONFIG = BotoConfig(
    connect_timeout=10,
    read_timeout=65,  # Bedrock Vision API needs 10-15s per image
    retries={"max_attempts": 2},
)

# --- PII patterns to redact from log output ---
# NOTE: The SSN pattern requires explicit separators (dash/dot/space) between
# the 3-2-4 digit groups. A bare 9-digit run is NOT treated as an SSN because
# Confluence page IDs are 9-digit integers and were previously clobbered to
# [REDACTED_SSN] in every log line that referenced a page (breaking the ability
# to correlate a log message to its page). Real SSNs in free text are written
# with separators; the separator requirement removes the page-ID false positive
# while still redacting canonically-formatted SSNs.
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b\d{3}[-.\s]\d{2}[-.\s]\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b\d{12}\b"), "[REDACTED_ACCOUNT_ID]"),
]


class _PIIMaskingFilter(logging.Filter):
    """Scrub PII patterns from log messages before they are emitted."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pattern, replacement in _PII_PATTERNS:
            msg = pattern.sub(replacement, msg)
        record.msg = msg
        record.args = None  # already formatted
        return True


class _CorrelationIdFilter(logging.Filter):
    """Inject the current correlation ID into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id.get()  # type: ignore[attr-defined]
        return True


class _JsonFormatter(jsonlogger.JsonFormatter):
    """Structured JSON formatter with correlation_id injected."""

    def add_fields(
        self,
        log_record: dict,  # type: ignore[type-arg]
        record: logging.LogRecord,
        message_dict: dict,  # type: ignore[type-arg]
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["correlation_id"] = getattr(record, "correlation_id", "-")


class SpaceCircuitBreaker:
    """Track consecutive page-processing failures per space.

    Trips (halts processing) after MAX_CONSECUTIVE_FAILURES consecutive
    failures for a given space key.  A single success resets the counter.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def record_failure(self, space_key: str) -> bool:
        """Increment the failure counter and return True if the breaker has tripped."""
        self._counts[space_key] = self._counts.get(space_key, 0) + 1
        return self._counts[space_key] >= MAX_CONSECUTIVE_FAILURES

    def record_success(self, space_key: str) -> None:
        """Reset the consecutive failure counter for *space_key*."""
        self._counts[space_key] = 0

    def is_tripped(self, space_key: str) -> bool:
        """Return True if the breaker is tripped for *space_key*."""
        return self._counts.get(space_key, 0) >= MAX_CONSECUTIVE_FAILURES


def _configure_logging() -> None:
    """Configure structured JSON logging with PII masking and correlation IDs."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(_PIIMaskingFilter())
    handler.addFilter(_CorrelationIdFilter())

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CKN Ingestion Pipeline — extract Confluence pages and upload to S3."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip S3 uploads and kb_last_synced update; log results to stderr.",
    )
    parser.add_argument(
        "--space",
        metavar="SPACE_KEY",
        default=None,
        help="Restrict ingestion to a single Confluence space key.",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default="./client.json",
        help="Path to client.json config file (default: ./client.json).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point.

    Parses --dry-run, --space, and --config flags, then orchestrates:
    extract → process images → classify → enrich → upload.
    Updates kb_last_synced on success (unless --dry-run).
    """
    # 1. Parse args
    args = _parse_args(argv)

    # 2. Configure logging (stderr only, no page content or secrets)
    _configure_logging()

    # 2b. Set correlation ID for this invocation
    run_id = str(uuid.uuid4())
    correlation_id.set(run_id)
    logger.info("Starting ingestion run")

    try:
        _run(args)
    except SystemExit:
        raise
    except Exception as exc:
        # Centralized error handler: log full details server-side,
        # expose only a generic message to callers / stdout.
        logger.exception("Unhandled error during ingestion: %s", type(exc).__name__)
        sys.exit(1)


def _run(args: argparse.Namespace) -> None:
    """Inner orchestration loop — separated for centralized error handling."""

    # 3. Load config (with payload size guard)
    config_path = Path(args.config)
    config_size = config_path.stat().st_size
    if config_size > 1 * 1024 * 1024:  # 1 MB
        logger.error("Config file too large (%d bytes); max 1 MB.", config_size)
        sys.exit(1)

    try:
        config = load_config(config_path)
    except Exception as exc:
        logger.error("Failed to load config from '%s': %s", config_path, type(exc).__name__)
        sys.exit(1)

    # 4. Validate --space if provided
    if args.space and args.space not in config.confluence.spaces:
        logger.error(
            "Space '%s' not found in config. Available: %s",
            args.space,
            config.confluence.spaces,
        )
        sys.exit(1)

    # 5. Get Confluence token
    try:
        token = get_confluence_token(config.confluence.kms_secret_id)
    except Exception:
        # get_confluence_token already logs the error type
        sys.exit(1)

    # 6. Create boto3 clients (with explicit timeouts)
    bedrock_client = boto3.client("bedrock-runtime", config=_BOTO_CONFIG)
    s3_client = boto3.client("s3", config=_BOTO_CONFIG)

    # 6b. Derive the AWS account ID from the active credentials. It determines
    # the S3 bucket name (ams-ckn-{account_id}), so deriving it — rather than
    # configuring it — guarantees uploads target the account we run in.
    account_id = boto3.client("sts", config=_BOTO_CONFIG).get_caller_identity()["Account"]

    # 7. Determine spaces to process
    spaces_to_process = [args.space] if args.space else config.confluence.spaces

    upload_failed = False
    breaker = SpaceCircuitBreaker()

    # 9. Process each space and page
    for space_key in spaces_to_process:
        logger.info("Processing space '%s'", space_key)

        for page in extract_pages(
            config.confluence,
            token,
            space_filter=space_key,
            since=config.kb_last_synced,
        ):
            # a. Check for processable attachments
            has_images = any(
                a.media_type in PROCESSABLE_MEDIA_TYPES or a.filename.lower().endswith(".drawio")
                for a in page.attachments
            )

            # b. Process images and get enriched markdown
            try:
                enriched_markdown = process_page_images(page, token, bedrock_client)
            except Exception as exc:
                logger.error(
                    "Image processing failed for page '%s' (%s)",
                    page.page_id,
                    type(exc).__name__,
                )
                enriched_markdown = page.markdown

            # c. Classify page (on raw markdown, before flattening)
            classification = classify_page(page.title, enriched_markdown, bedrock_client)

            # d. Flatten tables and split content into chunks
            try:
                flattened_markdown = flatten_tables(enriched_markdown, page.title)
                chunks = split_markdown(flattened_markdown, page.title)
            except Exception as exc:
                logger.error(
                    "Flatten/split failed for page '%s' (%s) — falling back to single chunk",
                    page.page_id,
                    type(exc).__name__,
                )
                chunks = [enriched_markdown]

            # e. Enrich metadata
            sidecar = enrich_metadata(page, classification, has_images)

            # f. Upload or dry-run log
            if not args.dry_run:
                try:
                    upload_page(
                        s3_client,
                        account_id,
                        page.space_key,
                        page.page_id,
                        chunks,
                        sidecar,
                        kms_key_arn=config.confluence.kms_key_arn,
                    )
                except Exception as exc:
                    upload_failed = True
                    logger.error(
                        "Upload failed for page '%s' (%s)",
                        page.page_id,
                        type(exc).__name__,
                    )
                    if breaker.record_failure(space_key):
                        logger.error(
                            "Circuit breaker tripped for space '%s' after %d consecutive failures",
                            space_key,
                            MAX_CONSECUTIVE_FAILURES,
                        )
                        break
                    continue
            else:
                # g. Dry-run: log page ID, title, classification outcome
                logger.info(
                    "[DRY-RUN] page_id=%s title=%s doc_type=%s service=%s severity=%s",
                    page.page_id,
                    page.title,
                    classification.doc_type,
                    classification.service,
                    classification.severity_relevance,
                )

            # h. Page processed successfully — reset circuit breaker
            breaker.record_success(space_key)

    # 10. Update kb_last_synced on success
    if not args.dry_run and not upload_failed:
        timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
        try:
            update_last_synced(config_path, timestamp)
            logger.info("Updated kb_last_synced to %s", timestamp)
        except Exception as exc:
            logger.error("Failed to update kb_last_synced: %s", type(exc).__name__)

    # 11. Trigger Knowledge Base sync
    if not args.dry_run and not upload_failed:
        try:
            bedrock_agent = boto3.client("bedrock-agent", region_name=config.kb_region)
            ds_list = bedrock_agent.list_data_sources(knowledgeBaseId=config.kb_id)
            ds_id = ds_list["dataSourceSummaries"][0]["dataSourceId"]
            bedrock_agent.start_ingestion_job(knowledgeBaseId=config.kb_id, dataSourceId=ds_id)
            logger.info(
                "Started KB sync for %s (data source %s)",
                config.kb_id,
                ds_id,
            )
        except Exception as exc:
            logger.error("Failed to start KB sync: %s — %s", type(exc).__name__, exc)

    # 12. Emit a stable run-completion marker. A CloudWatch metric filter keys
    # off the literal token INGESTION_RUN_COMPLETE to drive an absence-of-success
    # (heartbeat) alarm: no marker within the expected window => the pipeline
    # either did not run or did not finish cleanly. Skipped on dry runs (they do
    # not represent a real ingestion) and when uploads failed (not a clean run).
    if not args.dry_run and not upload_failed:
        logger.info("INGESTION_RUN_COMPLETE run_id=%s", correlation_id.get())
