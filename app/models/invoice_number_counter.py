from sqlalchemy import Column, Integer, String

from app.db.base import Base


class InvoiceNumberCounter(Base):
    """One row per invoice series (the brand segment of the number), holding the last
    serial issued in that series.

    Each brand runs its own consecutive 0001, 0002... series, so a single global
    sequence no longer works. The row is keyed on the derived *code*, not on brand_id:
    two brands whose names share a three-letter prefix collapse onto the same code, and
    sharing the counter is what keeps their invoice numbers from colliding.

    Allocation is a single atomic INSERT ... ON CONFLICT DO UPDATE ... RETURNING
    (app/services/invoice.py:_next_series_serial), never a read-modify-write in Python.
    """

    __tablename__ = "invoice_number_counters"

    series_code = Column(String, primary_key=True)
    last_serial = Column(Integer, nullable=False, default=0)
