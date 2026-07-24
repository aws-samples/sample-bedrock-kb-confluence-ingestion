"""Concurrency guard — prevent overlapping ingestion runs.

The pipeline is triggered on a daily schedule, but a single crawl can take
longer than the schedule interval. Without a guard, the next scheduled task
starts while the previous one is still running, producing duplicate corpus
generations and index pollution.

This module lets a task detect whether *another* task of the same ECS task
definition family is already RUNNING, so it can exit early.

Design principle: **fail open**. A guard that wrongly trips would deadlock the
pipeline forever (nothing ever runs) — strictly worse than the duplicate-runs
bug it prevents. Every uncertainty (no metadata endpoint, API error, unknown
self identity) resolves to "proceed with this run".
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3
import requests
from botocore.config import Config as BotoConfig

logger = logging.getLogger(__name__)

# Short timeouts: the metadata endpoint is a link-local service and the ECS
# control-plane call should be quick. We never want the guard to hang the run.
_METADATA_TIMEOUT_SECONDS = 2
_ECS_BOTO_CONFIG = BotoConfig(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2})


def _read_task_metadata() -> dict[str, Any] | None:
    """Return the ECS task metadata document, or None if unavailable.

    Uses the task metadata endpoint (v4) exposed to every Fargate task via the
    ``ECS_CONTAINER_METADATA_URI_V4`` environment variable. Returns None on any
    failure (env var absent — e.g. local runs — network error, bad JSON), so
    callers fail open.
    """
    base = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if not base:
        logger.info("No ECS task metadata endpoint; skipping concurrency guard.")
        return None
    try:
        resp = requests.get(f"{base}/task", timeout=_METADATA_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning(
            "Could not read ECS task metadata (%s); skipping concurrency guard.",
            type(exc).__name__,
        )
        return None


def _parse_identity(metadata: dict[str, Any]) -> tuple[str, str, str] | None:
    """Extract (cluster, family, self_task_arn) from task metadata.

    Returns None if any field is missing, so the caller fails open. The task
    metadata document exposes ``Cluster``, ``TaskARN``, and ``Family``.
    """
    cluster = metadata.get("Cluster")
    self_task_arn = metadata.get("TaskARN")
    family = metadata.get("Family")
    if not cluster or not self_task_arn or not family:
        logger.warning(
            "ECS task metadata missing Cluster/TaskARN/Family; skipping concurrency guard."
        )
        return None
    return cluster, family, self_task_arn


def _other_running_task_exists(
    ecs_client: Any, cluster: str, family: str, self_task_arn: str
) -> bool:
    """Return True if a RUNNING task of *family* other than this one exists.

    Pure over its ``ecs_client`` argument so it can be unit-tested with a mock.
    Paginates ``list_tasks`` filtered to the family and RUNNING desired status,
    and treats any ARN other than ``self_task_arn`` as a concurrent run.
    """
    paginator = ecs_client.get_paginator("list_tasks")
    for page in paginator.paginate(
        cluster=cluster, family=family, desiredStatus="RUNNING"
    ):
        for task_arn in page.get("taskArns", []):
            if task_arn != self_task_arn:
                logger.info(
                    "Detected another RUNNING task of family '%s': %s",
                    family,
                    task_arn,
                )
                return True
    return False


def another_run_in_progress() -> bool:
    """Return True if another ingestion task is already RUNNING.

    Fail-open: returns False (proceed) on any error or when the environment
    cannot be determined (e.g. running locally, missing metadata, API failure,
    or missing IAM permission).
    """
    metadata = _read_task_metadata()
    if metadata is None:
        return False

    identity = _parse_identity(metadata)
    if identity is None:
        return False
    cluster, family, self_task_arn = identity

    try:
        ecs_client = boto3.client("ecs", config=_ECS_BOTO_CONFIG)
        return _other_running_task_exists(ecs_client, cluster, family, self_task_arn)
    except Exception as exc:
        # Fail open on any error (throttling, AccessDenied, network): a run is
        # better than a stalled pipeline.
        logger.warning(
            "Concurrency check failed (%s); proceeding with this run.",
            type(exc).__name__,
        )
        return False
