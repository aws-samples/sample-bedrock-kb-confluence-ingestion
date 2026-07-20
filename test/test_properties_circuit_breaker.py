# Feature: security-review-remediation, Property 8: Circuit breaker state machine correctness
"""Property-based tests for the SpaceCircuitBreaker state machine.

Uses Hypothesis to verify that the circuit breaker trips if and only if
the last MAX_CONSECUTIVE_FAILURES events are all failures, and that a
success at any point resets the consecutive failure counter.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from ckn_ingestion.cli import MAX_CONSECUTIVE_FAILURES, SpaceCircuitBreaker

# ---------------------------------------------------------------------------
# Custom Hypothesis strategies
# ---------------------------------------------------------------------------

# Random sequences of True (success) / False (failure) events
_event_sequences = st.lists(
    st.booleans(),
    min_size=1,
    max_size=50,
)


# ---------------------------------------------------------------------------
# Property 8: Circuit breaker state machine correctness
# ---------------------------------------------------------------------------
# **Validates: Requirements 10.2, 10.4**


@given(events=_event_sequences)
@settings(max_examples=100)
def test_property8_circuit_breaker_state_machine_correctness(
    events: list[bool],
):
    """Property 8: Circuit breaker state machine correctness

    For any sequence of page processing results (success/failure) for a
    given space, the circuit breaker SHALL trip (halt processing) if and
    only if the last MAX_CONSECUTIVE_FAILURES results are all failures.
    A success at any point SHALL reset the consecutive failure counter
    to zero.

    **Validates: Requirements 10.2, 10.4**
    """
    breaker = SpaceCircuitBreaker()
    space_key = "TEST"
    consecutive_failures = 0

    for event in events:
        if event:
            # Success — reset counter
            breaker.record_success(space_key)
            consecutive_failures = 0
        else:
            # Failure — increment counter
            breaker.record_failure(space_key)
            consecutive_failures += 1

        # After each event, verify the breaker state matches expectation
        expected_tripped = consecutive_failures >= MAX_CONSECUTIVE_FAILURES
        actual_tripped = breaker.is_tripped(space_key)

        assert actual_tripped == expected_tripped, (
            f"Breaker state mismatch after processing events.\n"
            f"Events so far:          {events[:events.index(event) + 1] if event in events else events}\n"
            f"Consecutive failures:   {consecutive_failures}\n"
            f"MAX_CONSECUTIVE_FAILURES: {MAX_CONSECUTIVE_FAILURES}\n"
            f"Expected tripped:       {expected_tripped}\n"
            f"Actual tripped:         {actual_tripped}"
        )


# ---------------------------------------------------------------------------
# Unit test: Circuit breaker space isolation
# ---------------------------------------------------------------------------
# **Validates: Requirements 10.3**


def test_circuit_breaker_space_isolation():
    """Verify one space tripping the breaker does not affect other spaces.

    WHEN the Pipeline stops processing a space due to the Circuit_Breaker,
    THE Pipeline SHALL continue processing remaining spaces.

    **Validates: Requirements 10.3**
    """
    breaker = SpaceCircuitBreaker()

    # Trip the breaker for SPACE_A by recording MAX_CONSECUTIVE_FAILURES failures
    for _ in range(MAX_CONSECUTIVE_FAILURES):
        breaker.record_failure("SPACE_A")

    # SPACE_A should be tripped
    assert breaker.is_tripped("SPACE_A"), (
        "SPACE_A should be tripped after " f"{MAX_CONSECUTIVE_FAILURES} consecutive failures"
    )

    # SPACE_B should NOT be tripped — it has no failures at all
    assert not breaker.is_tripped(
        "SPACE_B"
    ), "SPACE_B should not be tripped; it is independent of SPACE_A"

    # Record a single failure for SPACE_B — still should not trip
    breaker.record_failure("SPACE_B")
    assert not breaker.is_tripped("SPACE_B"), "SPACE_B should not be tripped after only 1 failure"
