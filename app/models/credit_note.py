from sqlalchemy import CheckConstraint, Column, Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.db.base import Base

class CreditNote(Base):
    __tablename__ = "credit_notes"
    __table_args__ = (
        CheckConstraint("note_type IN ('C', 'D')", name="ck_credit_notes_note_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    credit_note_number = Column(String, unique=True, nullable=False)
    applies_to_outstanding = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Invoice reference (INV-06). Nullable: an order can reach DELIVERED with no invoice
    # via direct-status test fixtures or imported rows whose spreadsheet had no bill
    # number (see 02-05-PLAN.md planning evidence) — through the real lifecycle it is
    # always populated, since DELIVERED is only reachable via OUT_FOR_DELIVERY, where the
    # invoice is issued.
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True, index=True)
    # Snapshotted literals, not solely a join — the flat shape GSTR-1's CDNR table takes.
    # The invoice is immutable, so these can never disagree with the referenced row.
    original_invoice_number = Column(String, nullable=True)
    original_invoice_date = Column(DateTime(timezone=True), nullable=True)
    # 'C' credit note / 'D' debit note. Only 'C' is produced this phase; the column
    # exists so a debit note is a service change and not a migration (D-02).
    note_type = Column(String, nullable=False, default="C")
    # The document date, distinct from created_at (a row-insert timestamp) — mirrors
    # Invoice.invoice_date vs Invoice.created_at, so a backdated import can differ.
    note_date = Column(DateTime(timezone=True), nullable=True)

    order = relationship("Order", back_populates="credit_notes")
    items = relationship("CreditNoteItem", back_populates="credit_note", cascade="all, delete-orphan")
    # One-directional: no back_populates. Invoice must not gain a mutable collection —
    # appending here would be an ORM write path against an immutable object and would
    # trip the guard from plan 02-01 confusingly. A read-only reference is all INV-06
    # asks for.
    invoice = relationship("Invoice")
