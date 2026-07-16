"""
Digital signature operations for CoverUP PDF (pyHanko backend).

Provides certificate-based PDF signing (PKCS#12 credentials, invisible or
visible signature fields) and signature validation. All pyHanko imports
happen lazily inside the functions so that a broken or missing pyHanko
installation degrades to a clear RuntimeError instead of breaking module
import for unrelated features.

Coordinates for visible signatures arrive in ORIGINAL-image pixel space at
200 PPI, y-down (see docs/dev-architecture.md); they are converted to PDF
points with the y-axis flipped internally.

This module is a business module: it must not import FreeSimpleGUI or
tkinter and contains no user-visible (translated) strings.

License: GPL-3.0
(c) 2024 - 2026 Björn Seipel
Acrobat-suite additions (c) 2026 CoverUP contributors
"""

from typing import Optional, Union

# Pixel <-> point conversion at the canonical import resolution (200 PPI).
IMPORT_PPI = 200


def _px_to_pt(value_px: float) -> float:
    """Convert a length from 200-PPI pixels to PDF points."""
    return float(value_px) * 72.0 / IMPORT_PPI


def _import_pyhanko():
    """Import the pyHanko pieces needed for signing.

    Returns:
        Tuple (signers, fields, IncrementalPdfFileWriter).

    Raises:
        RuntimeError: If pyHanko is missing or broken.
    """
    try:
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.sign import fields, signers
    except Exception as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            f"pyHanko is not available or failed to import: {exc}"
        ) from exc
    return signers, fields, IncrementalPdfFileWriter


def _import_pyhanko_validation():
    """Import the pyHanko pieces needed for validation.

    Returns:
        Tuple (PdfFileReader, validate_pdf_signature, ValidationContext,
        load_certs_from_pemder).

    Raises:
        RuntimeError: If pyHanko is missing or broken.
    """
    try:
        from pyhanko.keys import load_certs_from_pemder
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature
        from pyhanko_certvalidator import ValidationContext
    except Exception as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            f"pyHanko is not available or failed to import: {exc}"
        ) from exc
    return PdfFileReader, validate_pdf_signature, ValidationContext, load_certs_from_pemder


def _page_height_pt(input_path: str, page_index: int, password: Optional[str]) -> float:
    """Return the mediabox height (points) of page `page_index`.

    Args:
        input_path: Path to the PDF file.
        page_index: Zero-based page index.
        password: Optional password for encrypted files.

    Raises:
        IndexError: If page_index is out of range.
        ValueError: If the file is encrypted and no valid password was given.
    """
    from pypdf import PdfReader

    reader = PdfReader(input_path)
    if reader.is_encrypted:
        if not reader.decrypt(password or ""):
            raise ValueError("The PDF is encrypted and the password is missing or wrong.")
    if page_index < 0 or page_index >= len(reader.pages):
        raise IndexError(
            f"Page index {page_index} out of range (document has {len(reader.pages)} pages)."
        )
    return float(reader.pages[page_index].mediabox.height)


def sign_pdf(
    input: str,
    output: str,
    p12_path: str,
    p12_password: Union[bytes, str],
    field_name: str = "Signature1",
    reason: Optional[str] = None,
    location: Optional[str] = None,
    contact: Optional[str] = None,
    visible: Optional[dict] = None,
    password: Optional[str] = None,
) -> str:
    """Digitally sign a PDF with a PKCS#12 (.p12/.pfx) credential.

    Args:
        input: Path to the source PDF.
        output: Path for the signed PDF (written as an incremental update).
        p12_path: Path to the PKCS#12 file containing key + certificate.
        p12_password: Passphrase for the PKCS#12 file (str or bytes).
        field_name: Name of the signature field to create or fill.
        reason: Optional signing reason recorded in the signature.
        location: Optional signing location recorded in the signature.
        contact: Optional contact info recorded in the signature.
        visible: None for an invisible signature, or a dict
            {'page_index': int, 'rect_px': [x0, y0, x1, y1]} with the widget
            rectangle in 200-PPI, y-down image pixels. Converted to PDF
            points (y-up) internally.
        password: Password for encrypted input PDFs.

    Returns:
        The output path.

    Raises:
        RuntimeError: If pyHanko is unavailable.
        ValueError: On bad credentials, bad passwords or malformed `visible`.
    """
    signers, fields, IncrementalPdfFileWriter = _import_pyhanko()

    if isinstance(p12_password, str):
        p12_password = p12_password.encode("utf-8")

    try:
        signer = signers.SimpleSigner.load_pkcs12(
            pfx_file=p12_path, passphrase=p12_password
        )
    except Exception as exc:
        raise ValueError(f"Could not load PKCS#12 credentials: {exc}") from exc
    if signer is None:
        raise ValueError(
            "Could not load PKCS#12 credentials (wrong passphrase or unusable file)."
        )

    new_field_spec = None
    if visible is not None:
        try:
            page_index = int(visible["page_index"])
            x0, y0, x1, y1 = (float(v) for v in visible["rect_px"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "visible must be a dict with 'page_index' and 'rect_px' [x0, y0, x1, y1]."
            ) from exc
        page_h_pt = _page_height_pt(input, page_index, password)
        left_px, right_px = min(x0, x1), max(x0, x1)
        top_px, bottom_px = min(y0, y1), max(y0, y1)
        # y-down px -> y-up pt: lower edge of the box comes from the larger px y.
        box = (
            _px_to_pt(left_px),
            page_h_pt - _px_to_pt(bottom_px),
            _px_to_pt(right_px),
            page_h_pt - _px_to_pt(top_px),
        )
        new_field_spec = fields.SigFieldSpec(
            sig_field_name=field_name, on_page=page_index, box=box
        )

    meta = signers.PdfSignatureMetadata(
        field_name=field_name,
        reason=reason,
        location=location,
        contact_info=contact,
    )
    pdf_signer = signers.PdfSigner(meta, signer=signer, new_field_spec=new_field_spec)

    with open(input, "rb") as in_fh:
        writer = IncrementalPdfFileWriter(in_fh, strict=False)
        if writer.security_handler is not None:
            if password is None:
                raise ValueError(
                    "The PDF is encrypted and the password is missing or wrong."
                )
            writer.encrypt(password)
        with open(output, "wb") as out_fh:
            pdf_signer.sign_pdf(writer, output=out_fh)
    return output


def _signer_common_name(embedded_sig) -> Optional[str]:
    """Best-effort extraction of the signer certificate's common name."""
    try:
        cert = embedded_sig.signer_cert
        if cert is None:
            return None
        subject = cert.subject.native
        return subject.get("common_name") or None
    except Exception:
        return None


def _signing_time_iso(embedded_sig) -> Optional[str]:
    """Best-effort ISO-8601 string of the signer-reported signing time."""
    try:
        timestamp = embedded_sig.self_reported_timestamp
        return timestamp.isoformat() if timestamp is not None else None
    except Exception:
        return None


def validate_signatures(
    input: str,
    extra_trust_roots: Optional[list] = None,
    password: Optional[str] = None,
) -> list:
    """Validate every digital signature in a PDF.

    Never raises because a signature is broken, untrusted or self-signed;
    per-signature problems are reported honestly in the result fields.

    Args:
        input: Path to the PDF file.
        extra_trust_roots: Optional list of paths to PEM or DER certificate
            files to use as trust anchors. When omitted, an empty trust list
            is used, so `trusted` will be False (but `intact` is still
            evaluated correctly).
        password: Password for encrypted input PDFs.

    Returns:
        A list with one dict per signature:
        {field_name, signer_cn, signing_time_iso, intact, valid, trusted,
        modification_level, summary}. `intact` means the document digest
        matches the signed digest; `valid` means the signature is
        cryptographically valid over THIS document (i.e. intact and the
        CMS signature verifies); `trusted` means the signer chains to one
        of the given trust roots.

    Raises:
        RuntimeError: If pyHanko is unavailable.
        ValueError: If the file is encrypted and the password is missing/wrong.
    """
    (
        PdfFileReader,
        validate_pdf_signature,
        ValidationContext,
        load_certs_from_pemder,
    ) = _import_pyhanko_validation()

    trust_roots = []
    for cert_path in extra_trust_roots or []:
        try:
            trust_roots.extend(load_certs_from_pemder([cert_path]))
        except Exception as exc:
            raise ValueError(
                f"Could not load trust root certificate '{cert_path}': {exc}"
            ) from exc

    validation_context = ValidationContext(
        trust_roots=trust_roots, allow_fetching=False
    )

    results = []
    with open(input, "rb") as fh:
        reader = PdfFileReader(fh, strict=False)
        if reader.security_handler is not None:
            try:
                auth = reader.decrypt(password or "")
                failed = getattr(auth, "status", None) is not None and (
                    auth.status.name == "FAILED"
                )
            except Exception as exc:
                raise ValueError(
                    "The PDF is encrypted and the password is missing or wrong."
                ) from exc
            if failed:
                raise ValueError(
                    "The PDF is encrypted and the password is missing or wrong."
                )
        for embedded_sig in reader.embedded_signatures:
            entry = {
                "field_name": embedded_sig.field_name,
                "signer_cn": _signer_common_name(embedded_sig),
                "signing_time_iso": _signing_time_iso(embedded_sig),
                "intact": False,
                "valid": False,
                "trusted": False,
                "modification_level": None,
                "summary": "",
            }
            try:
                status = validate_pdf_signature(
                    embedded_sig, signer_validation_context=validation_context
                )
                entry["intact"] = bool(status.intact)
                # pyhanko's `valid` only covers the CMS signature bytes; a
                # tampered document keeps valid=True with intact=False. The
                # user-facing meaning is "valid over this document".
                entry["valid"] = bool(status.intact and status.valid)
                entry["trusted"] = bool(status.trusted)
                if status.modification_level is not None:
                    entry["modification_level"] = status.modification_level.name
                try:
                    entry["summary"] = status.summary()
                except Exception:
                    entry["summary"] = "INTACT" if status.intact else "INVALID"
            except Exception as exc:
                entry["summary"] = f"Validation failed: {exc}"
            # signer_cn/signing time may only be parseable after validation
            # touched the CMS structures; retry if still unknown.
            if entry["signer_cn"] is None:
                entry["signer_cn"] = _signer_common_name(embedded_sig)
            if entry["signing_time_iso"] is None:
                entry["signing_time_iso"] = _signing_time_iso(embedded_sig)
            results.append(entry)
    return results
