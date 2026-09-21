"""Where the driver stood when they closed an order out.

The outlet-finder already captured this on its own `mark_delivered`, but that is a separate
screen a driver may never open. The order list is the one they *do* work, so the coordinates
have to come off that transition or they do not get captured at all.

The governing rule, and the reason most of these tests are about what does NOT happen: the
status change is the business fact and must never fail because of a weak GPS signal. Every
degraded case below has to end with the order DELIVERED.
"""

import itertools

import pytest

from app.models import Brand, Employee, Inventory, Retailer, SKU, SKUBatch, User, Warehouse
from app.models.enums import EmployeeRole, OrderStatus
from app.models.outlet_delivery import OutletDelivery
from app.schemas.order import OrderCreate, OrderItemCreate, StatusUpdate
from app.services.order import create_outgoing_order, update_order_status
from app.services.outlet_geo import DELIVERY_COORD_THRESHOLD_M

_counter = itertools.count()

# A shop, and a point ~30 m away — inside the 75 m threshold, so the pin should not move.
SHOP_LAT, SHOP_LNG = 12.971600, 77.594600
NEARBY_LAT, NEARBY_LNG = 12.971870, 77.594600
# ~1.5 km away: a real correction, still plausibly the same shop.
MOVED_LAT, MOVED_LNG = 12.985000, 77.594600
# Another city entirely — past the 5 km outer limit.
FARAWAY_LAT, FARAWAY_LNG = 19.076000, 72.877700


def _seed(db, retailer_lat=None, retailer_lng=None):
    n = next(_counter)
    brand = Brand(name=f"Geo Brand {n}")
    warehouse = Warehouse(name=f"Geo WH {n}", location="Delhi", state="Delhi")
    retailer = Retailer(
        name=f"Geo Retailer {n}", state="Delhi", latitude=retailer_lat, longitude=retailer_lng
    )
    db.add_all([brand, warehouse, retailer])
    db.commit()

    sku = SKU(name=f"Geo SKU {n}", brand_id=brand.id)
    db.add(sku)
    db.commit()
    db.add(SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=50, remaining_quantity=50))
    db.add(Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=50))

    driver = Employee(name=f"Geo Driver {n}", email=f"geo-driver-{n}@ascend.com", role=EmployeeRole.DRIVER)
    admin = User(email=f"geo-admin-{n}@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([driver, admin])
    db.commit()

    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=5, unit_price=100, discount_amount=0)],
        ),
        admin,
    )
    order.delivery_driver_id = driver.id
    db.commit()

    driver_user = User(
        email=f"geo-driveruser-{n}@ascend.com",
        password_hash="x",
        role=EmployeeRole.DRIVER,
        employee_id=driver.id,
    )
    db.add(driver_user)
    db.commit()

    update_order_status(db, order.id, StatusUpdate(status="READY_TO_SHIP"), current_user=admin)
    update_order_status(db, order.id, StatusUpdate(status="OUT_FOR_DELIVERY"), current_user=driver_user)
    db.refresh(order)
    return order, retailer, driver_user


def _deliver(db, order, driver_user, **geo):
    update_order_status(db, order.id, StatusUpdate(status="DELIVERED", **geo), current_user=driver_user)
    db.refresh(order)


def _rows(db, retailer):
    return db.query(OutletDelivery).filter(OutletDelivery.retailer_id == retailer.id).all()


def test_marking_delivered_records_where_the_driver_was(db):
    order, retailer, driver_user = _seed(db, SHOP_LAT, SHOP_LNG)

    _deliver(db, order, driver_user, latitude=NEARBY_LAT, longitude=NEARBY_LNG, accuracy_m=12.0)

    assert order.status == OrderStatus.DELIVERED
    (row,) = _rows(db, retailer)
    assert (row.driver_latitude, row.driver_longitude) == (NEARBY_LAT, NEARBY_LNG)
    assert row.user_id == driver_user.id
    assert row.driver_accuracy_m == 12.0
    assert row.distance_m == pytest.approx(30, abs=5)


def test_a_first_fix_gives_an_unpinned_retailer_its_location(db):
    """Most retailers have no coordinates. The first driver to reach one is how they get some."""
    order, retailer, driver_user = _seed(db)
    assert retailer.latitude is None

    _deliver(db, order, driver_user, latitude=SHOP_LAT, longitude=SHOP_LNG, accuracy_m=10.0)

    db.refresh(retailer)
    assert (retailer.latitude, retailer.longitude) == (SHOP_LAT, SHOP_LNG)
    (row,) = _rows(db, retailer)
    assert row.retailer_updated is True
    # Nothing to measure against on a first fix — not zero metres away.
    assert row.distance_m is None
    assert row.stored_lat_before is None


def test_a_reading_inside_the_threshold_leaves_a_good_pin_alone(db):
    """Otherwise every delivery would jitter the pin by a few metres of GPS noise forever."""
    order, retailer, driver_user = _seed(db, SHOP_LAT, SHOP_LNG)

    _deliver(db, order, driver_user, latitude=NEARBY_LAT, longitude=NEARBY_LNG, accuracy_m=10.0)

    db.refresh(retailer)
    assert (retailer.latitude, retailer.longitude) == (SHOP_LAT, SHOP_LNG)
    (row,) = _rows(db, retailer)
    assert row.retailer_updated is False
    assert row.distance_m < DELIVERY_COORD_THRESHOLD_M


def test_a_shop_that_really_moved_gets_its_pin_corrected(db):
    order, retailer, driver_user = _seed(db, SHOP_LAT, SHOP_LNG)

    _deliver(db, order, driver_user, latitude=MOVED_LAT, longitude=MOVED_LNG, accuracy_m=10.0)

    db.refresh(retailer)
    assert (retailer.latitude, retailer.longitude) == (MOVED_LAT, MOVED_LNG)
    (row,) = _rows(db, retailer)
    assert row.retailer_updated is True
    assert (row.stored_lat_before, row.stored_lng_before) == (SHOP_LAT, SHOP_LNG)


def test_a_reading_from_another_city_is_logged_but_never_overwrites_the_pin(db):
    """The outlet-finder can stop and ask the driver "are you sure?"; this path cannot, because
    a map prompt must not stand between a driver and closing out their order. So the far reading
    is recorded as evidence and the known-good pin is left untouched."""
    order, retailer, driver_user = _seed(db, SHOP_LAT, SHOP_LNG)

    _deliver(db, order, driver_user, latitude=FARAWAY_LAT, longitude=FARAWAY_LNG, accuracy_m=10.0)

    assert order.status == OrderStatus.DELIVERED
    db.refresh(retailer)
    assert (retailer.latitude, retailer.longitude) == (SHOP_LAT, SHOP_LNG)
    (row,) = _rows(db, retailer)
    assert row.retailer_updated is False
    assert row.distance_m > 500_000


def test_no_location_still_delivers(db):
    """Permission denied, location services off, or an old handset. The day's orders still close."""
    order, retailer, driver_user = _seed(db, SHOP_LAT, SHOP_LNG)

    _deliver(db, order, driver_user)

    assert order.status == OrderStatus.DELIVERED
    assert _rows(db, retailer) == []


def test_a_fix_too_fuzzy_to_identify_a_shop_is_discarded_not_stored(db):
    """A 400 m error bar cannot tell this shop from its neighbour, and a bad pin is worse than
    no pin — it looks authoritative. Dropped, and the delivery still goes through."""
    order, retailer, driver_user = _seed(db)

    _deliver(db, order, driver_user, latitude=SHOP_LAT, longitude=SHOP_LNG, accuracy_m=400.0)

    assert order.status == OrderStatus.DELIVERED
    assert _rows(db, retailer) == []
    db.refresh(retailer)
    assert retailer.latitude is None


def test_location_is_ignored_on_transitions_that_are_not_a_delivery(db):
    """Only arriving at the shop means anything. A dispatch scan from the warehouse does not."""
    order, retailer, driver_user = _seed(db, SHOP_LAT, SHOP_LNG)

    update_order_status(
        db,
        order.id,
        StatusUpdate(status="CANCELLED", latitude=SHOP_LAT, longitude=SHOP_LNG),
        current_user=driver_user,
    )

    assert _rows(db, retailer) == []
