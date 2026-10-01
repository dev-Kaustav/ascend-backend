from sqlalchemy import CheckConstraint, Column, Integer, String, ForeignKey, Float, Numeric

from app.db.base import Base

class SKU(Base):
    __tablename__ = "skus"
    __table_args__ = (
        CheckConstraint("pack_type IS NULL OR pack_type IN ('box','case','ladi')", name="ck_skus_pack_type"),
        CheckConstraint("units_per_pack IS NULL OR units_per_pack > 0", name="ck_skus_units_per_pack_positive"),
        CheckConstraint("net_weight_g IS NULL OR net_weight_g > 0", name="ck_skus_net_weight_g_positive"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    # The code printed on stock sheets and count sheets (CHN-HG-25g). Nullable
    # because SKUs predating this column have none; unique so a code names one
    # SKU. Postgres permits many NULLs under a unique constraint, so the
    # un-coded rows coexist.
    code = Column(String, unique=True, index=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    hsn_code = Column(String)
    distributor_landing_price = Column(Numeric(12, 3))
    mrp = Column(Numeric(12, 3))
    discount_amount = Column(Numeric(12, 3), default=0)
    discount_percent = Column(Numeric(12, 3), default=0)
    rate = Column(Numeric(12, 3))
    sgst_percent = Column(Numeric(12, 3))
    sgst_amount = Column(Numeric(12, 3))
    cgst_percent = Column(Numeric(12, 3))
    cgst_amount = Column(Numeric(12, 3))
    igst_percent = Column(Numeric(12, 3))
    igst_amount = Column(Numeric(12, 3))
    amount = Column(Numeric(12, 3))
    weight = Column(Float)
    length_cm = Column(Float)
    width_cm = Column(Float)
    height_cm = Column(Float)

    # Storefront catalogue data (STORE-07). image_url is https-only (validated at the schema).
    image_url = Column(String(2048))
    category_id = Column(
        Integer,
        ForeignKey("categories.id", ondelete="SET NULL", name="fk_skus_category_id_categories"),
        index=True,
    )
    # Whole grams; the only pack-size source (D-03).
    net_weight_g = Column(Integer)
    # D-02 provision: pack_type (box | case | ladi) and units_per_pack are admin-writable but
    # not read by ordering this phase.
    pack_type = Column(String(8))
    units_per_pack = Column(Integer)
