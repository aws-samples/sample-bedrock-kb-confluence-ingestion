"""Unit tests for bedrock_classifier module."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from ckn_ingestion.bedrock_classifier import _MODEL_ID, classify_page, parse_classification
from ckn_ingestion.models import (
    FALLBACK_CLASSIFICATION,
    VALID_DOC_TYPES,
    VALID_SEVERITY,
    Classification,
    normalize_vocab,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valid_classification_dict(**overrides) -> dict:
    base = {
        "doc_type": "runbook",
        "service": "rds",
        "severity_relevance": "sev1",
        "owner_team": "platform-team",
        "region": "us-east-1",
        "summary": "Step-by-step RDS failover procedure for incident responders.",
    }
    base.update(overrides)
    return base


def _make_bedrock_client(raw_json: str) -> MagicMock:
    """Return a mock Bedrock client whose invoke_model returns *raw_json*."""
    client = MagicMock()
    client.invoke_model.return_value = {
        "body": io.BytesIO(json.dumps({"content": [{"text": raw_json}]}).encode("utf-8"))
    }
    return client


def _make_bedrock_client_from_dict(data: dict) -> MagicMock:
    return _make_bedrock_client(json.dumps(data))


# ---------------------------------------------------------------------------
# parse_classification — valid JSON
# ---------------------------------------------------------------------------


class TestParseClassificationValid:
    def test_valid_runbook(self):
        raw = json.dumps(_make_valid_classification_dict())
        result = parse_classification(raw)
        assert isinstance(result, Classification)
        assert result.doc_type == "runbook"
        assert result.service == "rds"
        assert result.severity_relevance == "sev1"
        assert result.owner_team == "platform-team"
        assert result.region == "us-east-1"
        assert "RDS failover" in result.summary

    def test_all_valid_doc_types(self):
        for doc_type in VALID_DOC_TYPES:
            raw = json.dumps(_make_valid_classification_dict(doc_type=doc_type))
            result = parse_classification(raw)
            assert result.doc_type == doc_type

    def test_all_valid_severity_values(self):
        for sev in VALID_SEVERITY:
            raw = json.dumps(_make_valid_classification_dict(severity_relevance=sev))
            result = parse_classification(raw)
            assert result.severity_relevance == sev

    def test_extra_fields_are_ignored(self):
        data = _make_valid_classification_dict()
        data["unexpected_field"] = "should be ignored"
        result = parse_classification(json.dumps(data))
        assert result.doc_type == "runbook"


class TestParseClassificationNormalization:
    """F6: parse_classification normalizes the free-text owner_team/service
    fields so equality filters at query time are reliable."""

    def test_owner_team_normalized(self):
        data = _make_valid_classification_dict(owner_team="System Operations")
        result = parse_classification(json.dumps(data))
        assert result.owner_team == "system-operations"

    def test_service_normalized(self):
        data = _make_valid_classification_dict(service="Step Functions")
        result = parse_classification(json.dumps(data))
        assert result.service == "step-functions"

    def test_underscore_variant_normalized(self):
        data = _make_valid_classification_dict(owner_team="network_security")
        result = parse_classification(json.dumps(data))
        assert result.owner_team == "network-security"

    def test_already_canonical_unchanged(self):
        data = _make_valid_classification_dict(owner_team="platform-engineering", service="rds")
        result = parse_classification(json.dumps(data))
        assert result.owner_team == "platform-engineering"
        assert result.service == "rds"

    def test_doc_type_and_region_not_hyphenated(self):
        # doc_type is a closed enum (must stay verbatim); region is left as-is.
        data = _make_valid_classification_dict(doc_type="runbook", region="us-east-1")
        result = parse_classification(json.dumps(data))
        assert result.doc_type == "runbook"
        assert result.region == "us-east-1"


# ---------------------------------------------------------------------------
# parse_classification — invalid JSON
# ---------------------------------------------------------------------------


class TestParseClassificationInvalidJson:
    def test_raises_on_malformed_json(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_classification("{not valid json}")

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError):
            parse_classification("")

    def test_raises_on_json_array(self):
        with pytest.raises(ValueError, match="Expected a JSON object"):
            parse_classification("[1, 2, 3]")

    def test_raises_on_json_string(self):
        with pytest.raises(ValueError, match="Expected a JSON object"):
            parse_classification('"just a string"')


# ---------------------------------------------------------------------------
# parse_classification — missing fields
# ---------------------------------------------------------------------------


class TestParseClassificationMissingFields:
    def test_raises_on_missing_doc_type(self):
        data = _make_valid_classification_dict()
        del data["doc_type"]
        with pytest.raises(ValueError, match="Missing required fields"):
            parse_classification(json.dumps(data))

    def test_raises_on_missing_service(self):
        data = _make_valid_classification_dict()
        del data["service"]
        with pytest.raises(ValueError, match="Missing required fields"):
            parse_classification(json.dumps(data))

    def test_raises_on_missing_severity_relevance(self):
        data = _make_valid_classification_dict()
        del data["severity_relevance"]
        with pytest.raises(ValueError, match="Missing required fields"):
            parse_classification(json.dumps(data))

    def test_raises_on_missing_owner_team(self):
        data = _make_valid_classification_dict()
        del data["owner_team"]
        with pytest.raises(ValueError, match="Missing required fields"):
            parse_classification(json.dumps(data))

    def test_raises_on_missing_region(self):
        data = _make_valid_classification_dict()
        del data["region"]
        with pytest.raises(ValueError, match="Missing required fields"):
            parse_classification(json.dumps(data))

    def test_raises_on_missing_summary(self):
        data = _make_valid_classification_dict()
        del data["summary"]
        with pytest.raises(ValueError, match="Missing required fields"):
            parse_classification(json.dumps(data))

    def test_raises_on_empty_object(self):
        with pytest.raises(ValueError, match="Missing required fields"):
            parse_classification("{}")


# ---------------------------------------------------------------------------
# parse_classification — invalid enum values
# ---------------------------------------------------------------------------


class TestParseClassificationInvalidEnums:
    def test_raises_on_invalid_doc_type(self):
        data = _make_valid_classification_dict(doc_type="unknown_type")
        with pytest.raises(ValueError, match="Invalid doc_type"):
            parse_classification(json.dumps(data))

    def test_raises_on_invalid_severity_relevance(self):
        data = _make_valid_classification_dict(severity_relevance="sev3")
        with pytest.raises(ValueError, match="Invalid severity_relevance"):
            parse_classification(json.dumps(data))

    def test_raises_on_non_string_doc_type(self):
        data = _make_valid_classification_dict(doc_type=42)
        with pytest.raises(ValueError, match="must be a string"):
            parse_classification(json.dumps(data))

    def test_raises_on_non_string_summary(self):
        data = _make_valid_classification_dict(summary=None)
        with pytest.raises(ValueError, match="must be a string"):
            parse_classification(json.dumps(data))


# ---------------------------------------------------------------------------
# classify_page — happy path
# ---------------------------------------------------------------------------


class TestClassifyPageHappyPath:
    def test_returns_classification_on_valid_response(self):
        data = _make_valid_classification_dict()
        client = _make_bedrock_client_from_dict(data)

        with patch(
            "ckn_ingestion.bedrock_classifier._call_with_throttle_retry",
            side_effect=lambda fn: fn(),
        ):
            result = classify_page("RDS Failover Procedure", "## Steps\n1. Do this.", client)

        assert isinstance(result, Classification)
        assert result.doc_type == "runbook"
        assert result.service == "rds"

    def test_invokes_bedrock_with_correct_model(self):
        data = _make_valid_classification_dict()
        client = _make_bedrock_client_from_dict(data)

        with patch(
            "ckn_ingestion.bedrock_classifier._call_with_throttle_retry",
            side_effect=lambda fn: fn(),
        ):
            classify_page("My Page", "Content here.", client)

        client.invoke_model.assert_called_once()
        call_kwargs = client.invoke_model.call_args[1]
        # Assert against the module constant so a future model bump doesn't
        # re-stale this test (the pipeline uses a cross-region inference profile).
        assert call_kwargs["modelId"] == _MODEL_ID
        assert call_kwargs["contentType"] == "application/json"
        assert call_kwargs["accept"] == "application/json"

    def test_prompt_contains_title_and_content(self):
        data = _make_valid_classification_dict()
        client = _make_bedrock_client_from_dict(data)
        captured_bodies: list[str] = []

        def capture_invoke(**kwargs):
            captured_bodies.append(kwargs["body"])
            # Reset body stream for actual call
            client.invoke_model.return_value["body"].seek(0)
            return client.invoke_model.return_value

        client.invoke_model.side_effect = capture_invoke

        with patch(
            "ckn_ingestion.bedrock_classifier._call_with_throttle_retry",
            side_effect=lambda fn: fn(),
        ):
            classify_page("My Special Title", "My special content.", client)

        assert len(captured_bodies) == 1
        body_dict = json.loads(captured_bodies[0])
        prompt_text = body_dict["messages"][0]["content"]
        assert "My Special Title" in prompt_text
        assert "My special content." in prompt_text

    def test_max_tokens_is_1024(self):
        data = _make_valid_classification_dict()
        client = _make_bedrock_client_from_dict(data)
        captured_bodies: list[str] = []

        def capture_invoke(**kwargs):
            captured_bodies.append(kwargs["body"])
            client.invoke_model.return_value["body"].seek(0)
            return client.invoke_model.return_value

        client.invoke_model.side_effect = capture_invoke

        with patch(
            "ckn_ingestion.bedrock_classifier._call_with_throttle_retry",
            side_effect=lambda fn: fn(),
        ):
            classify_page("Title", "Content", client)

        body_dict = json.loads(captured_bodies[0])
        assert body_dict["max_tokens"] == 1024


# ---------------------------------------------------------------------------
# classify_page — fallback behavior
# ---------------------------------------------------------------------------


class TestClassifyPageFallback:
    def test_fallback_on_invalid_json_response(self):
        client = _make_bedrock_client("not valid json at all")

        with patch(
            "ckn_ingestion.bedrock_classifier._call_with_throttle_retry",
            side_effect=lambda fn: fn(),
        ):
            result = classify_page("Some Page", "Content.", client)

        assert result == FALLBACK_CLASSIFICATION

    def test_fallback_on_missing_fields(self):
        client = _make_bedrock_client(json.dumps({"doc_type": "runbook"}))

        with patch(
            "ckn_ingestion.bedrock_classifier._call_with_throttle_retry",
            side_effect=lambda fn: fn(),
        ):
            result = classify_page("Some Page", "Content.", client)

        assert result == FALLBACK_CLASSIFICATION

    def test_fallback_on_invalid_doc_type(self):
        data = _make_valid_classification_dict(doc_type="invalid_type")
        client = _make_bedrock_client_from_dict(data)

        with patch(
            "ckn_ingestion.bedrock_classifier._call_with_throttle_retry",
            side_effect=lambda fn: fn(),
        ):
            result = classify_page("Some Page", "Content.", client)

        assert result == FALLBACK_CLASSIFICATION

    def test_fallback_on_invalid_severity(self):
        data = _make_valid_classification_dict(severity_relevance="sev99")
        client = _make_bedrock_client_from_dict(data)

        with patch(
            "ckn_ingestion.bedrock_classifier._call_with_throttle_retry",
            side_effect=lambda fn: fn(),
        ):
            result = classify_page("Some Page", "Content.", client)

        assert result == FALLBACK_CLASSIFICATION

    def test_fallback_logs_page_title_not_content(self, caplog):
        client = _make_bedrock_client("bad json")

        with (
            patch(
                "ckn_ingestion.bedrock_classifier._call_with_throttle_retry",
                side_effect=lambda fn: fn(),
            ),
            caplog.at_level("ERROR", logger="ckn_ingestion.bedrock_classifier"),
        ):
            classify_page("My Page Title", "SECRET CONTENT NEVER LOG THIS", client)

        assert "My Page Title" in caplog.text
        assert "SECRET CONTENT NEVER LOG THIS" not in caplog.text


# ---------------------------------------------------------------------------
# classify_page — throttle retry
# ---------------------------------------------------------------------------


class TestClassifyPageThrottleRetry:
    def test_throttling_exception_triggers_retry(self):
        """ThrottlingException from Bedrock should be retried."""
        from botocore.exceptions import ClientError

        throttle_error = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
            "InvokeModel",
        )

        valid_response_body = json.dumps(
            {"content": [{"text": json.dumps(_make_valid_classification_dict())}]}
        )

        client = MagicMock()
        # Fail twice, succeed on third
        client.invoke_model.side_effect = [
            throttle_error,
            throttle_error,
            {"body": io.BytesIO(valid_response_body.encode("utf-8"))},
        ]

        result = classify_page("RDS Failover", "## Steps", client)

        assert result.doc_type == "runbook"
        assert client.invoke_model.call_count == 3

    def test_throttle_exhaustion_returns_fallback(self):
        """After max retries exhausted, FALLBACK_CLASSIFICATION is returned."""
        from botocore.exceptions import ClientError

        throttle_error = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
            "InvokeModel",
        )

        client = MagicMock()
        client.invoke_model.side_effect = throttle_error

        result = classify_page("Some Page", "Content.", client)

        assert result == FALLBACK_CLASSIFICATION

    def test_throttle_exhaustion_logs_page_title(self, caplog):
        """Throttle exhaustion should log the page title."""
        from botocore.exceptions import ClientError

        throttle_error = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
            "InvokeModel",
        )

        client = MagicMock()
        client.invoke_model.side_effect = throttle_error

        with caplog.at_level("ERROR", logger="ckn_ingestion.bedrock_classifier"):
            classify_page("Critical Page Title", "Content.", client)

        assert "Critical Page Title" in caplog.text

    def test_non_throttling_exception_yields_fallback(self):
        """A non-throttling exception from invoke_model does NOT propagate — classify_page
        catches it and returns FALLBACK_CLASSIFICATION so a single bad page can't abort the
        whole ingestion run (see classify_page's broad `except Exception` fail-safe)."""
        client = MagicMock()
        client.invoke_model.side_effect = RuntimeError("Unexpected error")

        # RuntimeError is not a ThrottlingException, so _call_with_throttle_retry re-raises
        # it immediately; classify_page's generic handler then applies the fallback.
        result = classify_page("Some Page", "Content.", client)

        assert result == FALLBACK_CLASSIFICATION


# ---------------------------------------------------------------------------
# Property-based bug condition exploration test (Task 1)
# ---------------------------------------------------------------------------

from hypothesis import assume, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

# Strategy: generate a valid classification dict with random valid values
_valid_doc_type_st = st.sampled_from(sorted(VALID_DOC_TYPES))
_valid_severity_st = st.sampled_from(sorted(VALID_SEVERITY))
_nonempty_text_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z"), blacklist_characters="\x00"),
    min_size=1,
    max_size=50,
).filter(lambda s: s.strip())


@st.composite
def valid_classification_dicts(draw):
    """Generate a random valid classification dict."""
    return {
        "doc_type": draw(_valid_doc_type_st),
        "service": draw(_nonempty_text_st),
        "severity_relevance": draw(_valid_severity_st),
        "owner_team": draw(_nonempty_text_st),
        "region": draw(_nonempty_text_st),
        "summary": draw(_nonempty_text_st),
    }


def _wrap_json_fenced(json_str: str) -> str:
    return f"```json\n{json_str}\n```"


def _wrap_bare_fenced(json_str: str) -> str:
    return f"```\n{json_str}\n```"


def _wrap_preamble(json_str: str) -> str:
    return f"Here is the classification:\n{json_str}"


def _wrap_preamble_fenced(json_str: str) -> str:
    return f"Sure, here you go:\n```json\n{json_str}\n```"


_wrapping_strategies = st.sampled_from(
    [
        _wrap_json_fenced,
        _wrap_bare_fenced,
        _wrap_preamble,
        _wrap_preamble_fenced,
    ]
)


class TestBugConditionExploration:
    """Property 1: Bug Condition — Fenced/Preambled JSON Causes ValueError.

    **Validates: Requirements 1.1, 1.2**

    This test generates valid classification dicts, wraps them in random
    fence/preamble formats, and asserts parse_classification() returns a valid
    Classification with correct field values.

    On UNFIXED code, this test is EXPECTED TO FAIL with ValueError because
    json.loads() cannot parse the raw fenced/preambled string. Failure confirms
    the bug exists.
    """

    @given(data=valid_classification_dicts(), wrapper=_wrapping_strategies)
    @settings(max_examples=50)
    def test_fenced_preambled_json_returns_valid_classification(self, data, wrapper):
        """parse_classification should handle wrapped JSON and return correct Classification."""
        raw_json = json.dumps(data)
        wrapped = wrapper(raw_json)

        # The bug condition: wrapped string is NOT valid JSON itself,
        # but contains a valid JSON object inside.
        try:
            json.loads(wrapped)
            assume(False)  # Skip if the wrapped string happens to be valid JSON
        except json.JSONDecodeError:
            pass  # Good — this confirms the bug condition holds

        result = parse_classification(wrapped)

        # Assert result is a valid Classification with correct field values.
        # owner_team/service are normalized (F6), so compare against the
        # normalized form of the input rather than the raw value.
        assert isinstance(result, Classification)
        assert result.doc_type == data["doc_type"]
        assert result.doc_type in VALID_DOC_TYPES
        assert result.service == normalize_vocab(data["service"])
        assert result.severity_relevance == data["severity_relevance"]
        assert result.severity_relevance in VALID_SEVERITY
        assert result.owner_team == normalize_vocab(data["owner_team"])
        assert result.region == data["region"]
        assert result.summary == data["summary"]


# ---------------------------------------------------------------------------
# Property-based preservation tests (Task 2)
# ---------------------------------------------------------------------------


# Strategy: generate strings that contain no JSON object pattern ({...})
_no_json_object_st = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        blacklist_characters="{}\x00",
    ),
    min_size=1,
    max_size=100,
).filter(lambda s: s.strip())


# Strategy: generate JSON dicts with missing required fields
_required_fields = ["doc_type", "service", "severity_relevance", "owner_team", "region", "summary"]


@st.composite
def classification_dicts_missing_fields(draw):
    """Generate a classification dict with at least one required field removed."""
    full = draw(valid_classification_dicts())
    # Pick a non-empty subset of fields to remove
    fields_to_remove = draw(
        st.lists(
            st.sampled_from(_required_fields),
            min_size=1,
            max_size=len(_required_fields),
            unique=True,
        )
    )
    for f in fields_to_remove:
        del full[f]
    return full


# Strategy: generate JSON dicts with invalid enum values
@st.composite
def classification_dicts_invalid_enums(draw):
    """Generate a classification dict with an invalid doc_type or severity_relevance."""
    d = draw(valid_classification_dicts())
    # Choose which enum to invalidate
    choice = draw(st.sampled_from(["doc_type", "severity_relevance"]))
    if choice == "doc_type":
        bad_value = draw(_nonempty_text_st.filter(lambda s: s not in VALID_DOC_TYPES))
        d["doc_type"] = bad_value
    else:
        bad_value = draw(_nonempty_text_st.filter(lambda s: s not in VALID_SEVERITY))
        d["severity_relevance"] = bad_value
    return d


class TestPreservationProperties:
    """Property 2: Preservation — Raw JSON Parsing and Invalid Input Rejection Unchanged.

    **Validates: Requirements 3.1, 3.2, 3.3**

    These tests observe baseline behavior on UNFIXED code to establish
    preservation invariants. All three properties must PASS on unfixed code.
    """

    @given(data=valid_classification_dicts())
    @settings(max_examples=50)
    def test_raw_json_preservation(self, data):
        """Property 2a: Raw JSON preservation.

        For any valid classification dict serialized to raw JSON,
        parse_classification() returns a Classification with matching field values.

        **Validates: Requirements 3.1**
        """
        raw_json = json.dumps(data)
        result = parse_classification(raw_json)

        # owner_team/service are normalized (F6); the rest round-trip verbatim.
        assert isinstance(result, Classification)
        assert result.doc_type == data["doc_type"]
        assert result.service == normalize_vocab(data["service"])
        assert result.severity_relevance == data["severity_relevance"]
        assert result.owner_team == normalize_vocab(data["owner_team"])
        assert result.region == data["region"]
        assert result.summary == data["summary"]

    @given(text=_no_json_object_st)
    @settings(max_examples=50)
    def test_invalid_input_rejection(self, text):
        """Property 2b: Invalid input rejection.

        For any string that contains no {...} JSON object pattern,
        parse_classification() raises ValueError.

        **Validates: Requirements 3.2**
        """
        with pytest.raises(ValueError):
            parse_classification(text)

    @given(data=classification_dicts_missing_fields())
    @settings(max_examples=50)
    def test_validation_error_missing_fields(self, data):
        """Property 2c (missing fields): Validation error preservation.

        For any JSON dict with missing required fields,
        parse_classification() raises ValueError.

        **Validates: Requirements 3.3**
        """
        raw_json = json.dumps(data)
        with pytest.raises(ValueError):
            parse_classification(raw_json)

    @given(data=classification_dicts_invalid_enums())
    @settings(max_examples=50)
    def test_validation_error_invalid_enums(self, data):
        """Property 2c (invalid enums): Validation error preservation.

        For any JSON dict with invalid doc_type or severity_relevance enum values,
        parse_classification() raises ValueError.

        **Validates: Requirements 3.3**
        """
        raw_json = json.dumps(data)
        with pytest.raises(ValueError):
            parse_classification(raw_json)
