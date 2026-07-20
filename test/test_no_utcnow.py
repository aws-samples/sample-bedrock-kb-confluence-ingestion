"""Unit test: No datetime.utcnow() calls in production source.

Scans all .py files under src/ckn_ingestion/ and asserts that none contain
the deprecated datetime.utcnow() call.

Validates: Requirements 9.2
"""

from __future__ import annotations

from pathlib import Path

# Root of the production source tree (relative to repo root).
_SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "ckn_ingestion"


def test_no_utcnow_in_production_source() -> None:
    """Every .py file under src/ckn_ingestion/ must be free of utcnow() calls."""
    matches: list[tuple[str, int, str]] = []

    for py_file in sorted(_SRC_DIR.rglob("*.py")):
        for lineno, line in enumerate(py_file.read_text().splitlines(), start=1):
            if "utcnow()" in line:
                matches.append((str(py_file.relative_to(_SRC_DIR)), lineno, line.strip()))

    if matches:
        report = "\n".join(f"  {path}:{lineno}: {text}" for path, lineno, text in matches)
        raise AssertionError(
            f"Found {len(matches)} deprecated utcnow() call(s) in production source:\n{report}"
        )
