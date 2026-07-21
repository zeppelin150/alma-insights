"""read_document must not turn an unreadable format into decoded garbage.

Audit finding 20: read_document had no unsupported-format branch — a real PDF
fell through to text decoding and was modelled into a one-slide deck of
mojibake. strict=True (used by deck modelling) raises; the lenient default
(Guru import) keeps a clear stub.
"""

import pytest

from src.data import doc_reader as dr


def _write(tmp_path, name, data: bytes):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_binary_extension_raises_in_strict_mode(tmp_path):
    pdf = _write(tmp_path, "deck.pdf", b"%PDF-1.7\n\x00\x01garbage")
    with pytest.raises(dr.UnsupportedDocumentError):
        dr.read_document(pdf, strict=True)


def test_binary_extension_returns_stub_in_lenient_mode(tmp_path):
    pdf = _write(tmp_path, "deck.pdf", b"%PDF-1.7\n\x00\x01garbage")
    out = dr.read_document(pdf)  # lenient default
    assert "can't be read locally" in out
    assert "\x00" not in out, "raw binary leaked into the returned text"


def test_mislabeled_binary_is_caught_by_content_sniff(tmp_path):
    """A binary file with a .txt extension must still be refused in strict
    mode — the NUL-byte sniff catches it."""
    fake_txt = _write(tmp_path, "notes.txt", b"PK\x03\x04\x00\x00binary zip")
    with pytest.raises(dr.UnsupportedDocumentError):
        dr.read_document(fake_txt, strict=True)


def test_real_text_still_reads(tmp_path):
    md = _write(tmp_path, "notes.md", b"# Title\n\nReal content.")
    out = dr.read_document(md, strict=True)
    assert "Real content." in out


def test_plain_text_extension_is_not_falsely_flagged(tmp_path):
    txt = _write(tmp_path, "a.txt", "café touché — accents are fine".encode())
    out = dr.read_document(txt, strict=True)
    assert "accents are fine" in out


def test_deck_modelling_surfaces_the_error(tmp_path, monkeypatch):
    """End to end: the deck path reports 'couldn't model a deck' rather than
    building one from garbage."""
    from src.data import pptx_store
    pdf = _write(tmp_path, "slides.pdf", b"%PDF-1.4\n\x00\x00")

    made = {"deck": False}
    monkeypatch.setattr(pptx_store, "save_deck",
                        lambda *a, **k: made.__setitem__("deck", True))
    # Mirror the page's handler logic without a Qt page.
    from src.data.doc_reader import read_document, UnsupportedDocumentError
    status = None
    try:
        read_document(pdf, strict=True)
    except UnsupportedDocumentError as exc:
        status = f"Couldn't model a deck: {exc}"
    assert status and "Couldn't model a deck" in status
    assert made["deck"] is False, "a deck was built from an unreadable file"
