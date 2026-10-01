"""Tests for scripts/seed_storefront_catalogue.py (STORE-07 catalogue seed).

scripts/ is not a package, so the script is loaded by path.
"""
import importlib.util
import os

import pytest

from app.models import Brand, Category, SKU

SCRIPT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "seed_storefront_catalogue.py")
)
CSV_PATH = os.path.join(os.path.dirname(SCRIPT_PATH), "jabsons_products.csv")

_spec = importlib.util.spec_from_file_location("seed_storefront_catalogue", SCRIPT_PATH)
seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed)


def make_brand(db, name="Jabsons"):
    brand = Brand(name=name)
    db.add(brand)
    db.commit()
    return brand


def make_sku(db, brand, code, **kwargs):
    sku = SKU(name=f"SKU {code}", code=code, brand_id=brand.id, **kwargs)
    db.add(sku)
    db.commit()
    return sku


def test_tracer_apply_fills_image_category_and_weight(db):
    brand = make_brand(db)
    sku = make_sku(db, brand, "PNT-TD-23g")
    rows = seed.load_rows(CSV_PATH)
    expected_image = next(r["image_link"] for r in rows if r["retailer_id"] == "PNT-TD-23g")

    seed.run(db, rows, apply=True)

    db.refresh(sku)
    assert sku.image_url == expected_image
    assert sku.net_weight_g == 23
    category = db.query(Category).filter(Category.id == sku.category_id).one()
    assert category.name == "Roasted Nuts & Seeds"
    assert category.sort_order == 10


def test_tracer_dry_run_writes_nothing_and_reports_plan(db):
    brand = make_brand(db)
    sku = make_sku(db, brand, "PNT-TD-23g")
    rows = seed.load_rows(CSV_PATH)

    report = seed.run(db, rows, apply=False)

    db.expire_all()
    sku = db.query(SKU).filter(SKU.code == "PNT-TD-23g").one()
    assert sku.image_url is None
    assert sku.category_id is None
    assert sku.net_weight_g is None
    assert db.query(Category).count() == 0
    assert report.updated["PNT-TD-23g"] == ["image_url", "category_id", "net_weight_g"]
    assert report.created_categories == ["Roasted Nuts & Seeds"]
