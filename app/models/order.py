from sqlalchemy import CheckConstraint, Column, Integer, Numeric, String, DateTime, Enum, Float, ForeignKey, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.db.base import Base
from .enums import OrderStatus, PaymentStatus, IssueCategory, state_check_constraint

class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        state_check_constraint("orders", column="ship_to_state"),
        CheckConstraint("delivery_charge >= 0", name="ck_orders_delivery_charge_nonnegative"),
        CheckConstraint("channel IN ('ONLINE', 'OFFLINE')", name="ck_orders_channel"),
    )

    id = Column(Integer, primary_key=True, index=True)
    from_entity_type = Column(String, nullable=False)
    from_entity_id = Column(Integer, nullable=False)
    to_entity_type = Column(String, nullable=False)
    to_entity_id = Column(Integer, nullable=False)
    status = Column(Enum(OrderStatus, name="order_status"), default=OrderStatus.PENDING)
    payment_status = Column(Enum(PaymentStatus, name="payment_status"), default=PaymentStatus.CREDIT)
    beat_id = Column(Integer, ForeignKey("beats.id"), nullable=True)
    salesman_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    delivery_driver_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    delivery_date = Column(DateTime(timezone=True), nullable=True)
    panel_status = Column(String, nullable=True)
    issue_category = Column(Enum(IssueCategory, name="issue_category"), nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # ONLINE: placed by a retailer on the storefront. OFFLINE: booked by staff (salesman, admin,
    # imports). It picks the /ON or /OF segment of the invoice number at dispatch.
    channel = Column(String(8), nullable=False, server_default="OFFLINE", default="OFFLINE")

    # 08-09 / D-29: the delivery address a storefront order was placed to, copied at order time.
    # The ship_to_* columns are a snapshot: editing or deleting the saved address afterwards must
    # not change where this order goes, so the FK is only a pointer (SET NULL on delete). All NULL
    # means "deliver to the retailer's registered address" (every salesman order).
    delivery_address_id = Column(
        Integer,
        ForeignKey(
            "retailer_addresses.id",
            name="fk_orders_delivery_address_id_retailer_addresses",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    ship_to_label = Column(String(40), nullable=True)
    ship_to_line1 = Column(String(200), nullable=True)
    ship_to_line2 = Column(String(200), nullable=True)
    ship_to_landmark = Column(String(120), nullable=True)
    ship_to_city = Column(String(80), nullable=True)
    ship_to_state = Column(String, nullable=True)
    ship_to_pincode = Column(Integer, nullable=True)
    ship_to_latitude = Column(Float, nullable=True)
    ship_to_longitude = Column(Float, nullable=True)

    # 08-11 / STORE-11: the GST-inclusive delivery charge the server added below the store's minimum
    # cart value (0 for every salesman/admin order and every order at or above the minimum), and the
    # GST rate it is taxed at (the highest per-item rate in the order, D-33). Both are fixed at
    # order creation; the percentage that produced the charge is never stored here.
    delivery_charge = Column(Numeric(12, 2), nullable=False, server_default="0", default=0)
    delivery_charge_gst_rate = Column(Numeric(5, 2), nullable=False, server_default="0", default=0)

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    credit_notes = relationship("CreditNote", back_populates="order", cascade="all, delete-orphan")
    payments = relationship("Account", back_populates="order", cascade="all, delete-orphan")
    trails = relationship("OrderTrail", backref="order", cascade="all, delete-orphan", order_by="OrderTrail.created_at")
    invoice = relationship("Invoice", back_populates="order", uselist=False)

    @property
    def invoice_number(self):
        """Read-only. Resolves through the Invoice relationship (D-05) — an order may
        legitimately have no invoice until it is dispatched (D-01)."""
        return self.invoice.invoice_number if self.invoice else None
