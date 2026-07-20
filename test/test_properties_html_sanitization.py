# Feature: security-review-remediation, Property 7: HTML sanitization removes all dangerous elements
"""Property-based tests for HTML sanitization.

Uses Hypothesis to verify that ``sanitize_html()`` strips all dangerous
HTML elements — ``<script>``, ``<iframe>``, inline event handlers
(``on*=``), and ``javascript:`` URIs — from any input string.
"""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from ckn_ingestion.sanitizer import sanitize_html

# ---------------------------------------------------------------------------
# Custom Hypothesis strategies
# ---------------------------------------------------------------------------

# Safe HTML fragments that should survive sanitization
_safe_fragments = st.one_of(
    st.just("<p>hello</p>"),
    st.just("<div>content</div>"),
    st.just('<a href="https://example.com">link</a>'),
    st.just('<img src="image.png">'),
    st.just("<h1>Title</h1>"),
    st.just("<ul><li>item</li></ul>"),
    st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N", "P", "S", "Z"),
        ),
        min_size=0,
        max_size=40,
    ),
)

# Dangerous <script> elements with varying case and content
_script_elements = st.one_of(
    st.builds(
        lambda body: f"<script>{body}</script>",
        st.text(min_size=0, max_size=30),
    ),
    st.builds(
        lambda body: f"<SCRIPT>{body}</SCRIPT>",
        st.text(min_size=0, max_size=30),
    ),
    st.builds(
        lambda attrs, body: f"<script {attrs}>{body}</script>",
        st.just('type="text/javascript"'),
        st.text(min_size=0, max_size=30),
    ),
    st.just("<script/>"),
    st.just('<Script src="evil.js"/>'),
    st.just('<SCRIPT src="evil.js"></SCRIPT>'),
)

# Dangerous <iframe> elements with varying case and content
_iframe_elements = st.one_of(
    st.builds(
        lambda src: f'<iframe src="{src}"></iframe>',
        st.text(min_size=1, max_size=30),
    ),
    st.builds(
        lambda src: f'<IFRAME src="{src}"></IFRAME>',
        st.text(min_size=1, max_size=30),
    ),
    st.just("<iframe/>"),
    st.just('<Iframe src="evil.html"/>'),
    st.just('<iframe width="100" height="100"></iframe>'),
)

# Event handler attributes (onclick=, onerror=, onload=, etc.)
_event_handler_names = st.sampled_from(
    [
        "onclick",
        "onerror",
        "onload",
        "onmouseover",
        "onfocus",
        "onblur",
        "onsubmit",
        "onchange",
        "ONCLICK",
        "OnLoad",
    ]
)

_event_handler_attrs = st.builds(
    lambda tag, name, val: f'<{tag} {name}="{val}">content</{tag}>',
    st.sampled_from(["div", "img", "a", "span", "body", "p"]),
    _event_handler_names,
    st.text(min_size=1, max_size=20),
)

# javascript: URI schemes in href/src attributes
_js_uri_elements = st.one_of(
    st.builds(
        lambda body: f'<a href="javascript:{body}">click</a>',
        st.text(min_size=0, max_size=30),
    ),
    st.builds(
        lambda body: f"<a href='javascript:{body}'>click</a>",
        st.text(min_size=0, max_size=30),
    ),
    st.builds(
        lambda body: f'<img src="javascript:{body}">',
        st.text(min_size=0, max_size=30),
    ),
    st.just('<a href="JAVASCRIPT:alert(1)">xss</a>'),
    st.just("<a href='JavaScript:void(0)'>link</a>"),
)

# Build HTML strings with random safe content interspersed with dangerous elements
_html_with_dangerous_elements = st.builds(
    lambda parts: "".join(parts),
    st.lists(
        st.one_of(
            _safe_fragments,
            _script_elements,
            _iframe_elements,
            _event_handler_attrs,
            _js_uri_elements,
        ),
        min_size=1,
        max_size=8,
    ),
)

# Regex patterns used for assertions
_SCRIPT_TAG_RE = re.compile(r"</?script\b", re.IGNORECASE)
_IFRAME_TAG_RE = re.compile(r"</?iframe\b", re.IGNORECASE)
_EVENT_HANDLER_ATTR_RE = re.compile(r"\bon[a-z]+=", re.IGNORECASE)
_JS_URI_RE = re.compile(
    r"""(?:href|src)\s*=\s*(?:"javascript:|'javascript:)""",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Property 7: HTML sanitization removes all dangerous elements
# ---------------------------------------------------------------------------
# **Validates: Requirements 5.1, 5.2, 5.3, 5.4**


@given(html=_html_with_dangerous_elements)
@settings(max_examples=100)
def test_property7_html_sanitization_removes_all_dangerous_elements(
    html: str,
) -> None:
    """Property 7: HTML sanitization removes all dangerous elements.

    For any HTML string, after sanitization the output SHALL NOT contain:
    (a) ``<script`` or ``</script>`` tags, (b) ``<iframe`` or ``</iframe>``
    tags, (c) any attribute matching ``on[a-z]+=`` (inline event handlers),
    or (d) any ``javascript:`` URI scheme in href/src attributes.

    **Validates: Requirements 5.1, 5.2, 5.3, 5.4**
    """
    sanitized = sanitize_html(html)

    # (a) No <script> or </script> tags (case insensitive)
    assert not _SCRIPT_TAG_RE.search(sanitized), (
        f"Sanitized output still contains <script> tag.\n"
        f"Input:     {html!r}\n"
        f"Sanitized: {sanitized!r}"
    )

    # (b) No <iframe> or </iframe> tags (case insensitive)
    assert not _IFRAME_TAG_RE.search(sanitized), (
        f"Sanitized output still contains <iframe> tag.\n"
        f"Input:     {html!r}\n"
        f"Sanitized: {sanitized!r}"
    )

    # (c) No inline event handler attributes (on[a-z]+=)
    assert not _EVENT_HANDLER_ATTR_RE.search(sanitized), (
        f"Sanitized output still contains event handler attribute.\n"
        f"Input:     {html!r}\n"
        f"Sanitized: {sanitized!r}"
    )

    # (d) No javascript: URI scheme in href/src attributes
    assert not _JS_URI_RE.search(sanitized), (
        f"Sanitized output still contains javascript: URI.\n"
        f"Input:     {html!r}\n"
        f"Sanitized: {sanitized!r}"
    )
