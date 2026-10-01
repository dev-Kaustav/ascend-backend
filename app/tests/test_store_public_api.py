"""Public storefront catalogue (08-04): what an anonymous visitor and a retailer see."""
from datetime import datetime, timedelta
from decimal import Decimal

from jose import jwt

from app.core.security import SECRET_KEY, create_refresh_token
from app.models.enums import EmployeeRole
from app.schemas.store import StoreBrand, StoreCategory, StoreProduct
from app.tests.store_helpers import (
    auth_headers,
    make_brand,
    make_category,
    make_retailer,
    make_storefront,
    make_user,
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


# ---------------------------------------------------------------------------
# Task 2: categories, brands, filters, search, pagination, ordering, detail
# ---------------------------------------------------------------------------


def _catalogue(db):
    warehouse = make_storefront(db)
    brand = make_brand(db, "Jabsons")
    return warehouse, brand


def test_categories_only_those_with_a_visible_sku_ordered_by_sort_order_then_id(client, db):
    warehouse, brand = _catalogue(db)
    later = make_category(db, "Namkeen & Bhujia", sort_order=2)
    first_a = make_category(db, "Dry Fruits", sort_order=1)
    first_b = make_category(db, "Roasted Nuts & Seeds", sort_order=1)
    empty = make_category(db, "Empty aisle", sort_order=0)
    hidden_only = make_category(db, "Hidden aisle", sort_order=0)
    for i, category in enumerate([later, first_b, first_a]):
        _peanut(db, warehouse, brand, category, name=f"P{i}", code=f"C-{i}")
    # A SKU that breaks the listing rule does not make its category appear.
    _peanut(db, warehouse, brand, hidden_only, name="Hidden", code="H-1", mrp="5", amount="9")

    response = client.get("/store/categories")

    assert response.status_code == 200
    assert [c["id"] for c in response.json()] == [first_a.id, first_b.id, later.id]
    assert empty.id not in [c["id"] for c in response.json()]
    for c in response.json():
        assert set(c) <= {"id", "name", "icon_url"}


def test_categories_and_brands_are_empty_lists_without_visible_skus(client, db):
    make_category(db, "Dry Fruits")
    make_brand(db, "Unused")

    assert client.get("/store/categories").json() == []
    assert client.get("/store/brands").json() == []
    assert client.get("/store/products").json() == {"items": [], "total": 0}


def test_brands_only_with_visible_sku_exactly_id_and_name_ordered_by_name(client, db):
    warehouse = make_storefront(db)
    category = make_category(db, "Dry Fruits")
    zed = make_brand(db, "Zed Foods")
    abc = make_brand(db, "Abc Snacks")
    unused = make_brand(db, "Unused")
    from app.models import Brand

    abc_row = db.get(Brand, abc.id)
    abc_row.poc_name = "Secret Person"
    abc_row.poc_email = "secret@example.com"
    db.commit()
    _peanut(db, warehouse, zed, category, name="Z", code="Z-1")
    _peanut(db, warehouse, abc, category, name="A", code="A-1")

    body = client.get("/store/brands").json()

    assert body == [{"id": abc.id, "name": "Abc Snacks"}, {"id": zed.id, "name": "Zed Foods"}]
    assert unused.id not in [b["id"] for b in body]
    assert "Secret" not in client.get("/store/products").text


def test_category_and_brand_filters(client, db):
    warehouse = make_storefront(db)
    nuts = make_category(db, "Roasted Nuts & Seeds")
    namkeen = make_category(db, "Namkeen & Bhujia")
    jabsons = make_brand(db, "Jabsons")
    other = make_brand(db, "Other")
    a = _peanut(db, warehouse, jabsons, nuts, name="A", code="A-1")
    b = _peanut(db, warehouse, jabsons, namkeen, name="B", code="B-1")
    c = _peanut(db, warehouse, other, nuts, name="C", code="C-1")

    def ids(query):
        body = client.get(f"/store/products?{query}").json()
        return [item["id"] for item in body["items"]], body["total"]

    assert ids(f"category_id={nuts.id}") == ([a.id, c.id], 2)
    assert ids(f"brand_id={jabsons.id}") == ([a.id, b.id], 2)
    assert ids(f"category_id={nuts.id}&brand_id={other.id}") == ([c.id], 1)
    assert ids("category_id=99999") == ([], 0)


def test_search_matches_name_or_code_case_insensitively_and_is_literal(client, db):
    warehouse, brand = _catalogue(db)
    category = make_category(db, "Roasted Nuts & Seeds")
    peanut = _peanut(db, warehouse, brand, category)
    cashew = _peanut(db, warehouse, brand, category, name="Cashew", code="CSH-1")
    percent = _peanut(db, warehouse, brand, category, name="100% Almonds", code="ALM-1")
    underscore = _peanut(db, warehouse, brand, category, name="Mix_Pack", code="MIX-1")

    def ids(q):
        response = client.get("/store/products", params={"q": q})
        assert response.status_code == 200
        return {item["id"] for item in response.json()["items"]}

    assert ids("PEANUT") == {peanut.id}
    assert ids("pnt-td") == {peanut.id}
    assert ids("  CASHEW  ") == {cashew.id}
    # '%' and '_' are literal characters, not wildcards.
    assert ids("%") == {percent.id}
    assert ids("_") == {underscore.id}
    assert ids("%%") == set()
    assert ids("zzz") == set()
    # Blank q means no filter.
    assert ids("   ") == {peanut.id, cashew.id, percent.id, underscore.id}


def test_search_longer_than_100_chars_is_rejected(client, db):
    assert client.get("/store/products", params={"q": "x" * 101}).status_code == 422
    assert client.get("/store/products", params={"q": "x" * 100}).status_code == 200


def test_limit_bounds_offset_pagination_and_total(client, db):
    warehouse, brand = _catalogue(db)
    category = make_category(db, "Dry Fruits")
    skus = [
        _peanut(db, warehouse, brand, category, name=f"Item {n:02d}", code=f"I-{n:02d}")
        for n in range(5)
    ]

    assert client.get("/store/products?limit=0").status_code == 422
    assert client.get("/store/products?limit=101").status_code == 422
    assert client.get("/store/products?offset=-1").status_code == 422
    assert client.get("/store/products?limit=100").status_code == 200

    page = client.get("/store/products?limit=2&offset=2").json()
    assert page["total"] == 5
    assert [item["id"] for item in page["items"]] == [skus[2].id, skus[3].id]
    past_end = client.get("/store/products?limit=2&offset=10").json()
    assert past_end == {"items": [], "total": 5}
    assert len(client.get("/store/products").json()["items"]) == 5


def test_out_of_stock_sorts_after_in_stock_then_name_then_id(client, db):
    warehouse, brand = _catalogue(db)
    category = make_category(db, "Dry Fruits")
    out_a = _peanut(db, warehouse, brand, category, name="Aaa out", code="O-A", qty=0)
    in_z = _peanut(db, warehouse, brand, category, name="Zzz in", code="I-Z", qty=5)
    in_b_first = _peanut(db, warehouse, brand, category, name="bbb same", code="I-B1", qty=5)
    in_b_second = _peanut(db, warehouse, brand, category, name="BBB same", code="I-B2", qty=5)

    body = client.get("/store/products").json()

    # lower(name) ties between "bbb same" and "BBB same" fall back to id.
    assert [item["id"] for item in body["items"]] == [in_b_first.id, in_b_second.id, in_z.id, out_a.id]
    assert [item["in_stock"] for item in body["items"]] == [True, True, True, False]


def test_product_detail_has_the_list_item_shape(client, db):
    warehouse, brand = _catalogue(db)
    category = make_category(db, "Roasted Nuts & Seeds")
    sku = _peanut(db, warehouse, brand, category)

    detail = client.get(f"/store/products/{sku.id}")

    assert detail.status_code == 200
    assert detail.json() == client.get("/store/products").json()["items"][0]


def test_product_detail_404_for_missing_and_non_visible(client, db):
    warehouse, brand = _catalogue(db)
    category = make_category(db, "Roasted Nuts & Seeds")
    uncategorised = _peanut(db, warehouse, brand, None, name="No cat", code="N-1")
    over_mrp = _peanut(db, warehouse, brand, category, name="Over", code="O-1", mrp="5", amount="6")

    for product_id in (uncategorised.id, over_mrp.id, 987654):
        response = client.get(f"/store/products/{product_id}")
        assert response.status_code == 404
        assert response.json() == {"detail": "Product not found"}


def test_pack_size_labels_and_absent_key_when_weight_unknown(client, db):
    from app.services.store_catalogue import pack_size_label

    assert pack_size_label(23) == "23 g"
    assert pack_size_label(999) == "999 g"
    assert pack_size_label(1000) == "1 kg"
    assert pack_size_label(1500) == "1.5 kg"
    assert pack_size_label(2000) == "2 kg"
    assert pack_size_label(1250) == "1.25 kg"
    assert pack_size_label(10000) == "10 kg"
    assert pack_size_label(None) is None

    warehouse, brand = _catalogue(db)
    category = make_category(db, "Dry Fruits")
    sku = _peanut(db, warehouse, brand, category, net_weight_g=None)
    item = client.get(f"/store/products/{sku.id}").json()
    assert "pack_size" not in item
    heavy = _peanut(db, warehouse, brand, category, name="Heavy", code="H-1", net_weight_g=1500)
    assert client.get(f"/store/products/{heavy.id}").json()["pack_size"] == "1.5 kg"


def test_pack_provision_fields_never_appear(client, db):
    warehouse, brand = _catalogue(db)
    category = make_category(db, "Dry Fruits")
    sku = _peanut(db, warehouse, brand, category)
    sku.pack_type = "case"
    sku.units_per_pack = 24
    db.commit()

    text = client.get("/store/products").text + client.get(f"/store/products/{sku.id}").text

    assert "pack_type" not in text
    assert "units_per_pack" not in text


# ---------------------------------------------------------------------------
# Task 3: retailer view, 401 on a bad token, Vary header, leak-proof anonymous view
# ---------------------------------------------------------------------------

RETAILER_ONLY = {"trade_price", "max_orderable"}

FORBIDDEN_KEYS = {
    "rate", "amount", "distributor_landing_price", "discount_amount", "discount_percent",
    "sgst_percent", "sgst_amount", "cgst_percent", "cgst_amount", "igst_percent", "igst_amount",
    "trade_price", "max_orderable", "quantity", "total_quantity", "reserved_quantity",
    "remaining_quantity", "available_quantity", "warehouse_id", "pack_type", "units_per_pack",
}


def _walk_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_keys(value)


def assert_no_forbidden_keys(body):
    keys = set(_walk_keys(body))
    assert not (keys & FORBIDDEN_KEYS), keys & FORBIDDEN_KEYS
    assert not [k for k in keys if "percent" in k.lower()], keys


def _all_routes(sku_id):
    return ["/store/categories", "/store/brands", "/store/products", f"/store/products/{sku_id}"]


def _retailer_headers(db):
    retailer = make_retailer(db, name="Kirana One")
    return auth_headers(make_user(db, EmployeeRole.RETAILER, retailer_id=retailer.id))


def test_retailer_token_adds_trade_price_and_orderable_maximum(client, db):
    warehouse, brand = _catalogue(db)
    category = make_category(db, "Roasted Nuts & Seeds")
    plenty = _peanut(db, warehouse, brand, category, name="Plenty", code="P-1", amount="6.125", qty=1500)
    few = _peanut(db, warehouse, brand, category, name="Few", code="F-1", amount="6.50", qty=7)
    none_left = _peanut(db, warehouse, brand, category, name="None", code="N-1", amount="7", qty=0)
    headers = _retailer_headers(db)

    listed = {i["id"]: i for i in client.get("/store/products", headers=headers).json()["items"]}

    assert listed[plenty.id]["trade_price"] == 6.13  # half-up from 6.125
    assert listed[plenty.id]["max_orderable"] == 999  # capped at MAX_LINE_QUANTITY
    assert listed[few.id]["trade_price"] == 6.5
    assert listed[few.id]["max_orderable"] == 7
    assert listed[none_left.id]["trade_price"] == 7.0
    assert listed[none_left.id]["max_orderable"] == 0
    assert listed[none_left.id]["in_stock"] is False
    assert set(listed[few.id]) == ANON_KEYS | RETAILER_ONLY
    detail = client.get(f"/store/products/{few.id}", headers=headers).json()
    assert detail == listed[few.id]


def test_non_retailer_identities_get_exactly_the_anonymous_shape(client, db):
    warehouse, brand = _catalogue(db)
    category = make_category(db, "Roasted Nuts & Seeds")
    sku = _peanut(db, warehouse, brand, category)
    shopless_retailer = make_user(db, EmployeeRole.RETAILER, retailer_id=None)
    identities = {
        "admin": make_user(db, EmployeeRole.ADMIN),
        "salesman": make_user(db, EmployeeRole.SALESMAN),
        "accountant": make_user(db, EmployeeRole.ACCOUNTANT),
        "shopless retailer": shopless_retailer,
    }
    for label, user in identities.items():
        for url in (f"/store/products/{sku.id}", "/store/products"):
            response = client.get(url, headers=auth_headers(user))
            assert response.status_code == 200, label
            body = response.json()
            item = body["items"][0] if "items" in body else body
            assert set(item) == ANON_KEYS, label
            assert_no_forbidden_keys(body)


def test_bad_tokens_get_401_on_every_catalogue_route(client, db):
    warehouse, brand = _catalogue(db)
    category = make_category(db, "Roasted Nuts & Seeds")
    sku = _peanut(db, warehouse, brand, category)
    retailer = make_retailer(db, name="Kirana One")
    user = make_user(db, EmployeeRole.RETAILER, retailer_id=retailer.id)
    expired = jwt.encode(
        {"user_id": user.id, "role": "RETAILER", "tv": 0, "typ": "access",
         "exp": datetime.utcnow() - timedelta(minutes=1)},
        SECRET_KEY, algorithm="HS256",
    )
    stale_tv = jwt.encode(
        {"user_id": user.id, "role": "RETAILER", "tv": 5, "typ": "access",
         "exp": datetime.utcnow() + timedelta(minutes=5)},
        SECRET_KEY, algorithm="HS256",
    )
    refresh = create_refresh_token({"user_id": user.id, "role": "RETAILER", "tv": 0})
    for token in ("not-a-jwt", expired, stale_tv, refresh):
        for url in _all_routes(sku.id):
            response = client.get(url, headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 401, (url, token[:12])

    # Deleted or inactive user: the token is well-formed but the account is not usable.
    user.is_active = False
    db.commit()
    assert client.get("/store/products", headers=auth_headers(user)).status_code == 401


def _vary_tokens(response):
    # CORSMiddleware may append "Origin" to the same header, so compare tokens.
    return {t.strip().lower() for t in response.headers.get("vary", "").split(",") if t.strip()}


def test_every_catalogue_route_sends_vary_authorization(client, db):
    warehouse, brand = _catalogue(db)
    category = make_category(db, "Roasted Nuts & Seeds")
    sku = _peanut(db, warehouse, brand, category)
    for headers in ({}, _retailer_headers(db)):
        for url in _all_routes(sku.id):
            response = client.get(url, headers=headers)
            assert response.status_code == 200
            assert _vary_tokens(response) >= {"authorization"}, url
    missing = client.get("/store/products/987654")
    assert missing.status_code == 404
    assert "authorization" in _vary_tokens(missing)


def test_anonymous_bodies_contain_no_forbidden_key_anywhere(client, db):
    warehouse, brand = _catalogue(db)
    category = make_category(db, "Roasted Nuts & Seeds")
    sku = _peanut(db, warehouse, brand, category, qty=7)
    _peanut(db, warehouse, brand, category, name="Out", code="O-1", qty=0)
    sku.pack_type = "box"
    sku.units_per_pack = 12
    sku.discount_amount = Decimal("1.5")
    sku.discount_percent = Decimal("10")
    db.commit()

    for url in _all_routes(sku.id):
        response = client.get(url)
        assert response.status_code == 200
        assert_no_forbidden_keys(response.json())
    # The stock count 7 is never the value of any key either.
    assert '"7"' not in client.get("/store/products").text
    assert ":7" not in client.get("/store/products").text.replace(" ", "")


def test_response_schema_field_sets_are_the_allowlist():
    assert set(StoreProduct.model_fields) == ANON_KEYS | RETAILER_ONLY
    assert set(StoreBrand.model_fields) == {"id", "name"}
    assert set(StoreCategory.model_fields) == {"id", "name", "icon_url"}
