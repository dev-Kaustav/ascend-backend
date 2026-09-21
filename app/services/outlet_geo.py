"""One copy of the outlet-coordinate rules.

Two flows now capture a driver's position at the shop door: the outlet-finder's
`POST /deliveries/mark`, and the order status transition to DELIVERED. They must agree on
what "close enough" means, on when a reading is trusted enough to move a retailer's stored
location, and on the accuracy floor below which a reading is worthless. Two copies of those
numbers would drift, and the drift would be invisible until a retailer's pin quietly moved
because one path used a threshold the other had since changed.

The thresholds stay environment-tunable at the same names the outlet-finder router already
used, so an existing deployment's settings keep working.
"""

import math
import os

from app.models.outlet_delivery import OutletDelivery

# Inside this radius the stored location is treated as still correct and is left alone.
DELIVERY_COORD_THRESHOLD_M = float(os.getenv("DELIVERY_COORD_THRESHOLD_M", "75"))
# Beyond this, the reading is more likely the wrong shop than a moved one.
DELIVERY_COORD_OUTER_LIMIT_M = float(os.getenv("DELIVERY_COORD_OUTER_LIMIT_M", "5000"))
# A reading this fuzzy cannot distinguish one shop from its neighbour.
DELIVERY_GPS_ACCURACY_MAX_M = float(os.getenv("DELIVERY_GPS_ACCURACY_MAX_M", "200"))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def distance_to_retailer(retailer, latitude: float, longitude: float):
    """None when the retailer has no stored pin yet — the caller treats that as 'first fix',
    not as 'zero metres away'."""
    if retailer.latitude is None or retailer.longitude is None:
        return None
    return haversine_m(retailer.latitude, retailer.longitude, latitude, longitude)


def should_update_retailer(distance_m, outer_limit_confirmed: bool = False) -> bool:
    """A first fix always wins. After that a reading only moves the pin if it is far enough
    to be a real correction but near enough to still be the same shop.

    Past the outer limit the reading is far more likely to be a different shop than a moved
    one, so it takes a human saying "yes, deliver here" to overwrite a good pin — that is the
    confirmation the outlet-finder already collects before it retries. A caller that cannot
    ask (the order flow, where the status change must not be held hostage to a map prompt)
    passes False and the pin simply stays put.
    """
    if distance_m is None:
        return True
    if distance_m < DELIVERY_COORD_THRESHOLD_M:
        return False
    if distance_m > DELIVERY_COORD_OUTER_LIMIT_M:
        return outer_limit_confirmed
    return True


def record_delivery_location(
    db,
    retailer,
    user_id: int,
    latitude: float,
    longitude: float,
    accuracy_m=None,
    outer_limit_overridden: bool = False,
):
    """Write the audit row and, when the reading earns it, move the retailer's pin.

    Adds to the session but does not commit: the caller owns the transaction, because in the
    order flow this has to land in the same commit as the status change. A delivery recorded
    without its status, or a status without its location, are both worse than neither.
    """
    stored_lat, stored_lng = retailer.latitude, retailer.longitude
    distance_m = distance_to_retailer(retailer, latitude, longitude)

    retailer_updated = should_update_retailer(distance_m, outer_limit_overridden)
    if retailer_updated:
        retailer.latitude = latitude
        retailer.longitude = longitude

    delivery = OutletDelivery(
        user_id=user_id,
        retailer_id=retailer.id,
        driver_latitude=latitude,
        driver_longitude=longitude,
        driver_accuracy_m=accuracy_m,
        distance_m=distance_m,
        stored_lat_before=stored_lat,
        stored_lng_before=stored_lng,
        retailer_updated=retailer_updated,
        outer_limit_overridden=outer_limit_overridden,
    )
    db.add(delivery)
    return delivery, distance_m, retailer_updated
