"""Property-based tests for KB chunking optimization.

Uses Hypothesis to verify correctness properties from the design document.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from ckn_ingestion.table_flattener import flatten_tables

# ---------------------------------------------------------------------------
# Custom Hypothesis strategies
# ---------------------------------------------------------------------------

# Characters safe for markdown table cells (no pipes, no newlines)
_safe_cell_text = (
    st.text(
        alphabet=st.characters(blacklist_characters="|\n\r"),
        min_size=1,
    )
    .map(str.strip)
    .filter(lambda s: len(s) > 0)
)


@st.composite
def markdown_tables(draw: st.DrawFn):
    """Generate a random markdown table with headers and data rows.

    Returns a tuple of (markdown_string, headers, data_rows) where
    data_rows is a list of lists of cell values (some may be empty).
    """
    num_cols = draw(st.integers(min_value=1, max_value=5))
    num_data_rows = draw(st.integers(min_value=1, max_value=6))

    headers = draw(st.lists(_safe_cell_text, min_size=num_cols, max_size=num_cols))

    # Build data rows — some cells may be empty to test omission logic
    data_rows: list[list[str]] = []
    for _ in range(num_data_rows):
        row = draw(
            st.lists(
                st.one_of(
                    _safe_cell_text,
                    st.just(""),  # empty cell
                ),
                min_size=num_cols,
                max_size=num_cols,
            )
        )
        data_rows.append(row)

    # Assemble markdown table
    header_line = "| " + " | ".join(headers) + " |"
    separator_line = "| " + " | ".join("---" for _ in headers) + " |"
    row_lines = []
    for row in data_rows:
        row_lines.append("| " + " | ".join(row) + " |")

    md = "\n".join([header_line, separator_line] + row_lines)
    return md, headers, data_rows


# ---------------------------------------------------------------------------
# Feature: kb-chunking-optimization, Property 5: Table flattening sentence format
# ---------------------------------------------------------------------------
# **Validates: Requirements 2.1, 2.2**


@given(
    table_data=markdown_tables(),
    page_title=_safe_cell_text,
)
@settings(max_examples=100)
def test_property5_table_flattening_sentence_format(
    table_data: tuple[str, list[str], list[list[str]]],
    page_title: str,
):
    """Property 5: Table flattening produces correctly formatted sentences.

    For any markdown table with N data rows (excluding header and separator),
    flatten_tables shall produce exactly N sentences, each in the format
    "{page_title}: {col1} is {val1}, {col2} is {val2}", where column-value
    pairs with empty values are omitted.
    """
    md, headers, data_rows = table_data

    result = flatten_tables(md, page_title)
    sentences = [line for line in result.split("\n") if line.strip()]

    # --- Sentence count equals data row count (rows with all-empty cells produce no sentence) ---
    expected_sentences = []
    for row in data_rows:
        pairs = [f"{col} is {val}" for col, val in zip(headers, row) if val]  # empty cells omitted
        if pairs:
            expected_sentences.append(f"{page_title}: {', '.join(pairs)}")

    assert len(sentences) == len(expected_sentences), (
        f"Expected {len(expected_sentences)} sentences but got {len(sentences)}.\n"
        f"Input markdown:\n{md}\n"
        f"Result:\n{result}"
    )

    # --- Each sentence matches the expected format ---
    for actual, expected in zip(sentences, expected_sentences):
        assert actual == expected, (
            f"Sentence mismatch.\n" f"Expected: {expected!r}\n" f"Actual:   {actual!r}"
        )


# ---------------------------------------------------------------------------
# Feature: kb-chunking-optimization, Property 6: Non-table content preservation
# ---------------------------------------------------------------------------
# **Validates: Requirements 2.4, 2.6**


@st.composite
def mixed_markdown_blocks(draw: st.DrawFn):
    """Generate markdown mixing non-table paragraphs and table blocks.

    Accepts a page_title so that non-table lines are guaranteed not to
    collide with generated flattened-table sentences.

    Returns (full_markdown, expected_non_table_lines) where
    expected_non_table_lines is the list of non-table lines in order.
    """
    page_title: str = draw(st.shared(_safe_cell_text, key="p6_title"))

    # Non-table lines must not start with '|' and must not look like
    # a generated sentence ("{page_title}: ...")
    sentence_prefix = f"{page_title}: "
    non_table_line = (
        st.text(
            alphabet=st.characters(blacklist_characters="\n\r"),
            min_size=1,
        )
        .map(str.strip)
        .filter(
            lambda s: len(s) > 0 and not s.startswith("|") and not s.startswith(sentence_prefix)
        )
    )

    num_blocks = draw(st.integers(min_value=1, max_value=6))
    all_lines: list[str] = []
    non_table_lines: list[str] = []

    for _ in range(num_blocks):
        is_table = draw(st.booleans())
        if is_table:
            # Generate a small table block
            num_cols = draw(st.integers(min_value=1, max_value=3))
            num_rows = draw(st.integers(min_value=1, max_value=3))
            headers = draw(st.lists(_safe_cell_text, min_size=num_cols, max_size=num_cols))
            header_line = "| " + " | ".join(headers) + " |"
            sep_line = "| " + " | ".join("---" for _ in headers) + " |"
            all_lines.append(header_line)
            all_lines.append(sep_line)
            for _ in range(num_rows):
                cells = draw(
                    st.lists(
                        st.one_of(_safe_cell_text, st.just("")),
                        min_size=num_cols,
                        max_size=num_cols,
                    )
                )
                all_lines.append("| " + " | ".join(cells) + " |")
        else:
            # Generate 1-3 non-table lines
            count = draw(st.integers(min_value=1, max_value=3))
            for _ in range(count):
                line = draw(non_table_line)
                all_lines.append(line)
                non_table_lines.append(line)

    return "\n".join(all_lines), non_table_lines


@given(
    page_title=st.shared(_safe_cell_text, key="p6_title"),
    data=mixed_markdown_blocks(),
)
@settings(max_examples=100)
def test_property6_non_table_content_preservation(
    page_title: str,
    data: tuple[str, list[str]],
):
    """Property 6: Non-table content preservation.

    For any markdown string, the non-table lines in the output of
    flatten_tables shall be identical to the non-table lines in the input,
    in the same relative order and form.
    """
    md, expected_non_table_lines = data

    result = flatten_tables(md, page_title)

    # Extract non-table lines from the output.
    # Non-table lines are those that:
    #   - are non-empty (empty lines can be artifacts of table removal)
    #   - don't start with '|' (table markup)
    #   - are not generated sentences (which start with "{page_title}: ")
    sentence_prefix = f"{page_title}: "
    output_non_table_lines = [
        line
        for line in result.split("\n")
        if line.strip()
        and not line.strip().startswith("|")
        and not line.startswith(sentence_prefix)
    ]

    assert output_non_table_lines == expected_non_table_lines, (
        f"Non-table lines differ.\n"
        f"Expected: {expected_non_table_lines!r}\n"
        f"Actual:   {output_non_table_lines!r}\n"
        f"Input markdown:\n{md}\n"
        f"Output:\n{result}"
    )


from ckn_ingestion.content_splitter import split_markdown  # noqa: E402

# ---------------------------------------------------------------------------
# Feature: kb-chunking-optimization, Property 1: Heading-boundary splitting
# ---------------------------------------------------------------------------
# **Validates: Requirements 1.1**

# Strategy: generate heading+content pairs for sections with h1/h2 headings
# Paragraph text must not start with "# " to avoid being treated as headings,
# and must be non-empty so the splitter doesn't drop the chunk.
_paragraph_text = (
    st.text(
        alphabet=st.characters(blacklist_characters="\n\r#"),
        min_size=1,
    )
    .map(str.strip)
    .filter(lambda s: len(s) > 0)
)


@st.composite
def heading_sections(draw: st.DrawFn):
    """Generate markdown with 2+ heading-delimited sections.

    Returns (markdown_string, expected_section_count) where each section
    has a heading (h1 or h2) followed by non-empty paragraph text.
    """
    sections = draw(
        st.lists(
            st.tuples(
                st.sampled_from(["# ", "## "]),
                _paragraph_text,  # heading text
                _paragraph_text,  # body paragraph
            ),
            min_size=2,
            max_size=8,
        )
    )

    lines: list[str] = []
    for heading_prefix, heading_text, body_text in sections:
        lines.append(f"{heading_prefix}{heading_text}")
        lines.append(body_text)

    markdown = "\n".join(lines)
    section_count = len(sections)
    return markdown, section_count


@given(data=heading_sections(), page_title=_safe_cell_text)
@settings(max_examples=100)
def test_property1_heading_boundary_splitting(
    data: tuple[str, int],
    page_title: str,
):
    """Property 1: Heading-boundary splitting.

    For any markdown string containing two or more heading-delimited sections,
    split_markdown shall return a list with one chunk per section, where each
    chunk begins at a heading boundary from the original markdown.
    """
    markdown, section_count = data
    prefix = f"# {page_title}\n\n"

    chunks = split_markdown(markdown, page_title)

    # Chunk count equals section count
    assert len(chunks) == section_count, (
        f"Expected {section_count} chunks but got {len(chunks)}.\n"
        f"Markdown:\n{markdown!r}\n"
        f"Chunks:\n{chunks!r}"
    )

    # Each chunk starts with the title prefix
    for i, chunk in enumerate(chunks):
        assert chunk.startswith(prefix), (
            f"Chunk {i} does not start with title prefix.\n"
            f"Expected prefix: {prefix!r}\n"
            f"Chunk start: {chunk[:len(prefix)+20]!r}"
        )


# ---------------------------------------------------------------------------
# Feature: kb-chunking-optimization, Property 2: Title prefix invariant
# ---------------------------------------------------------------------------
# **Validates: Requirements 1.2**


@given(
    page_title=_safe_cell_text,
    markdown=st.text(),
)
@settings(max_examples=100)
def test_property2_title_prefix_invariant(
    page_title: str,
    markdown: str,
):
    """Property 2: Title prefix invariant.

    For any page title and any markdown string, every chunk returned by
    split_markdown shall begin with '# {page_title}\\n\\n'.
    """
    prefix = f"# {page_title}\n\n"

    chunks = split_markdown(markdown, page_title)

    assert len(chunks) >= 1, "split_markdown must return at least one chunk"

    for i, chunk in enumerate(chunks):
        assert chunk.startswith(prefix), (
            f"Chunk {i} does not start with title prefix.\n"
            f"Expected prefix: {prefix!r}\n"
            f"Chunk start: {chunk[:len(prefix)+30]!r}\n"
            f"Page title: {page_title!r}\n"
            f"Markdown: {markdown!r}"
        )


# ---------------------------------------------------------------------------
# Feature: kb-chunking-optimization, Property 3: Split round-trip content preservation
# ---------------------------------------------------------------------------
# **Validates: Requirements 1.7**


@given(data=heading_sections(), page_title=_safe_cell_text)
@settings(max_examples=100)
def test_property3_split_round_trip_content_preservation(
    data: tuple[str, int],
    page_title: str,
):
    """Property 3: Split round-trip content preservation.

    For any markdown string and page title, concatenating all chunks from
    split_markdown — after stripping the '# {page_title}\\n\\n' prefix from
    chunks at index 1..N — shall produce content equivalent to
    '# {page_title}\\n\\n{original_markdown}'.
    """
    markdown, _ = data
    prefix = f"# {page_title}\n\n"

    chunks = split_markdown(markdown, page_title)

    # Reassemble: chunk[0] as-is, chunks[1..N] strip the title prefix
    reassembled_parts: list[str] = []
    for i, chunk in enumerate(chunks):
        if i == 0:
            reassembled_parts.append(chunk)
        else:
            assert chunk.startswith(prefix), (
                f"Chunk {i} does not start with expected prefix.\n" f"Chunk: {chunk!r}"
            )
            reassembled_parts.append(chunk[len(prefix) :])

    reassembled = "".join(reassembled_parts)
    expected = prefix + markdown

    assert reassembled == expected, (
        f"Round-trip content mismatch.\n"
        f"Expected:\n{expected!r}\n"
        f"Reassembled:\n{reassembled!r}\n"
        f"Chunks:\n{chunks!r}"
    )


from ckn_ingestion.metadata_enricher import enrich_metadata  # noqa: E402
from ckn_ingestion.models import Classification, MetadataSidecar, PageContent  # noqa: E402

# ---------------------------------------------------------------------------
# Strategies for metadata enricher property tests
# ---------------------------------------------------------------------------

_non_empty_text = st.text(min_size=1).filter(lambda s: s.strip())

_doc_types = st.sampled_from(["runbook", "architecture", "postmortem", "contact", "reference"])
_severity = st.sampled_from(["sev1", "sev2", "all"])


@st.composite
def random_page_content(draw: st.DrawFn) -> PageContent:
    """Generate a random PageContent with a non-empty title."""
    return PageContent(
        page_id=draw(_non_empty_text),
        title=draw(_non_empty_text),
        space_key=draw(_non_empty_text),
        author=draw(_non_empty_text),
        last_modified=draw(_non_empty_text),
        url=draw(_non_empty_text),
        markdown=draw(st.text()),
        attachments=[],
    )


@st.composite
def random_classification(draw: st.DrawFn) -> Classification:
    """Generate a random Classification."""
    return Classification(
        doc_type=draw(_doc_types),
        service=draw(_non_empty_text),
        severity_relevance=draw(_severity),
        owner_team=draw(_non_empty_text),
        region=draw(_non_empty_text),
        summary=draw(st.text()),
    )


# ---------------------------------------------------------------------------
# Feature: kb-chunking-optimization, Property 7: page_title present in sidecar
# ---------------------------------------------------------------------------
# **Validates: Requirements 3.1**


@given(
    page=random_page_content(),
    classification=random_classification(),
    has_images=st.booleans(),
)
@settings(max_examples=100)
def test_property7_page_title_present_in_sidecar(
    page: PageContent,
    classification: Classification,
    has_images: bool,
):
    """Property 7: page_title present in sidecar.

    For any PageContent with a non-empty title and any Classification,
    enrich_metadata shall produce a MetadataSidecar whose metadata_attributes
    dict contains a page_title key equal to the page's title.
    """
    sidecar = enrich_metadata(page, classification, has_images)

    assert "page_title" in sidecar.metadata_attributes, (
        f"'page_title' key missing from sidecar metadata_attributes.\n"
        f"Keys present: {list(sidecar.metadata_attributes.keys())}"
    )
    assert sidecar.metadata_attributes["page_title"] == page.title, (
        f"page_title mismatch.\n"
        f"Expected: {page.title!r}\n"
        f"Actual:   {sidecar.metadata_attributes['page_title']!r}"
    )


from unittest.mock import MagicMock  # noqa: E402

from ckn_ingestion.s3_uploader import upload_page  # noqa: E402

# ---------------------------------------------------------------------------
# Strategies for S3 uploader property tests
# ---------------------------------------------------------------------------

# Safe characters for S3 key components (ASCII letters and digits only).
_s3_safe_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N"),
        whitelist_characters="",
        max_codepoint=127,
    ),
    min_size=1,
    max_size=30,
)


# ---------------------------------------------------------------------------
# Feature: kb-chunking-optimization, Property 4: Multi-chunk upload S3 keys
# ---------------------------------------------------------------------------
# **Validates: Requirements 1.4, 1.5**


@given(
    space_key=_s3_safe_text,
    page_id=_s3_safe_text,
    chunks=st.lists(st.text(min_size=1), min_size=2, max_size=10),
)
@settings(max_examples=100)
def test_property4_multi_chunk_upload_s3_keys(
    space_key: str,
    page_id: str,
    chunks: list[str],
):
    """Property 4: Multi-chunk upload produces correct S3 keys and paired sidecars.

    For any list of chunks with length > 1, calling upload_page shall invoke
    put_object exactly 2 * len(chunks) times, with content keys matching
    confluence/{space}/{page_id}_chunk_{i}.md and sidecar keys matching
    confluence/{space}/{page_id}_chunk_{i}.md.metadata.json for each index i.
    """
    mock_client = MagicMock()
    account_id = "123456789012"
    sidecar = MetadataSidecar(metadata_attributes={"page_title": "Test Page"})

    upload_page(mock_client, account_id, space_key, page_id, chunks, sidecar)

    # Verify put_object called exactly 2 * len(chunks) times
    assert mock_client.put_object.call_count == 2 * len(chunks), (
        f"Expected {2 * len(chunks)} put_object calls but got "
        f"{mock_client.put_object.call_count}."
    )

    # Collect all keys from put_object calls
    actual_keys = [call.kwargs["Key"] for call in mock_client.put_object.call_args_list]

    # Verify content and sidecar keys for each chunk index
    for i in range(len(chunks)):
        expected_content_key = f"confluence/{space_key}/{page_id}_chunk_{i}.md"
        expected_sidecar_key = f"confluence/{space_key}/{page_id}_chunk_{i}.md.metadata.json"

        assert expected_content_key in actual_keys, (
            f"Missing content key for chunk {i}.\n"
            f"Expected: {expected_content_key!r}\n"
            f"Actual keys: {actual_keys!r}"
        )
        assert expected_sidecar_key in actual_keys, (
            f"Missing sidecar key for chunk {i}.\n"
            f"Expected: {expected_sidecar_key!r}\n"
            f"Actual keys: {actual_keys!r}"
        )


# ---------------------------------------------------------------------------
# Feature: kb-chunking-optimization, Property 8: S3 extension and content type
# ---------------------------------------------------------------------------
# **Validates: Requirements 3.4, 3.5, 3.6**


@given(
    space_key=_s3_safe_text,
    page_id=_s3_safe_text,
    chunks=st.lists(st.text(min_size=1), min_size=1, max_size=5),
)
@settings(max_examples=100)
def test_property8_s3_extension_and_content_type(
    space_key: str,
    page_id: str,
    chunks: list[str],
):
    """Property 8: S3 objects use correct extension and content type.

    For any upload via upload_page, all content object keys shall end with
    .md, all sidecar keys shall end with .md.metadata.json, the ContentType
    for content objects shall be text/markdown, and the ContentType for
    sidecar objects shall be application/json.
    """
    mock_client = MagicMock()
    account_id = "123456789012"
    sidecar = MetadataSidecar(metadata_attributes={"page_title": "Test Page"})

    upload_page(mock_client, account_id, space_key, page_id, chunks, sidecar)

    calls = mock_client.put_object.call_args_list

    # Separate content and sidecar calls by key suffix
    content_calls = [c for c in calls if not c.kwargs["Key"].endswith(".metadata.json")]
    sidecar_calls = [c for c in calls if c.kwargs["Key"].endswith(".metadata.json")]

    # Every content key ends with .md (and not .md.metadata.json)
    for call in content_calls:
        key = call.kwargs["Key"]
        assert key.endswith(".md"), f"Content key does not end with '.md': {key!r}"
        assert not key.endswith(
            ".md.metadata.json"
        ), f"Content key should not end with '.md.metadata.json': {key!r}"

    # Every sidecar key ends with .md.metadata.json
    for call in sidecar_calls:
        key = call.kwargs["Key"]
        assert key.endswith(
            ".md.metadata.json"
        ), f"Sidecar key does not end with '.md.metadata.json': {key!r}"

    # Content objects have ContentType text/markdown
    for call in content_calls:
        ct = call.kwargs["ContentType"]
        assert (
            ct == "text/markdown"
        ), f"Content object ContentType should be 'text/markdown', got {ct!r}"

    # Sidecar objects have ContentType application/json
    for call in sidecar_calls:
        ct = call.kwargs["ContentType"]
        assert (
            ct == "application/json"
        ), f"Sidecar object ContentType should be 'application/json', got {ct!r}"
