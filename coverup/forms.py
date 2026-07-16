"""
AcroForm operations for CoverUP PDF (pypdf backend).

Provides listing and filling of interactive PDF form fields (text fields,
checkboxes, radio buttons, choice fields, signature fields). Filling writes
a new document via pypdf; `flatten=True` additionally marks all form fields
read-only (sets bit 1 of /Ff) so the filled values can no longer be edited
in a viewer.

This module is a business module: it must not import FreeSimpleGUI or
tkinter and contains no user-visible (translated) strings.

License: GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

from typing import Optional

# /Ff flag bits (PDF 32000-1, table 221 ff.)
FF_READ_ONLY = 1
FF_RADIO = 1 << 15       # bit 16
FF_PUSHBUTTON = 1 << 16  # bit 17


def _open_reader(input_path: str, password: Optional[str]):
    """Open a PDF with pypdf, decrypting it when necessary.

    Raises:
        ValueError: If the file is encrypted and the password is missing/wrong.
    """
    from pypdf import PdfReader

    reader = PdfReader(input_path)
    if reader.is_encrypted:
        try:
            auth = reader.decrypt(password or "")
        except Exception as exc:
            raise ValueError(
                "The PDF is encrypted and the password is missing or wrong."
            ) from exc
        if not auth:
            raise ValueError(
                "The PDF is encrypted and the password is missing or wrong."
            )
    return reader


def _strip_name(value) -> Optional[str]:
    """Normalize a pypdf NameObject like '/Yes' to 'Yes'."""
    if value is None:
        return None
    text = str(value)
    return text[1:] if text.startswith("/") else text


def _field_type(field) -> str:
    """Map a raw field dict to one of the public type strings.

    Returns one of 'text', 'checkbox', 'radio', 'choice', 'signature',
    'other'.
    """
    ft = field.get("/FT")
    ft = str(ft) if ft is not None else None
    flags = int(field.get("/Ff") or 0)
    if ft == "/Tx":
        return "text"
    if ft == "/Btn":
        if flags & FF_PUSHBUTTON:
            return "other"
        if flags & FF_RADIO:
            return "radio"
        return "checkbox"
    if ft == "/Ch":
        return "choice"
    if ft == "/Sig":
        return "signature"
    return "other"


def _field_options(field, field_type: str) -> Optional[list]:
    """Extract the selectable options of a choice/radio/checkbox field."""
    if field_type == "choice":
        options = []
        for opt in field.get("/Opt") or []:
            opt = opt.get_object() if hasattr(opt, "get_object") else opt
            if isinstance(opt, (list, tuple)):
                # [export, display] pair -> display value
                options.append(str(opt[-1]))
            else:
                options.append(str(opt))
        return options or None
    if field_type in ("radio", "checkbox"):
        # pypdf collects the widget appearance states under /_States_.
        states = field.get("/_States_")
        if states:
            options = [
                _strip_name(state) for state in states if str(state) != "/Off"
            ]
            return options or None
    return None


def _field_value(field, field_type: str):
    """Normalize the /V entry for the public dict."""
    value = field.get("/V")
    if field_type == "checkbox":
        return _strip_name(value) not in (None, "Off", "")
    if field_type == "radio":
        name = _strip_name(value)
        return None if name in (None, "Off") else name
    if value is None:
        return None
    value = value.get_object() if hasattr(value, "get_object") else value
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return _strip_name(value) if str(value).startswith("/") else str(value)


def _qualified_widget_names(page) -> list:
    """Fully-qualified field names of all widget annotations on a page."""
    names = []
    for annot_ref in page.get("/Annots") or []:
        annot = annot_ref.get_object()
        if str(annot.get("/Subtype", "")) != "/Widget":
            continue
        parts = []
        node = annot
        seen = set()
        while node is not None and id(node) not in seen:
            seen.add(id(node))
            title = node.get("/T")
            if title:
                parts.append(str(title))
            parent = node.get("/Parent")
            node = parent.get_object() if parent is not None else None
        if parts:
            names.append(".".join(reversed(parts)))
    return names


def list_fields(input: str, password: Optional[str] = None) -> list:
    """List all AcroForm fields of a PDF.

    Args:
        input: Path to the PDF file.
        password: Password for encrypted input PDFs.

    Returns:
        A list with one dict per field:
        {name, type ('text'|'checkbox'|'radio'|'choice'|'signature'|'other'),
        value, options: list|None, page_index: int|None, read_only: bool}.
        Returns an empty list for PDFs without an AcroForm.
    """
    reader = _open_reader(input, password)
    raw_fields = reader.get_fields() or {}

    page_map: dict = {}
    for page_index, page in enumerate(reader.pages):
        for name in _qualified_widget_names(page):
            page_map.setdefault(name, page_index)

    results = []
    for name, field in raw_fields.items():
        field_type = _field_type(field)
        results.append(
            {
                "name": str(name),
                "type": field_type,
                "value": _field_value(field, field_type),
                "options": _field_options(field, field_type),
                "page_index": page_map.get(str(name)),
                "read_only": bool(int(field.get("/Ff") or 0) & FF_READ_ONLY),
            }
        )
    return results


def _checkbox_on_state(field) -> str:
    """Determine the 'on' appearance state name of a checkbox (e.g. '/Yes')."""
    for state in field.get("/_States_") or []:
        if str(state) != "/Off":
            return str(state)
    return "/Yes"


def _set_read_only(field_obj, seen: set) -> None:
    """Recursively set the read-only flag on a field and its kids."""
    from pypdf.generic import NameObject, NumberObject

    obj = field_obj.get_object()
    if id(obj) in seen:
        return
    seen.add(id(obj))
    flags = int(obj.get("/Ff") or 0)
    obj[NameObject("/Ff")] = NumberObject(flags | FF_READ_ONLY)
    for kid in obj.get("/Kids") or []:
        _set_read_only(kid, seen)


def fill_fields(
    input: str,
    output: str,
    values: dict,
    password: Optional[str] = None,
    flatten: bool = False,
) -> str:
    """Fill AcroForm field values and write the result to `output`.

    Args:
        input: Path to the source PDF.
        output: Path for the filled PDF.
        values: Mapping of fully-qualified field name -> new value. Checkbox
            fields accept bool values (True -> the widget's 'on' state,
            False -> '/Off').
        password: Password for encrypted input PDFs.
        flatten: When True, all form fields are additionally marked
            read-only (/Ff bit 1) after filling.

    Returns:
        The output path.

    Raises:
        ValueError: If `values` contains names that do not exist in the form
            (the message lists every unknown name), or on password problems.
    """
    from pypdf import PdfWriter
    from pypdf.generic import BooleanObject, NameObject

    reader = _open_reader(input, password)
    existing = reader.get_fields() or {}

    unknown = sorted(set(values) - set(str(k) for k in existing))
    if unknown:
        raise ValueError(
            "Unknown form field name(s): " + ", ".join(unknown)
        )

    prepared = {}
    for name, value in values.items():
        field = existing[name]
        field_type = _field_type(field)
        if field_type == "checkbox" and isinstance(value, bool):
            value = _checkbox_on_state(field) if value else "/Off"
        elif isinstance(value, bool):
            value = str(value)
        prepared[name] = value

    writer = PdfWriter()
    writer.append(reader)

    for page in writer.pages:
        if page.get("/Annots"):
            writer.update_page_form_field_values(
                page, prepared, auto_regenerate=False
            )

    acro_form = writer._root_object.get("/AcroForm")
    if acro_form is not None:
        acro_form = acro_form.get_object()
        acro_form[NameObject("/NeedAppearances")] = BooleanObject(True)
        if flatten:
            seen: set = set()
            for field_ref in acro_form.get("/Fields") or []:
                _set_read_only(field_ref, seen)

    with open(output, "wb") as fh:
        writer.write(fh)
    return output
