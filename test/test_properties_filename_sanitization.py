# Feature: security-review-remediation, Property 3: Filename sanitization removes all path traversal components
"""Property-based tests for attachment filename sanitization.

Uses Hypothesis to verify that _sanitize_filename() strips all path
separator characters (``/``, ``\\``) and parent directory references
(``..``) from any input string.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ckn_ingestion.image_processor import _sanitize_filename

# ---------------------------------------------------------------------------
# Custom Hypothesis strategies
# ---------------------------------------------------------------------------

# Arbitrary text that may naturally contain path traversal characters
_arbitrary_text = st.text(min_size=0, max_size=200)

# Text with path traversal components deliberately injected
_traversal_injected_text = st.builds(
    lambda parts: "".join(parts),
    st.lists(
        st.one_of(
            st.just("/"),
            st.just("\\"),
            st.just(".."),
            st.just("../"),
            st.just("..\\"),
            st.text(
                alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
                min_size=0,
                max_size=20,
            ),
        ),
        min_size=1,
        max_size=10,
    ),
)


# ---------------------------------------------------------------------------
# Property 3: Filename sanitization removes all path traversal components
# ---------------------------------------------------------------------------
# **Validates: Requirements 2.1**


@given(filename=st.one_of(_arbitrary_text, _traversal_injected_text))
@settings(max_examples=100)
def test_property3_filename_sanitization_removes_all_path_traversal_components(
    filename: str,
):
    """Property 3: Filename sanitization removes all path traversal components.

    For any string used as an attachment filename, after sanitization the
    result SHALL NOT contain ``/``, ``\\``, or ``..`` substrings.

    **Validates: Requirements 2.1**
    """
    sanitized = _sanitize_filename(filename)

    assert "/" not in sanitized, (
        f"Sanitized filename still contains '/'.\n"
        f"Input:     {filename!r}\n"
        f"Sanitized: {sanitized!r}"
    )
    assert "\\" not in sanitized, (
        f"Sanitized filename still contains '\\'.\n"
        f"Input:     {filename!r}\n"
        f"Sanitized: {sanitized!r}"
    )
    assert ".." not in sanitized, (
        f"Sanitized filename still contains '..'.\n"
        f"Input:     {filename!r}\n"
        f"Sanitized: {sanitized!r}"
    )


# ---------------------------------------------------------------------------
# Property 4: Constructed file paths never escape the temporary directory
# ---------------------------------------------------------------------------
# **Validates: Requirements 2.4**
#
# Property 4 exercised _safe_path(), which was only used by the Draw.io
# attachment processor. Draw.io/SVG processing was removed due to dependency
# (cairosvg/drawio) license constraints, so _safe_path no longer exists and
# this property is retained only as a skipped stub.


@pytest.mark.skip(
    reason="_safe_path removed with Draw.io/SVG processing due to dependency license constraints."
)
def test_property4_constructed_file_paths_never_escape_temp_directory():
    pass
