from sqlalchemy import Column, Integer, String

from app.db.base import Base


class CompanyProfile(Base):
    __tablename__ = "company_profile"

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
    invoice_next_number = Column(Integer, nullable=False, default=1)
    invoice_footer = Column(String)
