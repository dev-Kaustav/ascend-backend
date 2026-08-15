from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.db.base import Base
from .enums import InvoiceStatus, InvoiceType, SupplyType, state_check_constraint

# There is no per-SKU unit of measure anywhere in this codebase today — app/models/sku.py
# has no unit column, the Excel import template has no unit column, and the UI has no unit
# field. Every invoice line is stamped with this constant until a real source exists.
# Correct only while Ascend sells whole pieces (see 02-CONTEXT.md Confirmed Data Gaps).
DEFAULT_UQC = "PCS"


def _enum_check_constraint(table_name: str, column: str, enum_cls) -> CheckConstraint:
    values_list = ", ".join(f"'{v.value}'" for v in enum_cls)
    return CheckConstraint(
        f"{column} IN ({values_list})",
        name=f"ck_{table_name}_{column}",
    )


class InvoiceImmutableError(Exception):
    """Raised when code attempts to UPDATE or DELETE a persisted Invoice/InvoiceLine.

    An issued invoice is a legal tax record (D-01, D-06, INV-03). This is the ORM-level
    mechanism, effective on every engine including the SQLite test engine. A PostgreSQL
    trigger (migration 0045) is the defence-in-depth mechanism for raw SQL that bypasses
    the ORM entirely.
    """


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        _enum_check_constraint("invoices", "status", InvoiceStatus),
        _enum_check_constraint("invoices", "invoice_type", InvoiceType),
        _enum_check_constraint("invoices", "supply_type", SupplyType),
        state_check_constraint("invoices", column="place_of_supply"),
        state_check_constraint("invoices", column="supplier_state"),
        state_check_constraint("invoices", column="buyer_state"),
    )

    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String, nullable=False, unique=True, index=True)
    invoice_serial = Column(Integer, nullable=True, unique=True)
    invoice_date = Column(DateTime(timezone=True), nullable=False)
    status = Column(String, nullable=False, default=InvoiceStatus.ISSUED.value)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, unique=True)

    # GSTR-1 / e-invoice document fields (INV-05, D-02)
    invoice_type = Column(String, nullable=False, default=InvoiceType.B2B.value)
    supply_type = Column(String, nullable=False, default=SupplyType.REGULAR.value)
    reverse_charge = Column(Boolean, nullable=False, default=False)
    place_of_supply = Column(String, nullable=False)
    # Snapshotted, not re-derived, so the CGST/SGST-versus-IGST decision on a reissued PDF
    # cannot drift if a retailer later moves state.
    is_inter_state = Column(Boolean, nullable=False)

    # Supplier snapshot (INV-05)
    supplier_legal_name = Column(String, nullable=False)
    supplier_gstin = Column(String, nullable=True)
    supplier_state = Column(String, nullable=True)
    supplier_address = Column(String, nullable=True)
    supplier_pincode = Column(String, nullable=True)

    # Buyer snapshot (INV-05)
    buyer_name = Column(String, nullable=False)
    buyer_gstin = Column(String, nullable=True)
    buyer_state = Column(String, nullable=True)
    buyer_address = Column(String, nullable=True)
    buyer_pincode = Column(String, nullable=True)

    # Money totals — NUMERIC(12,2), Decimal-native, ROUND_HALF_UP via finance._round_money.
    taxable_value = Column(Numeric(12, 2), nullable=False)
    discount_amount = Column(Numeric(12, 2), nullable=False)
    cgst_amount = Column(Numeric(12, 2), nullable=False)
    sgst_amount = Column(Numeric(12, 2), nullable=False)
    igst_amount = Column(Numeric(12, 2), nullable=False)
    cess_amount = Column(Numeric(12, 2), nullable=False)
    total_tax_amount = Column(Numeric(12, 2), nullable=False)
    grand_total = Column(Numeric(12, 2), nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    order = relationship("Order", back_populates="invoice")
    # No delete-orphan: an invoice line is never orphaned because an invoice is never
    # deleted, and a delete cascade would give the ORM a route around the immutability guard.
    lines = relationship("InvoiceLine", back_populates="invoice", order_by="InvoiceLine.line_number")


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    __table_args__ = (
        UniqueConstraint("invoice_id", "line_number", name="uq_invoice_lines_invoice_line"),
    )

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    line_number = Column(Integer, nullable=False)
    # Provenance only. The invoice must survive the SKU being deleted, which is exactly why
    # every presentational field below is copied rather than joined.
    sku_id = Column(Integer, ForeignKey("skus.id", ondelete="SET NULL"), nullable=True)

    description = Column(String, nullable=False)
    hsn_code = Column(String, nullable=True)
    quantity = Column(Integer, nullable=False)
    uqc = Column(String, nullable=False)
    # GST-inclusive per-unit price as charged; finance._order_item_taxable_value
    # back-computes the taxable value out of quantity * unit_price.
    unit_rate = Column(Numeric(12, 2), nullable=False)
    discount_amount = Column(Numeric(12, 2), nullable=False)
    taxable_value = Column(Numeric(12, 2), nullable=False)

    cgst_rate = Column(Numeric(12, 2), nullable=False)
    cgst_amount = Column(Numeric(12, 2), nullable=False)
    sgst_rate = Column(Numeric(12, 2), nullable=False)
    sgst_amount = Column(Numeric(12, 2), nullable=False)
    igst_rate = Column(Numeric(12, 2), nullable=False)
    igst_amount = Column(Numeric(12, 2), nullable=False)
    cess_rate = Column(Numeric(12, 2), nullable=False)
    cess_amount = Column(Numeric(12, 2), nullable=False)
    total_tax_amount = Column(Numeric(12, 2), nullable=False)
    # GST-inclusive line total after discount. taxable_value + total_tax_amount ==
    # line_total holds exactly and is asserted in plan 02-02.
    line_total = Column(Numeric(12, 2), nullable=False)

    invoice = relationship("Invoice", back_populates="lines")


def _raise_immutable(mapper, connection, target):
    raise InvoiceImmutableError(
        f"{target.__class__.__name__}(id={target.id}) is immutable: issued invoice rows "
        "cannot be updated or deleted."
    )


event.listens_for(Invoice, "before_update")(_raise_immutable)
event.listens_for(Invoice, "before_delete")(_raise_immutable)
event.listens_for(InvoiceLine, "before_update")(_raise_immutable)
event.listens_for(InvoiceLine, "before_delete")(_raise_immutable)
