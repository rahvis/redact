"""
Tests for workonward_read.pdf_ops — merge/split/extract, encryption, sanitize,
properties and rotation.

License: GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

from datetime import datetime

import pytest
import fixtures
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link
from pypdf.constants import UserAccessPermissions as UAP
from pypdf.generic import DictionaryObject, NameObject, StreamObject, TextStringObject

from workonward_read import pdf_ops


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

def test_merge_pdfs(tmp_path):
    a = fixtures.make_pdf(tmp_path / "a.pdf", pages=3)
    b = fixtures.make_pdf(tmp_path / "b.pdf", pages=2, texts=["Bravo one", "Bravo two"])
    out = str(tmp_path / "merged.pdf")

    count = pdf_ops.merge_pdfs([a, b], out)

    assert count == 5
    reader = PdfReader(out)
    assert len(reader.pages) == 5
    assert "Page 1" in reader.pages[0].extract_text()
    assert "Bravo one" in reader.pages[3].extract_text()


def test_merge_with_encrypted_input(tmp_path):
    a = fixtures.make_pdf(tmp_path / "a.pdf", pages=1)
    enc = fixtures.make_encrypted_pdf(tmp_path / "enc.pdf", user_password="pw1", pages=2)
    out = str(tmp_path / "merged.pdf")

    count = pdf_ops.merge_pdfs([a, enc], out, passwords={enc: "pw1"})

    assert count == 3
    assert len(PdfReader(out).pages) == 3


def test_merge_encrypted_wrong_or_missing_password(tmp_path):
    enc = fixtures.make_encrypted_pdf(tmp_path / "enc.pdf", user_password="pw1")
    out = str(tmp_path / "merged.pdf")
    with pytest.raises(ValueError):
        pdf_ops.merge_pdfs([enc], out, passwords={enc: "wrong"})
    with pytest.raises(ValueError):
        pdf_ops.merge_pdfs([enc], out)


def test_merge_empty_inputs_raises(tmp_path):
    with pytest.raises(ValueError):
        pdf_ops.merge_pdfs([], str(tmp_path / "out.pdf"))


# ---------------------------------------------------------------------------
# split / extract
# ---------------------------------------------------------------------------

def test_split_pdf(tmp_path):
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=5)
    pattern = str(tmp_path / "part_{n}.pdf")

    outputs = pdf_ops.split_pdf(src, [(0, 1), (2, 4)], pattern)

    assert outputs == [str(tmp_path / "part_1.pdf"), str(tmp_path / "part_2.pdf")]
    assert len(PdfReader(outputs[0]).pages) == 2
    part2 = PdfReader(outputs[1])
    assert len(part2.pages) == 3
    assert "Page 3" in part2.pages[0].extract_text()


def test_split_invalid_ranges(tmp_path):
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=3)
    pattern = str(tmp_path / "part_{n}.pdf")
    with pytest.raises(ValueError):
        pdf_ops.split_pdf(src, [(0, 5)], pattern)
    with pytest.raises(ValueError):
        pdf_ops.split_pdf(src, [(2, 1)], pattern)
    with pytest.raises(ValueError):
        pdf_ops.split_pdf(src, [(0, 1)], str(tmp_path / "no_placeholder.pdf"))


def test_extract_pages_order(tmp_path):
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=3)
    out = str(tmp_path / "extract.pdf")

    pdf_ops.extract_pages(src, [2, 0], out)

    reader = PdfReader(out)
    assert len(reader.pages) == 2
    assert "Page 3" in reader.pages[0].extract_text()
    assert "Page 1" in reader.pages[1].extract_text()


def test_extract_pages_encrypted_and_bad_index(tmp_path):
    enc = fixtures.make_encrypted_pdf(tmp_path / "enc.pdf", user_password="s3", pages=2)
    out = str(tmp_path / "extract.pdf")
    pdf_ops.extract_pages(enc, [1], out, password="s3")
    assert len(PdfReader(out).pages) == 1

    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=2)
    with pytest.raises(ValueError):
        pdf_ops.extract_pages(src, [5], out)
    with pytest.raises(ValueError):
        pdf_ops.extract_pages(src, [], out)


# ---------------------------------------------------------------------------
# passwords
# ---------------------------------------------------------------------------

def test_set_passwords_aes_roundtrip(tmp_path):
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=2)
    out = str(tmp_path / "enc.pdf")

    pdf_ops.set_passwords(src, out, user_pw="usr", owner_pw="own")

    reader = PdfReader(out)
    assert reader.is_encrypted
    # AES-256 (R6) encryption dictionary
    enc_dict = reader.trailer["/Encrypt"]
    assert enc_dict["/V"] == 5
    assert enc_dict["/R"] == 6
    assert int(reader.decrypt("wrong")) == 0
    assert int(reader.decrypt("usr")) != 0
    assert "Page 1" in reader.pages[0].extract_text()
    # Owner password opens it too
    reader2 = PdfReader(out)
    assert int(reader2.decrypt("own")) != 0
    assert len(reader2.pages) == 2


def test_set_passwords_permission_flags(tmp_path):
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=1)
    out = str(tmp_path / "enc.pdf")
    pdf_ops.set_passwords(src, out, user_pw="u", owner_pw="o",
                          allow_print=True, allow_copy=False, allow_modify=False)
    reader = PdfReader(out)
    reader.decrypt("u")
    perms = reader.user_access_permissions
    assert perms & UAP.PRINT
    assert not perms & UAP.EXTRACT
    assert not perms & UAP.MODIFY
    assert not perms & UAP.ASSEMBLE_DOC

    out2 = str(tmp_path / "enc2.pdf")
    pdf_ops.set_passwords(src, out2, user_pw="u", allow_print=False,
                          allow_copy=True, allow_modify=True)
    reader2 = PdfReader(out2)
    reader2.decrypt("u")
    perms2 = reader2.user_access_permissions
    assert not perms2 & UAP.PRINT
    assert perms2 & UAP.EXTRACT
    assert perms2 & UAP.MODIFY


def test_set_passwords_owner_defaults_to_user(tmp_path):
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=1)
    out = str(tmp_path / "enc.pdf")
    pdf_ops.set_passwords(src, out, user_pw="only")
    reader = PdfReader(out)
    assert int(reader.decrypt("only")) != 0
    assert len(reader.pages) == 1


def test_set_passwords_owner_only_opens_without_password(tmp_path):
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=1)
    out = str(tmp_path / "enc.pdf")
    pdf_ops.set_passwords(src, out, owner_pw="boss")
    reader = PdfReader(out)
    assert reader.is_encrypted
    assert int(reader.decrypt("")) != 0
    assert len(reader.pages) == 1


def test_set_passwords_requires_a_password(tmp_path):
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=1)
    with pytest.raises(ValueError):
        pdf_ops.set_passwords(src, str(tmp_path / "out.pdf"))


def test_remove_password(tmp_path):
    enc = fixtures.make_encrypted_pdf(tmp_path / "enc.pdf", user_password="geheim", pages=2)
    out = str(tmp_path / "plain.pdf")

    pdf_ops.remove_password(enc, "geheim", out)

    reader = PdfReader(out)
    assert not reader.is_encrypted
    assert len(reader.pages) == 2


def test_remove_password_wrong_password_raises(tmp_path):
    enc = fixtures.make_encrypted_pdf(tmp_path / "enc.pdf", user_password="geheim")
    with pytest.raises(ValueError):
        pdf_ops.remove_password(enc, "falsch", str(tmp_path / "plain.pdf"))


# ---------------------------------------------------------------------------
# sanitize
# ---------------------------------------------------------------------------

def _make_dirty_pdf(tmp_path):
    """PDF with info dict, XMP, JavaScript, OpenAction, attachment, annots."""
    src = fixtures.make_pdf(tmp_path / "clean_src.pdf", pages=2)
    writer = PdfWriter(clone_from=src)
    writer.add_metadata({"/Title": "Secret Title", "/Author": "Anonymous"})
    writer.add_js("app.alert('boo');")  # also sets /OpenAction
    writer.add_attachment("notes.txt", b"attached data")
    writer.add_annotation(0, Link(rect=(50, 50, 150, 100), url="https://example.com"))
    # XMP metadata stream
    xmp = StreamObject()
    xmp.set_data(b"<x:xmpmeta xmlns:x='adobe:ns:meta/'></x:xmpmeta>")
    xmp[NameObject("/Type")] = NameObject("/Metadata")
    xmp[NameObject("/Subtype")] = NameObject("/XML")
    writer._root_object[NameObject("/Metadata")] = writer._add_object(xmp)
    # document-level additional actions
    writer._root_object[NameObject("/AA")] = DictionaryObject({
        NameObject("/WC"): DictionaryObject({
            NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): TextStringObject("app.alert('bye');"),
        })
    })
    dirty = str(tmp_path / "dirty.pdf")
    writer.write(dirty)
    return dirty


def test_sanitize_removes_everything(tmp_path):
    dirty = _make_dirty_pdf(tmp_path)
    out = str(tmp_path / "sane.pdf")

    report = pdf_ops.sanitize(dirty, out)

    assert isinstance(report, dict)
    removed = report["removed"]
    assert removed and all(isinstance(item, str) for item in removed)
    assert len(removed) >= 5

    reader = PdfReader(out)
    root = reader.trailer["/Root"]
    assert "/Metadata" not in root
    assert "/OpenAction" not in root
    assert "/AA" not in root
    names = root.get("/Names")
    if names is not None:
        names = names.get_object()
        assert "/JavaScript" not in names
        assert "/EmbeddedFiles" not in names
    assert not reader.metadata  # info dictionary gone or empty
    for page in reader.pages:
        assert "/Annots" not in page
    # Content is untouched
    assert "Page 1" in reader.pages[0].extract_text()


def test_sanitize_selective_flags(tmp_path):
    dirty = _make_dirty_pdf(tmp_path)
    out = str(tmp_path / "partial.pdf")

    report = pdf_ops.sanitize(dirty, out, strip_annotations=False, strip_metadata=False)

    reader = PdfReader(out)
    root = reader.trailer["/Root"]
    # annotations and metadata kept
    assert "/Annots" in reader.pages[0]
    assert reader.metadata is not None
    assert reader.metadata.title == "Secret Title"
    # javascript / attachments stripped
    assert "/OpenAction" not in root
    names = root.get("/Names")
    if names is not None:
        names = names.get_object()
        assert "/JavaScript" not in names
        assert "/EmbeddedFiles" not in names
    assert not any("annotation" in item for item in report["removed"])


def test_sanitize_encrypted_input(tmp_path):
    enc = fixtures.make_encrypted_pdf(tmp_path / "enc.pdf", user_password="pw", pages=1)
    out = str(tmp_path / "sane.pdf")
    report = pdf_ops.sanitize(enc, out, password="pw")
    assert isinstance(report["removed"], list)
    assert not PdfReader(out).is_encrypted


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------

def test_write_and_read_properties_unicode_roundtrip(tmp_path):
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=3)
    out = str(tmp_path / "meta.pdf")

    pdf_ops.write_properties(src, out, {
        "title": "Résumé — 履歴書",
        "author": "Björn Größé",
        "subject": "Ünïcode ✓",
        "keywords": "秘密, prüfung",
        "creator": "WorkOnward Read tests",
        "created": datetime(2026, 1, 2, 3, 4, 5),
    })

    props = pdf_ops.read_properties(out)
    assert props["title"] == "Résumé — 履歴書"
    assert props["author"] == "Björn Größé"
    assert props["subject"] == "Ünïcode ✓"
    assert props["keywords"] == "秘密, prüfung"
    assert props["creator"] == "WorkOnward Read tests"
    assert "2026" in str(props["created"])
    assert props["pages"] == 3
    assert props["encrypted"] is False
    width, height = props["page_size_pt"]
    assert width == pytest.approx(595.28, abs=0.1)
    assert height == pytest.approx(841.89, abs=0.1)


def test_write_properties_rejects_unknown_key(tmp_path):
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=1)
    with pytest.raises(ValueError):
        pdf_ops.write_properties(src, str(tmp_path / "out.pdf"), {"nonsense": "x"})


def test_read_properties_encrypted(tmp_path):
    enc = fixtures.make_encrypted_pdf(tmp_path / "enc.pdf", user_password="pw", pages=2)
    props = pdf_ops.read_properties(enc, password="pw")
    assert props["encrypted"] is True
    assert props["pages"] == 2
    with pytest.raises(ValueError):
        pdf_ops.read_properties(enc, password="wrong")
    with pytest.raises(ValueError):
        pdf_ops.read_properties(enc)


def test_zero_page_pdf(tmp_path):
    empty = str(tmp_path / "empty.pdf")
    PdfWriter().write(empty)

    props = pdf_ops.read_properties(empty)
    assert props["pages"] == 0
    assert props["page_size_pt"] is None

    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=3)
    out = str(tmp_path / "merged.pdf")
    assert pdf_ops.merge_pdfs([src, empty], out) == 3


def test_read_properties_missing_file():
    with pytest.raises(FileNotFoundError):
        pdf_ops.read_properties("/nonexistent/nowhere.pdf")


# ---------------------------------------------------------------------------
# rotate_pages
# ---------------------------------------------------------------------------

def test_rotate_pages(tmp_path):
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=3)
    out = str(tmp_path / "rot.pdf")

    pdf_ops.rotate_pages(src, out, {0: 90, 2: 180})

    reader = PdfReader(out)
    assert reader.pages[0].rotation % 360 == 90
    assert reader.pages[1].rotation % 360 == 0
    assert reader.pages[2].rotation % 360 == 180


def test_rotate_pages_encrypted_and_invalid(tmp_path):
    enc = fixtures.make_encrypted_pdf(tmp_path / "enc.pdf", user_password="pw", pages=1)
    out = str(tmp_path / "rot.pdf")
    pdf_ops.rotate_pages(enc, out, {0: 270}, password="pw")
    assert PdfReader(out).pages[0].rotation % 360 == 270

    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=1)
    with pytest.raises(ValueError):
        pdf_ops.rotate_pages(src, out, {0: 45})
    with pytest.raises(ValueError):
        pdf_ops.rotate_pages(src, out, {7: 90})
