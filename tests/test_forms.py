"""Tests for workonward_read.forms (pypdf AcroForm listing and filling).

Form fixtures are synthesized at test time — no binary fixtures.

License: GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

import io

import fixtures
from fixtures import runtime_pw
import pytest
from pypdf import PdfReader, PdfWriter

from workonward_read import forms


def _make_form_with_checkbox(path):
    """Create a one-page PDF with two text fields and one checkbox.

    Mirrors fixtures.make_form_pdf but adds an 'agree' checkbox with a
    proper /AP appearance dictionary (Yes/Off states).
    """
    from fpdf import FPDF
    from pypdf.generic import (
        ArrayObject,
        BooleanObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    plain = io.BytesIO()
    pdf = FPDF(unit="pt")
    pdf.add_page()
    plain.write(pdf.output())
    plain.seek(0)

    writer = PdfWriter()
    writer.append(PdfReader(plain))
    page = writer.pages[0]
    page[NameObject("/Annots")] = ArrayObject()
    field_refs = ArrayObject()

    def _text_field(name, rect):
        annot = DictionaryObject(
            {
                NameObject("/FT"): NameObject("/Tx"),
                NameObject("/T"): TextStringObject(name),
                NameObject("/V"): TextStringObject(""),
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/Rect"): ArrayObject(NumberObject(v) for v in rect),
                NameObject("/Ff"): NumberObject(0),
            }
        )
        ref = writer._add_object(annot)
        annot[NameObject("/P")] = page.indirect_reference
        page["/Annots"].append(ref)
        field_refs.append(ref)

    _text_field("name", [200, 700, 400, 720])
    _text_field("city", [200, 660, 400, 680])

    def _ap_stream():
        stream = DecodedStreamObject()
        stream.set_data(b"")
        stream[NameObject("/Type")] = NameObject("/XObject")
        stream[NameObject("/Subtype")] = NameObject("/Form")
        stream[NameObject("/BBox")] = ArrayObject(
            NumberObject(v) for v in (0, 0, 16, 16)
        )
        return writer._add_object(stream)

    checkbox = DictionaryObject(
        {
            NameObject("/FT"): NameObject("/Btn"),
            NameObject("/T"): TextStringObject("agree"),
            NameObject("/V"): NameObject("/Off"),
            NameObject("/AS"): NameObject("/Off"),
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/Rect"): ArrayObject(
                NumberObject(v) for v in (200, 620, 216, 636)
            ),
            NameObject("/Ff"): NumberObject(0),
            NameObject("/AP"): DictionaryObject(
                {
                    NameObject("/N"): DictionaryObject(
                        {
                            NameObject("/Yes"): _ap_stream(),
                            NameObject("/Off"): _ap_stream(),
                        }
                    )
                }
            ),
        }
    )
    ref = writer._add_object(checkbox)
    checkbox[NameObject("/P")] = page.indirect_reference
    page["/Annots"].append(ref)
    field_refs.append(ref)

    writer._root_object[NameObject("/AcroForm")] = DictionaryObject(
        {
            NameObject("/Fields"): field_refs,
            NameObject("/NeedAppearances"): BooleanObject(True),
        }
    )
    with open(path, "wb") as fh:
        writer.write(fh)
    return str(path)


def _encrypt(src, dst, password):
    writer = PdfWriter()
    writer.append(PdfReader(src))
    writer.encrypt(user_password=password, algorithm="AES-256")
    with open(dst, "wb") as fh:
        writer.write(fh)
    return str(dst)


def test_list_fields_names_and_types(tmp_path):
    src = fixtures.make_form_pdf(tmp_path / "form.pdf")
    listed = forms.list_fields(src)
    by_name = {f["name"]: f for f in listed}
    assert set(by_name) == {"name", "city"}
    for field in listed:
        assert field["type"] == "text"
        assert field["page_index"] == 0
        assert field["read_only"] is False
        assert field["options"] is None
        assert field["value"] in (None, "")


def test_list_fields_no_form(tmp_path):
    src = fixtures.make_pdf(tmp_path / "plain.pdf", pages=1)
    assert forms.list_fields(src) == []


def test_fill_fields_and_read_back(tmp_path):
    src = fixtures.make_form_pdf(tmp_path / "form.pdf")
    out = str(tmp_path / "filled.pdf")
    result = forms.fill_fields(
        src, out, {"name": "Jane Döe", "city": "Zürich – 東京"}
    )
    assert result == out

    read_back = PdfReader(out).get_fields()
    assert read_back["name"].value == "Jane Döe"
    assert read_back["city"].value == "Zürich – 東京"

    # NeedAppearances must be set so viewers regenerate appearances.
    root = PdfReader(out).trailer["/Root"]
    assert bool(root["/AcroForm"]["/NeedAppearances"]) is True

    # list_fields sees the new values, still editable.
    by_name = {f["name"]: f for f in forms.list_fields(out)}
    assert by_name["name"]["value"] == "Jane Döe"
    assert by_name["name"]["read_only"] is False


def test_fill_unknown_field_raises_with_names(tmp_path):
    src = fixtures.make_form_pdf(tmp_path / "form.pdf")
    out = str(tmp_path / "filled.pdf")
    with pytest.raises(ValueError) as excinfo:
        forms.fill_fields(src, out, {"name": "ok", "bogus": "x", "nope": "y"})
    message = str(excinfo.value)
    assert message.startswith("Unknown form field name(s): ")
    assert "bogus" in message
    assert "nope" in message
    # Known field names are not listed as unknown.
    assert "name" not in message.split(": ", 1)[1].split(", ")


def test_flatten_sets_read_only(tmp_path):
    src = fixtures.make_form_pdf(tmp_path / "form.pdf")
    out = str(tmp_path / "flat.pdf")
    forms.fill_fields(src, out, {"name": "Final"}, flatten=True)

    listed = forms.list_fields(out)
    assert listed, "fields should survive flattening"
    for field in listed:
        assert field["read_only"] is True
    by_name = {f["name"]: f for f in listed}
    assert by_name["name"]["value"] == "Final"


def test_checkbox_bool_fill(tmp_path):
    src = _make_form_with_checkbox(tmp_path / "form.pdf")

    by_name = {f["name"]: f for f in forms.list_fields(src)}
    assert by_name["agree"]["type"] == "checkbox"
    assert by_name["agree"]["value"] is False
    assert by_name["agree"]["options"] == ["Yes"]

    out_on = str(tmp_path / "checked.pdf")
    forms.fill_fields(src, out_on, {"agree": True, "name": "A"})
    fields = PdfReader(out_on).get_fields()
    assert str(fields["agree"].value) == "/Yes"
    assert {f["name"]: f for f in forms.list_fields(out_on)}["agree"]["value"] is True

    out_off = str(tmp_path / "unchecked.pdf")
    forms.fill_fields(out_on, out_off, {"agree": False})
    assert {f["name"]: f for f in forms.list_fields(out_off)}["agree"]["value"] is False


def test_encrypted_form_password_handling(tmp_path):
    plain = fixtures.make_form_pdf(tmp_path / "form.pdf")
    enc = _encrypt(plain, tmp_path / "enc.pdf", runtime_pw("secret"))

    with pytest.raises(ValueError):
        forms.list_fields(enc)

    listed = forms.list_fields(enc, password=runtime_pw("secret"))
    assert {f["name"] for f in listed} == {"name", "city"}

    out = str(tmp_path / "filled.pdf")
    forms.fill_fields(enc, out, {"name": "Bob"}, password=runtime_pw("secret"))
    assert PdfReader(out).get_fields()["name"].value == "Bob"
