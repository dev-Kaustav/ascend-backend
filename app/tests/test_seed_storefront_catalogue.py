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


# ---- Task 2: reports, idempotency, never-overwrite, --verify ----

CSV_CODES = [
    "DF-CSTD-35g", "DF-CSTD-18g", "PNT-TD-23g", "NAM-GPOG-22g", "PNT-BP-23g", "DF-CSBP-35g",
    "PNT-CS-27g", "PNT-HG-23g", "CHN-HG-25g", "DF-CSCS-35g", "DF-CSBP-18g", "DF-CSCS-18g",
]
CSV_WEIGHTS = [35, 18, 23, 22, 23, 35, 27, 23, 25, 35, 18, 18]


def make_all_skus(db):
    brand = make_brand(db)
    for code in CSV_CODES:
        make_sku(db, brand, code)
    return brand


def snapshot(db):
    db.expire_all()
    return [
        (s.code, s.image_url, s.category_id, s.net_weight_g)
        for s in db.query(SKU).order_by(SKU.id).all()
    ]


def test_csv_copy_has_twelve_rows():
    assert [r["retailer_id"] for r in seed.load_rows(CSV_PATH)] == CSV_CODES


def test_parse_net_weight_g():
    assert seed.parse_net_weight_g("PNT-TD-23g", None) == 23
    assert seed.parse_net_weight_g("ABC", "Some Namkeen 150g") == 150
    assert seed.parse_net_weight_g("ABC", "Some Namkeen 150 grams") is None
    assert seed.parse_net_weight_g("ABC", None) is None
    assert [seed.parse_net_weight_g(c, "") for c in CSV_CODES] == CSV_WEIGHTS


def test_category_for_code():
    assert seed.category_for_code("PNT-TD-23g") == "Roasted Nuts & Seeds"
    assert seed.category_for_code("CHN-HG-25g") == "Roasted Nuts & Seeds"
    assert seed.category_for_code("NAM-GPOG-22g") == "Namkeen & Bhujia"
    assert seed.category_for_code("DF-CSTD-35g") == "Dry Fruits"
    assert seed.category_for_code("XYZ-1-10g") is None


def test_apply_fills_all_twelve_with_expected_weights_and_shared_images(db):
    make_all_skus(db)
    report = seed.run(db, seed.load_rows(CSV_PATH), apply=True)

    assert len(report.updated) == 12
    assert report.unmatched_codes == []
    assert sorted(report.created_categories) == sorted(name for name, _ in seed.CATEGORY_SEED)
    skus = {s.code: s for s in db.query(SKU).all()}
    assert [skus[c].net_weight_g for c in CSV_CODES] == CSV_WEIGHTS
    assert all(s.image_url and s.category_id for s in skus.values())
    assert skus["DF-CSTD-18g"].image_url == skus["DF-CSTD-35g"].image_url
    assert skus["DF-CSBP-18g"].image_url == skus["DF-CSBP-35g"].image_url
    assert seed.verify(db) == []


def test_second_apply_is_a_no_op(db):
    make_all_skus(db)
    rows = seed.load_rows(CSV_PATH)
    seed.run(db, rows, apply=True)
    before = snapshot(db)

    report = seed.run(db, rows, apply=True)

    assert report.changes == 0
    assert report.updated == {}
    assert report.created_categories == []
    assert report.kept_existing == {}
    assert snapshot(db) == before


def test_admin_set_image_survives_apply(db):
    brand = make_brand(db)
    make_sku(db, brand, "PNT-TD-23g", image_url="https://cdn.example.com/admin-picked.png")

    report = seed.run(db, seed.load_rows(CSV_PATH), apply=True)

    sku = db.query(SKU).filter(SKU.code == "PNT-TD-23g").one()
    assert sku.image_url == "https://cdn.example.com/admin-picked.png"
    assert report.kept_existing == {"PNT-TD-23g": ["image_url"]}
    assert report.updated["PNT-TD-23g"] == ["category_id", "net_weight_g"]


def test_admin_set_category_and_weight_survive_apply(db):
    brand = make_brand(db)
    other = Category(name="Gifting", sort_order=5)
    db.add(other)
    db.commit()
    make_sku(db, brand, "PNT-TD-23g", category_id=other.id, net_weight_g=500)

    seed.run(db, seed.load_rows(CSV_PATH), apply=True)

    sku = db.query(SKU).filter(SKU.code == "PNT-TD-23g").one()
    assert sku.category_id == other.id
    assert sku.net_weight_g == 500
    assert sku.image_url is not None


def test_unmatched_code_is_reported_and_nothing_created(db):
    brand = make_brand(db)
    make_sku(db, brand, "PNT-TD-23g")
    sku_count = db.query(SKU).count()

    report = seed.run(db, seed.load_rows(CSV_PATH), apply=True)

    assert "DF-CSTD-35g" in report.unmatched_codes
    assert len(report.unmatched_codes) == 11
    assert db.query(SKU).count() == sku_count
    # Only the aisle the one matched SKU needs is created.
    assert [c.name for c in db.query(Category).all()] == ["Roasted Nuts & Seeds"]


def test_code_is_matched_exactly_after_trimming_feed_value(db):
    brand = make_brand(db)
    make_sku(db, brand, "pnt-td-23g")  # different case: not an exact match
    rows = [{"retailer_id": "  PNT-TD-23g ", "title": "x", "image_link": "https://a.example/x.png"}]

    report = seed.run(db, rows, apply=True)

    assert report.unmatched_codes == ["PNT-TD-23g"]
    assert db.query(SKU).one().image_url is None


def test_unknown_prefix_and_http_image_are_reported_and_skipped_per_field(db):
    brand = make_brand(db)
    make_sku(db, brand, "ZZZ-AA-10g")
    make_sku(db, brand, "PNT-TD-23g")
    rows = [
        {"retailer_id": "ZZZ-AA-10g", "title": "t", "image_link": "https://a.example/z.png"},
        {"retailer_id": "PNT-TD-23g", "title": "t", "image_link": "http://a.example/p.png"},
    ]

    report = seed.run(db, rows, apply=True)

    zzz = db.query(SKU).filter(SKU.code == "ZZZ-AA-10g").one()
    pnt = db.query(SKU).filter(SKU.code == "PNT-TD-23g").one()
    assert report.unknown_prefix_codes == ["ZZZ-AA-10g"]
    assert zzz.category_id is None and zzz.image_url == "https://a.example/z.png" and zzz.net_weight_g == 10
    assert report.invalid_image_codes == ["PNT-TD-23g"]
    assert pnt.image_url is None and pnt.category_id is not None and pnt.net_weight_g == 23


def test_unparsed_weight_is_reported_not_guessed(db):
    brand = make_brand(db)
    make_sku(db, brand, "PNT-XX")
    rows = [{"retailer_id": "PNT-XX", "title": "Plain peanuts", "image_link": "https://a.example/p.png"}]

    report = seed.run(db, rows, apply=True)

    assert report.unparsed_weight_codes == ["PNT-XX"]
    assert db.query(SKU).one().net_weight_g is None


def test_existing_category_is_reused_case_insensitively(db):
    brand = make_brand(db)
    make_sku(db, brand, "PNT-TD-23g")
    make_sku(db, brand, "CHN-HG-25g")
    existing = Category(name="roasted nuts & seeds", sort_order=99)
    db.add(existing)
    db.commit()

    report = seed.run(db, seed.load_rows(CSV_PATH), apply=True)

    assert report.created_categories == []
    assert db.query(Category).count() == 1
    assert {s.category_id for s in db.query(SKU).all()} == {existing.id}
    assert existing.sort_order == 99


def test_empty_csv_changes_nothing(db, tmp_path):
    make_all_skus(db)
    path = tmp_path / "empty.csv"
    path.write_text(open(CSV_PATH, encoding="utf-8").readline())
    before = snapshot(db)

    report = seed.run(db, seed.load_rows(str(path)), apply=True)

    assert seed.load_rows(str(path)) == []
    assert report.changes == 0
    assert report.unmatched_codes == [] and report.kept_existing == {}
    assert snapshot(db) == before
    assert db.query(Category).count() == 0


def test_no_matching_codes_changes_nothing(db):
    brand = make_brand(db)
    make_sku(db, brand, "OTHER-1-10g")

    report = seed.run(db, seed.load_rows(CSV_PATH), apply=True)

    assert report.changes == 0
    assert len(report.unmatched_codes) == 12
    assert db.query(Category).count() == 0


def test_repeated_runs_never_create_skus_and_cap_categories_at_three(db):
    make_all_skus(db)
    rows = seed.load_rows(CSV_PATH)
    sku_count = db.query(SKU).count()
    for apply in (False, True, True, False):
        seed.run(db, rows, apply=apply)
        assert db.query(SKU).count() == sku_count
        assert db.query(Category).count() <= 3
    assert db.query(Category).count() == 3


def test_script_never_deletes_truncates_or_constructs_skus():
    import ast

    tree = ast.parse(open(SCRIPT_PATH, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            assert not (isinstance(func, ast.Name) and func.id == "SKU")
            assert not (isinstance(func, ast.Attribute) and func.attr in {"delete", "execute", "truncate"})
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 200:
            assert "TRUNCATE" not in node.value.upper().split() and "DROP" not in node.value.upper().split()


# ---- verify ----

def test_verify_lists_jabsons_skus_missing_fields_and_ignores_other_brands(db):
    jabsons = make_brand(db, "Jabsons Foods")
    other = make_brand(db, "Acme")
    cat = Category(name="Dry Fruits", sort_order=1)
    db.add(cat)
    db.commit()
    make_sku(db, jabsons, "A-1-10g", image_url="https://a.example/a.png", category_id=cat.id, net_weight_g=10)
    make_sku(db, jabsons, "B-1-10g", category_id=cat.id, net_weight_g=10)
    make_sku(db, jabsons, "C-1-10g")
    make_sku(db, other, "D-1-10g")

    gaps = {g["code"]: g["missing"] for g in seed.verify(db)}

    assert gaps == {
        "B-1-10g": ["image_url"],
        "C-1-10g": ["image_url", "category_id", "net_weight_g"],
    }


def test_verify_brand_match_is_case_insensitive_prefix(db):
    brand = make_brand(db, "JABSONS")
    make_sku(db, brand, "A-1-10g")
    not_jabsons = make_brand(db, "The Jabsons Co")  # does not START with jabsons
    make_sku(db, not_jabsons, "B-1-10g")

    assert [g["code"] for g in seed.verify(db)] == ["A-1-10g"]


@pytest.fixture
def file_db(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base

    url = f"sqlite:///{tmp_path / 'seed.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield url, session
    session.close()
    engine.dispose()


def test_main_verify_exit_codes(file_db, capsys):
    url, session = file_db
    brand = make_brand(session)
    make_sku(session, brand, "PNT-TD-23g")

    assert seed.main(["--verify", "--database-url", url]) == 1
    assert "PNT-TD-23g" in capsys.readouterr().out

    assert seed.main(["--apply", "--csv", CSV_PATH, "--database-url", url]) == 0
    capsys.readouterr()
    session.expire_all()
    assert seed.main(["--verify", "--database-url", url]) == 0


def test_main_verify_with_no_jabsons_skus_exits_zero(file_db):
    url, _ = file_db
    assert seed.main(["--verify", "--database-url", url]) == 0


def test_main_verify_never_writes(file_db):
    url, session = file_db
    brand = make_brand(session)
    make_sku(session, brand, "PNT-TD-23g")
    seed.main(["--verify", "--database-url", url])
    session.expire_all()
    assert session.query(SKU).one().image_url is None
    assert session.query(Category).count() == 0


def test_main_default_is_dry_run(file_db, capsys):
    url, session = file_db
    brand = make_brand(session)
    make_sku(session, brand, "PNT-TD-23g")

    assert seed.main(["--csv", CSV_PATH, "--database-url", url]) == 0

    out = capsys.readouterr().out
    assert "DRY RUN" in out and "Dry run" in out
    session.expire_all()
    assert session.query(SKU).one().image_url is None


def test_main_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as exc:
        seed.main(["--csv", CSV_PATH])
    assert "DATABASE_URL" in str(exc.value)


def test_main_verify_and_apply_are_mutually_exclusive(file_db):
    url, _ = file_db
    with pytest.raises(SystemExit):
        seed.main(["--verify", "--apply", "--database-url", url])
