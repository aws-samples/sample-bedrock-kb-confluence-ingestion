"""HTML sanitizer for Confluence page content.

Strips dangerous HTML elements and attributes before markdown conversion.
Uses bleach for robust tag/attribute allowlisting with an additional pass
to remove javascript: URIs in allowlisted attributes.
"""

import re

import bleach

# Tags safe to keep for markdownify conversion.
_ALLOWED_TAGS = [
    "a",
    "abbr",
    "acronym",
    "b",
    "blockquote",
    "br",
    "code",
    "dd",
    "del",
    "div",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
]

_ALLOWED_ATTRIBUTES: dict[str, list[str]] = {
    "a": ["href", "title"],
    "img": ["src", "alt", "title", "width", "height"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan"],
}

# Extra pass: remove javascript: URIs that may survive allowlisted attrs.
_JS_URI_RE = re.compile(
    r"""(?P<attr>(?:href|src)\s*=\s*)(?:"javascript:[^"]*"|'javascript:[^']*')""",
    re.IGNORECASE,
)


def sanitize_html(html: str) -> str:
    """Strip dangerous HTML elements and attributes from Confluence page content.

    Uses bleach allowlist-based cleaning (robust) followed by an extra pass
    to strip javascript: URIs from allowlisted attributes.
    """
    result = bleach.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        strip=True,
    )
    result = _JS_URI_RE.sub(r'\g<attr>""', result)
    return result
