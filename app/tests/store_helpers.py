"""Shared builders for the storefront test modules (08-04 onward).

Imported as app.tests.store_helpers (app/tests has no __init__.py; pg_utils uses the same style).
The engine is deliberately not imported from conftest: pytest loads conftest under two module
names, so a static engine import would point at a different in-memory database than the fixture.
"""
import itertools
from decimal import Decimal

from app.core.security import create_access_token
from app.models import Brand, Category, Inventory, Retailer, SKU, SKUBatch, User, Warehouse
from app.models.enums import EmployeeRole
from app.services.store_settings import get_store_settings

_counter = itertools.count(1)


def make_user(db, role=EmployeeRole.RETAILER, *, retailer_id=None, phone_number=None, confirmed=True):
    n = next(_counter)
    user = User(
        email=f"store-user-{n}@example.com",
        password_hash="x",
        role=role,
        retailer_id=retailer_id,
        phone_number=phone_number,
    )
    if confirmed and retailer_id is not None:
        from datetime import datetime, timezone

        user.shop_confirmed_at = datetime.now(timezone.utc)
    db.add(user)
    db.commit()
    return user


def auth_headers(user):
    role = user.role.value if hasattr(user.role, "value") else user.role
    token = create_access_token({"user_id": user.id, "role": role, "tv": user.token_version or 0})
    return {"Authorization": f"Bearer {token}"}


def make_retailer(db, *, name, state="Delhi", mobile_number=None, assigned_salesman_id=None):
    retailer = Retailer(
        name=name,
        state=state,
        mobile_number=mobile_number,
        assigned_salesman_id=assigned_salesman_id,
    )
    db.add(retailer)
    db.commit()
    return retailer


def make_storefront(db, *, state="Delhi"):
    """Create a warehouse and point store_settings.storefront_warehouse_id at it."""
    n = next(_counter)
    warehouse = Warehouse(name=f"Store Warehouse {n}", location="Delhi", state=state)
    db.add(warehouse)
    db.commit()
    settings = get_store_settings(db)
    settings.storefront_warehouse_id = warehouse.id
    # 08-11: no minimum-order delivery charge unless a test sets one, so the pricing and totals
    # tests of earlier plans keep asserting the cart price alone.
    settings.min_order_value = Decimal("0")
    db.commit()
    return warehouse


def make_category(db, name, sort_order=0):
    category = Category(name=name, sort_order=sort_order)
    db.add(category)
    db.commit()
    return category


def make_brand(db, name="Jabsons"):
    brand = Brand(name=name)
    db.add(brand)
    db.commit()
    return brand


def stocked_sku(
    db,
    *,
    warehouse,
    brand,
    category,
    name,
    code,
    mrp,
    amount,
    gst_rate=Decimal("12"),
    net_weight_g=23,
    qty=20,
    image_url="https://jabsons.com/cdn/x.webp",
):
    """A SKU with cgst/sgst = gst_rate / 2, plus qty units of stock at warehouse.

    qty 0 creates no batch and no inventory row. category=None builds a SKU that is not
    store-visible.
    """
    half = Decimal(str(gst_rate)) / 2
    sku = SKU(
        name=name,
        code=code,
        brand_id=brand.id,
        category_id=category.id if category is not None else None,
        hsn_code="2008",
        mrp=Decimal(str(mrp)) if mrp is not None else None,
        amount=Decimal(str(amount)) if amount is not None else None,
        cgst_percent=half,
        sgst_percent=half,
        net_weight_g=net_weight_g,
        image_url=image_url,
        # Sensitive columns the store must never serialise.
        distributor_landing_price=Decimal("5.000"),
        rate=Decimal("6.000"),
    )
    db.add(sku)
    db.commit()
    if qty:
        db.add_all(
            [
                SKUBatch(
                    sku_id=sku.id,
                    warehouse_id=warehouse.id,
                    quantity_received=qty,
                    remaining_quantity=qty,
                ),
                Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=qty),
            ]
        )
        db.commit()
    return sku


def ready_retailer(db, *, warehouse_state="Delhi", assigned_salesman_id=None):
    """A store-ready retailer: confirmed shop, RETAILER user and one saved delivery address.

    warehouse_state is the state of both the registered shop and the saved address, so pass the
    storefront warehouse's state for an intra-state order (CGST + SGST) and a different one for
    an inter-state order (IGST). Returns (user, retailer, address).
    """
    from app.models import RetailerAddress

    n = next(_counter)
    retailer = make_retailer(
        db,
        name=f"Ready Shop {n}",
        state=warehouse_state,
        mobile_number=9100000000 + n,
        assigned_salesman_id=assigned_salesman_id,
    )
    user = make_user(
        db, EmployeeRole.RETAILER, retailer_id=retailer.id, phone_number=9100000000 + n
    )
    address = RetailerAddress(
        retailer_id=retailer.id,
        label="Shop",
        line1=f"{n} Market Road",
        line2="Block B",
        landmark="Near the temple",
        city="Delhi",
        state=warehouse_state,
        pincode=110001,
        latitude=28.6139,
        longitude=77.209,
    )
    db.add(address)
    db.commit()
    return user, retailer, address
