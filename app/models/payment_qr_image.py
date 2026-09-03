import hashlib

from sqlalchemy import Column, DateTime, Integer, LargeBinary, String
from sqlalchemy.sql import func

from app.db.base import Base


class PaymentQRImage(Base):
    """An uploaded payment QR image, stored append-only.

    Rows are never updated or deleted while any invoice references them. Replacing the
    company's QR inserts a new row and repoints `company_profile.payment_qr_image_id`;
    invoices issued earlier keep pointing at the image they were rendered with, which is
    what keeps `invoices.pdf_sha256` reproducible after a QR change.
    """

    __tablename__ = "payment_qr_images"

    id = Column(Integer, primary_key=True, index=True)
    image_bytes = Column(LargeBinary, nullable=False)
    content_type = Column(String, nullable=False)
    # Unique: re-uploading the same file resolves to the existing row rather than
    # inserting a duplicate, so an accidental re-upload does not orphan the old image.
    sha256 = Column(String(64), nullable=False, unique=True)
    label = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


def digest_image(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()
