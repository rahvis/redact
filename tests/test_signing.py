"""Tests for workonward_read.signing (pyHanko-based signing and validation).

All credentials are synthesized in-test with `cryptography` — no binary
fixtures.

License: GPL-3.0
(c) 2024 - 2026 Björn Seipel
(c) 2026 WorkOnward Read contributors
"""

from datetime import datetime, timedelta, timezone

import fixtures
from fixtures import runtime_pw
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from workonward_read import signing

TEST_CN = "WorkOnward Read Test Signer"
P12_PASSWORD = runtime_pw("p12").encode("ascii")


def _make_credentials(tmp_path, cn=TEST_CN, password=P12_PASSWORD):
    """Create a self-signed cert + RSA key, return (p12_path, pem_path)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, cn),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "WorkOnward Read Tests"),
        ]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,  # aka non_repudiation
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    p12_bytes = pkcs12.serialize_key_and_certificates(
        name=b"workonward_read-test",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password),
    )
    p12_path = tmp_path / "signer.p12"
    p12_path.write_bytes(p12_bytes)

    pem_path = tmp_path / "signer.pem"
    pem_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(p12_path), str(pem_path)


@pytest.fixture()
def credentials(tmp_path):
    return _make_credentials(tmp_path)


def test_sign_invisible_and_validate(tmp_path, credentials):
    p12_path, pem_path = credentials
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=2)
    out = str(tmp_path / "signed.pdf")

    result = signing.sign_pdf(
        src,
        out,
        p12_path,
        P12_PASSWORD,
        reason="Approval",
        location="Zurich",
    )
    assert result == out

    # Without trust roots: intact + cryptographically valid, but untrusted.
    reports = signing.validate_signatures(out)
    assert len(reports) == 1
    report = reports[0]
    assert report["field_name"] == "Signature1"
    assert report["intact"] is True
    assert report["valid"] is True
    assert report["trusted"] is False
    assert report["signer_cn"] == TEST_CN
    assert report["signing_time_iso"] is not None
    # sanity: parseable ISO timestamp
    datetime.fromisoformat(report["signing_time_iso"])
    assert isinstance(report["summary"], str) and report["summary"]

    # With the self-signed cert as extra trust root: trusted.
    trusted_reports = signing.validate_signatures(out, extra_trust_roots=[pem_path])
    assert trusted_reports[0]["intact"] is True
    assert trusted_reports[0]["valid"] is True
    assert trusted_reports[0]["trusted"] is True


def test_sign_str_password_and_custom_field_name(tmp_path, credentials):
    p12_path, _ = credentials
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=1)
    out = str(tmp_path / "signed.pdf")
    signing.sign_pdf(
        src, out, p12_path, P12_PASSWORD.decode("ascii"), field_name="MySig"
    )
    reports = signing.validate_signatures(out)
    assert reports[0]["field_name"] == "MySig"
    assert reports[0]["intact"] is True


def test_sign_visible_creates_widget_with_converted_rect(tmp_path, credentials):
    p12_path, _ = credentials
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=2)
    out = str(tmp_path / "signed.pdf")

    rect_px = [100, 100, 500, 300]  # 200-PPI px, y-down
    signing.sign_pdf(
        src,
        out,
        p12_path,
        P12_PASSWORD,
        visible={"page_index": 0, "rect_px": rect_px},
    )

    reports = signing.validate_signatures(out)
    assert reports[0]["intact"] is True
    assert reports[0]["valid"] is True

    # Verify the widget rectangle in pt with y-flip on page 0.
    from pypdf import PdfReader

    reader = PdfReader(out)
    page = reader.pages[0]
    page_h = float(page.mediabox.height)
    widgets = [
        a.get_object()
        for a in (page.get("/Annots") or [])
        if str(a.get_object().get("/Subtype", "")) == "/Widget"
    ]
    assert widgets, "visible signature widget missing on page 0"
    rect = [float(v) for v in widgets[0]["/Rect"]]
    scale = 72.0 / 200.0
    expected = [
        100 * scale,
        page_h - 300 * scale,
        500 * scale,
        page_h - 100 * scale,
    ]
    got = [min(rect[0], rect[2]), min(rect[1], rect[3]),
           max(rect[0], rect[2]), max(rect[1], rect[3])]
    for g, e in zip(got, expected):
        assert abs(g - e) < 0.5


def test_signature_metadata_recorded(tmp_path, credentials):
    p12_path, _ = credentials
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=1)
    out = str(tmp_path / "signed.pdf")
    signing.sign_pdf(
        src,
        out,
        p12_path,
        P12_PASSWORD,
        reason="Approved for release",
        location="Zürich",
        contact="signer@example.org",
    )
    from pypdf import PdfReader

    fields = PdfReader(out).get_fields()
    sig_value = fields["Signature1"].get("/V").get_object()
    assert str(sig_value.get("/Reason")) == "Approved for release"
    assert str(sig_value.get("/Location")) == "Zürich"
    assert str(sig_value.get("/ContactInfo")) == "signer@example.org"


def test_tampered_file_reports_not_intact(tmp_path, credentials):
    p12_path, _ = credentials
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=1)
    out = tmp_path / "signed.pdf"
    signing.sign_pdf(src, str(out), p12_path, P12_PASSWORD)

    data = bytearray(out.read_bytes())
    # Flip a byte inside the first content stream (inside the signed byte
    # range, but structurally harmless: stream length is unchanged).
    idx = data.find(b"stream")
    assert idx != -1
    target = idx + len(b"stream") + 4
    data[target] ^= 0x01
    tampered = tmp_path / "tampered.pdf"
    tampered.write_bytes(bytes(data))

    reports = signing.validate_signatures(str(tampered))
    assert len(reports) == 1
    assert reports[0]["intact"] is False
    assert reports[0]["valid"] is False


def test_sign_encrypted_input(tmp_path, credentials):
    p12_path, pem_path = credentials
    src = fixtures.make_encrypted_pdf(tmp_path / "enc.pdf", user_password=runtime_pw("secret"))
    out = str(tmp_path / "signed.pdf")

    # Missing password -> clear error, no output corruption.
    with pytest.raises(ValueError):
        signing.sign_pdf(src, out, p12_path, P12_PASSWORD)

    signing.sign_pdf(src, out, p12_path, P12_PASSWORD, password=runtime_pw("secret"))

    with pytest.raises(ValueError):
        signing.validate_signatures(out)  # password required

    reports = signing.validate_signatures(
        out, extra_trust_roots=[pem_path], password=runtime_pw("secret")
    )
    assert reports[0]["intact"] is True
    assert reports[0]["valid"] is True
    assert reports[0]["trusted"] is True


def test_bad_p12_password_raises_value_error(tmp_path, credentials):
    p12_path, _ = credentials
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=1)
    with pytest.raises(ValueError):
        signing.sign_pdf(
            src, str(tmp_path / "out.pdf"), p12_path, b"wrong-password"
        )


def test_visible_rect_validation_errors(tmp_path, credentials):
    p12_path, _ = credentials
    src = fixtures.make_pdf(tmp_path / "src.pdf", pages=1)
    with pytest.raises(ValueError):
        signing.sign_pdf(
            src, str(tmp_path / "out.pdf"), p12_path, P12_PASSWORD,
            visible={"rect_px": [0, 0, 10, 10]},  # page_index missing
        )
    with pytest.raises(IndexError):
        signing.sign_pdf(
            src, str(tmp_path / "out.pdf"), p12_path, P12_PASSWORD,
            visible={"page_index": 99, "rect_px": [0, 0, 10, 10]},
        )


def test_validate_unsigned_pdf_returns_empty_list(tmp_path):
    src = fixtures.make_pdf(tmp_path / "plain.pdf", pages=1)
    assert signing.validate_signatures(src) == []
