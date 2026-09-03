"""Bank details and the payment QR are frozen onto the invoice at issuance, not read live.

The load-bearing property: invoices.pdf_sha256 is the proof that a regenerated PDF
reproduces the issue-time render. If the PDF drew payment details from the live
CompanyProfile, changing a bank account or replacing the QR would silently invalidate the
digest of every invoice ever issued. These tests pin that it does not.
"""
import hashlib
from io import BytesIO

from PIL import Image as PILImage

from app.models import CompanyProfile, Invoice, PaymentQRImage
from app.services.admin import set_payment_qr_image
from app.services.invoice_pdf import regenerate_invoice_pdf, render_invoice_pdf

from test_invoice_pdf import _dispatched_order

def _png(color):
    """A real, decodable PNG. Generated rather than hardcoded as hex so the test cannot
    be broken by a typo in a byte string, and so the two images genuinely differ."""
    buf = BytesIO()
    PILImage.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


PNG_A = _png((0, 0, 0))
PNG_B = _png((255, 0, 0))
CORRUPT_PNG = PNG_A[:20] + b"garbage-not-an-image"


def _profile_with_payment_details(db, qr_bytes=PNG_A):
    profile = db.query(CompanyProfile).order_by(CompanyProfile.id.asc()).first()
    if profile is None:
        profile = CompanyProfile(legal_name="Ascend Foods", invoice_prefix="ASC")
        db.add(profile)
    profile.legal_name = "Ascend Foods"
    profile.gstin = "06DLXPB3523G1Z3"
    profile.state = "Haryana"
    profile.bank_name = "Yes Bank"
    profile.bank_account_name = "Ascend Foods"
    profile.bank_account_number = "1234567890"
    profile.bank_ifsc = "YESB0000123"
    profile.bank_branch = "Gurugram"
    db.commit()
    set_payment_qr_image(db, qr_bytes, "image/png")
    db.refresh(profile)
    return profile


def test_invoice_freezes_the_bank_details_and_qr_at_issuance(db):
    profile = _profile_with_payment_details(db)
    _, invoice = _dispatched_order(db)

    assert invoice.supplier_bank_name == "Yes Bank"
    assert invoice.supplier_bank_account_name == "Ascend Foods"
    assert invoice.supplier_bank_account_number == "1234567890"
    assert invoice.supplier_bank_ifsc == "YESB0000123"
    assert invoice.supplier_bank_branch == "Gurugram"
    assert invoice.payment_qr_image_id == profile.payment_qr_image_id
    assert invoice.payment_qr_image_id is not None


def test_changing_the_bank_or_qr_does_not_alter_an_issued_invoice(db):
    """The whole reason these columns exist. Change everything on the profile, then prove
    the issued invoice and its regenerated PDF are untouched."""
    _profile_with_payment_details(db, qr_bytes=PNG_A)
    _, invoice = _dispatched_order(db)

    frozen_account = invoice.supplier_bank_account_number
    frozen_qr_id = invoice.payment_qr_image_id
    digest_at_issue = invoice.pdf_sha256
    assert digest_at_issue

    # Move to a different bank and a different QR image.
    profile = db.query(CompanyProfile).order_by(CompanyProfile.id.asc()).first()
    profile.bank_name = "Some Other Bank"
    profile.bank_account_number = "9999999999"
    profile.bank_ifsc = "OTHR0000999"
    db.commit()
    set_payment_qr_image(db, PNG_B, "image/png")
    db.refresh(profile)

    assert profile.bank_account_number == "9999999999"
    assert profile.payment_qr_image_id != frozen_qr_id

    db.refresh(invoice)
    assert invoice.supplier_bank_account_number == frozen_account
    assert invoice.payment_qr_image_id == frozen_qr_id

    regenerated, _ = regenerate_invoice_pdf(db, invoice)
    assert hashlib.sha256(regenerated.getvalue()).hexdigest() == digest_at_issue


def test_the_qr_image_bytes_are_embedded_in_the_rendered_pdf(db):
    _profile_with_payment_details(db, qr_bytes=PNG_A)
    _, invoice = _dispatched_order(db)
    pdf = render_invoice_pdf(invoice).getvalue()
    # reportlab re-encodes the raster, so the PNG container bytes are not searchable.
    # An embedded image always produces an XObject of subtype /Image; without a QR the
    # invoice PDF contains no images at all (asserted by the next test).
    assert b"/Subtype /Image" in pdf or b"/Subtype/Image" in pdf


def test_an_invoice_with_no_payment_details_renders_without_a_payment_block(db):
    """Invoices issued before any payment details existed must be unaffected."""
    profile = db.query(CompanyProfile).order_by(CompanyProfile.id.asc()).first()
    if profile is None:
        profile = CompanyProfile(legal_name="Ascend Foods", invoice_prefix="ASC")
        db.add(profile)
        db.commit()

    _, invoice = _dispatched_order(db)
    assert invoice.payment_qr_image_id is None
    assert invoice.supplier_bank_name is None

    pdf = render_invoice_pdf(invoice).getvalue()
    assert b"/Subtype /Image" not in pdf and b"/Subtype/Image" not in pdf
    # And it still renders and still matches its issue-time digest.
    regenerated, _ = regenerate_invoice_pdf(db, invoice)
    assert hashlib.sha256(regenerated.getvalue()).hexdigest() == invoice.pdf_sha256


def test_reuploading_an_identical_qr_reuses_the_stored_row(db):
    """Append-only with digest identity: the same file must not pile up duplicate rows,
    because each stored image is retained forever for the invoices that reference it."""
    _profile_with_payment_details(db, qr_bytes=PNG_A)
    first_id = db.query(CompanyProfile).first().payment_qr_image_id

    set_payment_qr_image(db, PNG_A, "image/png")
    assert db.query(CompanyProfile).first().payment_qr_image_id == first_id
    assert db.query(PaymentQRImage).count() == 1

    set_payment_qr_image(db, PNG_B, "image/png")
    assert db.query(PaymentQRImage).count() == 2


def test_qr_upload_rejects_oversized_and_non_image_payloads(db):
    import pytest

    with pytest.raises(ValueError, match="PNG, JPEG or GIF"):
        set_payment_qr_image(db, PNG_A, "application/pdf")
    with pytest.raises(ValueError, match="2 MB"):
        set_payment_qr_image(db, b"x" * (2 * 1024 * 1024 + 1), "image/png")
    with pytest.raises(ValueError, match="empty"):
        set_payment_qr_image(db, b"", "image/png")


def test_a_corrupt_stored_qr_does_not_make_the_invoice_unrenderable(db):
    """An invoice is a legal record; the QR is a convenience. A stored image that cannot
    be decoded must be dropped from the layout, not raise out of doc.build()."""
    _profile_with_payment_details(db, qr_bytes=PNG_A)
    _, invoice = _dispatched_order(db)

    # Corrupt the stored bytes behind the invoice's back.
    image = db.query(PaymentQRImage).filter(PaymentQRImage.id == invoice.payment_qr_image_id).one()
    image.image_bytes = CORRUPT_PNG
    db.commit()
    db.expire_all()
    invoice = db.query(Invoice).filter(Invoice.id == invoice.id).one()

    pdf = render_invoice_pdf(invoice).getvalue()
    assert pdf.startswith(b"%PDF")
    # The bank rows still render; only the image is dropped.
    assert b"/Subtype /Image" not in pdf and b"/Subtype/Image" not in pdf
