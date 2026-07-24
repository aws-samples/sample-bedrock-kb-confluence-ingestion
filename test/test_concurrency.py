"""Unit tests for the concurrency guard (F1).

The guard must (a) detect another RUNNING task of the same ECS family and
(b) fail OPEN — proceed with the run — on every form of uncertainty, because a
guard that wrongly trips would deadlock the pipeline forever.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ckn_ingestion import concurrency

SELF_ARN = "arn:aws:ecs:us-west-2:111122223333:task/ckn-ingestion/self"
OTHER_ARN = "arn:aws:ecs:us-west-2:111122223333:task/ckn-ingestion/other"
META = {"Cluster": "ckn-ingestion", "TaskARN": SELF_ARN, "Family": "ckn-ingestion"}


def _paginator_returning(*pages):
    """Build a mock ECS client whose list_tasks paginator yields *pages*."""
    client = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = iter(pages)
    client.get_paginator.return_value = paginator
    return client


# ---------------------------------------------------------------------------
# _parse_identity
# ---------------------------------------------------------------------------


class TestParseIdentity:
    def test_full_metadata(self):
        assert concurrency._parse_identity(META) == (
            "ckn-ingestion",
            "ckn-ingestion",
            SELF_ARN,
        )

    def test_missing_field_returns_none(self):
        for missing in ("Cluster", "TaskARN", "Family"):
            partial = {k: v for k, v in META.items() if k != missing}
            assert concurrency._parse_identity(partial) is None


# ---------------------------------------------------------------------------
# _other_running_task_exists
# ---------------------------------------------------------------------------


class TestOtherRunningTaskExists:
    def test_only_self_running_returns_false(self):
        client = _paginator_returning({"taskArns": [SELF_ARN]})
        assert (
            concurrency._other_running_task_exists(
                client, "ckn-ingestion", "ckn-ingestion", SELF_ARN
            )
            is False
        )

    def test_other_task_running_returns_true(self):
        client = _paginator_returning({"taskArns": [SELF_ARN, OTHER_ARN]})
        assert (
            concurrency._other_running_task_exists(
                client, "ckn-ingestion", "ckn-ingestion", SELF_ARN
            )
            is True
        )

    def test_other_task_on_later_page(self):
        client = _paginator_returning(
            {"taskArns": [SELF_ARN]}, {"taskArns": [OTHER_ARN]}
        )
        assert (
            concurrency._other_running_task_exists(
                client, "ckn-ingestion", "ckn-ingestion", SELF_ARN
            )
            is True
        )

    def test_no_tasks_returns_false(self):
        client = _paginator_returning({"taskArns": []})
        assert (
            concurrency._other_running_task_exists(
                client, "ckn-ingestion", "ckn-ingestion", SELF_ARN
            )
            is False
        )

    def test_filters_by_family_and_running_status(self):
        client = _paginator_returning({"taskArns": [SELF_ARN]})
        concurrency._other_running_task_exists(
            client, "ckn-ingestion", "ckn-ingestion", SELF_ARN
        )
        client.get_paginator.return_value.paginate.assert_called_once_with(
            cluster="ckn-ingestion", family="ckn-ingestion", desiredStatus="RUNNING"
        )


# ---------------------------------------------------------------------------
# another_run_in_progress — fail-open behavior
# ---------------------------------------------------------------------------


class TestAnotherRunInProgress:
    def test_no_metadata_env_fails_open(self, monkeypatch):
        monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
        assert concurrency.another_run_in_progress() is False

    def test_metadata_http_error_fails_open(self, monkeypatch):
        monkeypatch.setenv("ECS_CONTAINER_METADATA_URI_V4", "http://169.254.170.2/v4")
        with patch("ckn_ingestion.concurrency.requests.get", side_effect=OSError("boom")):
            assert concurrency.another_run_in_progress() is False

    def test_metadata_missing_fields_fails_open(self, monkeypatch):
        monkeypatch.setenv("ECS_CONTAINER_METADATA_URI_V4", "http://169.254.170.2/v4")
        resp = MagicMock()
        resp.json.return_value = {"Cluster": "ckn-ingestion"}  # missing TaskARN/Family
        resp.raise_for_status.return_value = None
        with patch("ckn_ingestion.concurrency.requests.get", return_value=resp):
            assert concurrency.another_run_in_progress() is False

    def test_ecs_api_error_fails_open(self, monkeypatch):
        monkeypatch.setenv("ECS_CONTAINER_METADATA_URI_V4", "http://169.254.170.2/v4")
        resp = MagicMock()
        resp.json.return_value = META
        resp.raise_for_status.return_value = None
        with (
            patch("ckn_ingestion.concurrency.requests.get", return_value=resp),
            patch("boto3.client", side_effect=RuntimeError("AccessDenied")),
        ):
            assert concurrency.another_run_in_progress() is False

    def test_detects_concurrent_run(self, monkeypatch):
        monkeypatch.setenv("ECS_CONTAINER_METADATA_URI_V4", "http://169.254.170.2/v4")
        resp = MagicMock()
        resp.json.return_value = META
        resp.raise_for_status.return_value = None
        ecs_client = _paginator_returning({"taskArns": [SELF_ARN, OTHER_ARN]})
        with (
            patch("ckn_ingestion.concurrency.requests.get", return_value=resp),
            patch("boto3.client", return_value=ecs_client),
        ):
            assert concurrency.another_run_in_progress() is True

    def test_alone_proceeds(self, monkeypatch):
        monkeypatch.setenv("ECS_CONTAINER_METADATA_URI_V4", "http://169.254.170.2/v4")
        resp = MagicMock()
        resp.json.return_value = META
        resp.raise_for_status.return_value = None
        ecs_client = _paginator_returning({"taskArns": [SELF_ARN]})
        with (
            patch("ckn_ingestion.concurrency.requests.get", return_value=resp),
            patch("boto3.client", return_value=ecs_client),
        ):
            assert concurrency.another_run_in_progress() is False
