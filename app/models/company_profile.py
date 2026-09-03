from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.db.base import Base
from .enums import state_check_constraint


class CompanyProfile(Base):
    __tablename__ = "company_profile"
    __table_args__ = (state_check_constraint("company_profile"),)

    id = Column(Integer, primary_key=True, index=True)
    legal_name = Column(String, nullable=False, default="Ascend Foods")
    gstin = Column(String)
    address_line1 = Column(String)
    address_line2 = Column(String)
    city = Column(String)
    state = Column(String)
    pincode = Column(String)
    phone = Column(String)
    email = Column(String)
    invoice_prefix = Column(String, nullable=False, default="ASC")
    invoice_footer = Column(String)

    # Payment instructions. These are the *current* values; each invoice freezes its own
    # copy at issuance (see app/models/invoice.py), because invoices.pdf_sha256 pins the
    # rendered bytes and changing a bank account must not rewrite historical documents.
    bank_name = Column(String)
    bank_account_name = Column(String)
    bank_account_number = Column(String)
    bank_ifsc = Column(String)
    bank_branch = Column(String)
    payment_qr_image_id = Column(Integer, ForeignKey("payment_qr_images.id", ondelete="RESTRICT"))

    payment_qr_image = relationship("PaymentQRImage")
