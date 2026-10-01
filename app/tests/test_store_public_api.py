"""Public storefront catalogue (08-04): what an anonymous visitor and a retailer see."""
from decimal import Decimal

from app.tests.store_helpers import (
    make_brand,
    make_category,
    make_storefront,
    stocked_sku,
)

ANON_KEYS = {"id", "code", "name", "brand", "category", "image_url", "pack_size", "mrp", "in_stock"}


def _peanut(db, warehouse, brand, category, **overrides):
    values = dict(
        warehouse=warehouse,
        brand=brand,
        category=category,
        name="Jabsons Tandoori Roasted Peanuts 23g",
        code="PNT-TD-23g",
        mrp="8.00",
        amount="6.50",
        net_weight_g=23,
        qty=20,
    )
    values.update(overrides)
    return stocked_sku(db, **values)


def test_anonymous_lists_visible_products_with_mrp_and_in_stock_flag(client, db):
    warehouse = make_storefront(db)
    brand = make_brand(db)
    category = make_category(db, "Roasted Nuts & Seeds")
    category.icon_url = "https://jabsons.com/cdn/nuts.png"
    db.commit()
    sku = _peanut(db, warehouse, brand, category)

    response = client.get("/store/products")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"] == [
        {
            "id": sku.id,
            "code": "PNT-TD-23g",
            "name": "Jabsons Tandoori Roasted Peanuts 23g",
            "brand": {"id": brand.id, "name": "Jabsons"},
            "category": {"id": category.id, "name": "Roasted Nuts & Seeds", "icon_url": "https://jabsons.com/cdn/nuts.png"},
            "image_url": "https://jabsons.com/cdn/x.webp",
            "pack_size": "23 g",
            "mrp": 8.0,
            "in_stock": True,
        }
    ]
    assert set(body["items"][0]) == ANON_KEYS


def test_visible_sku_without_stock_is_listed_out_of_stock_and_uncategorised_sku_is_absent(client, db):
    warehouse = make_storefront(db)
    brand = make_brand(db)
    category = make_category(db, "Namkeen & Bhujia")
    empty = _peanut(db, warehouse, brand, category, name="Empty", code="E-1", qty=0)
    _peanut(db, warehouse, brand, None, name="No category", code="N-1")

    body = client.get("/store/products").json()

    assert body["total"] == 1
    assert [(item["id"], item["in_stock"]) for item in body["items"]] == [(empty.id, False)]


def test_closed_store_still_lists_products_all_out_of_stock(client, db):
    # make_storefront is never called, so no settings row exists yet and
    # storefront_warehouse_id reads NULL ("store closed"). Stock exists at some warehouse.
    from app.models import Warehouse

    warehouse = Warehouse(name="Not the storefront", location="Delhi", state="Delhi")
    db.add(warehouse)
    db.commit()
    brand = make_brand(db)
    category = make_category(db, "Dry Fruits")
    _peanut(db, warehouse, brand, category)

    response = client.get("/store/products")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["in_stock"] is False


def test_closed_store_after_settings_cleared_still_returns_200(client, db):
    from app.services.store_settings import get_store_settings

    warehouse = make_storefront(db)
    brand = make_brand(db)
    category = make_category(db, "Dry Fruits")
    _peanut(db, warehouse, brand, category)
    get_store_settings(db).storefront_warehouse_id = None
    db.commit()

    response = client.get("/store/products")

    assert response.status_code == 200
    assert response.json()["items"][0]["in_stock"] is False


def test_listing_rule_excludes_zero_and_over_mrp_amounts(client, db):
    warehouse = make_storefront(db)
    brand = make_brand(db)
    category = make_category(db, "Dry Fruits")
    ok = _peanut(db, warehouse, brand, category, name="OK", code="OK-1", mrp="10", amount="10")
    _peanut(db, warehouse, brand, category, name="Zero", code="Z-1", mrp="10", amount="0")
    _peanut(db, warehouse, brand, category, name="Over", code="O-1", mrp="10", amount="10.5")
    _peanut(db, warehouse, brand, category, name="NoMrp", code="M-1", mrp=None, amount="5")
    _peanut(db, warehouse, brand, category, name="NoAmount", code="A-1", mrp="5", amount=None)

    body = client.get("/store/products").json()

    assert [item["id"] for item in body["items"]] == [ok.id]
    assert body["total"] == 1
    assert Decimal(str(body["items"][0]["mrp"])) == Decimal("10")
