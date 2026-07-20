"""Minimal smoke test for Brazil build fleet (no runtime deps required)."""


def test_models_importable():
    """Verify the models module can be imported (no external deps)."""
    from ckn_ingestion.models import Attachment, PageContent  # noqa: F401

    assert Attachment is not None
    assert PageContent is not None
