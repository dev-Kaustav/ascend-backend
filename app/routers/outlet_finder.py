import math
import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from datetime import datetime, timezone

from app.db.session import get_db
from app.core.deps import get_current_active_user, require_roles
from app.models.employee import Employee
from app.models.enums import AssignmentStatus, EmployeeRole
from app.models.outlet_assignment import OutletAssignment, OutletAssignmentItem
from app.models.outlet_delivery import OutletDelivery
from app.models.retailer import Retailer
from app.models.user import User
from app.schemas.outlet_finder import (
    AssignmentCreateRequest,
    AssignmentItem,
    AssignmentResponse,
    MarkDeliveredRequest,
    MarkDeliveredResponse,
    RetailerLocation,
    RetailerLookupRequest,
    RetailerLookupResponse,
    RetailerStub,
)

router = APIRouter()


def _build_address(r: Retailer):
    parts = [r.address_line1, r.address_line2, r.city, r.state]
    return ", ".join(p for p in parts if p) or None


DELIVERY_COORD_THRESHOLD_M = float(os.getenv("DELIVERY_COORD_THRESHOLD_M", "75"))
DELIVERY_COORD_OUTER_LIMIT_M = float(os.getenv("DELIVERY_COORD_OUTER_LIMIT_M", "5000"))
DELIVERY_GPS_ACCURACY_MAX_M = float(os.getenv("DELIVERY_GPS_ACCURACY_MAX_M", "200"))


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@router.post("/retailers/lookup", response_model=RetailerLookupResponse)
def lookup_retailers(
    payload: RetailerLookupRequest,
    db: Session = Depends(get_db),
    _=Depends(get_current_active_user),
):
    deduped = sorted({eid.strip() for eid in payload.external_ids if eid.strip()})
    if not deduped:
        raise HTTPException(status_code=400, detail="external_ids must contain at least one non-empty value")

    rows = db.query(Retailer).filter(Retailer.external_id.in_(deduped)).all()

    found: list[RetailerLocation] = []
    missing_coords: list[RetailerStub] = []
    seen: set[str] = set()

    for r in rows:
        seen.add(r.external_id)
        if r.latitude is None or r.longitude is None:
            missing_coords.append(RetailerStub(external_id=r.external_id, name=r.name))
            continue
        found.append(RetailerLocation(
            external_id=r.external_id,
            name=r.name,
            address=_build_address(r),
            latitude=r.latitude,
            longitude=r.longitude,
        ))

    not_found = [eid for eid in deduped if eid not in seen]

    return RetailerLookupResponse(
        found=found,
        missing_coords=missing_coords,
        not_found=not_found,
    )


@router.post("/deliveries/mark", response_model=MarkDeliveredResponse)
def mark_delivered(
    payload: MarkDeliveredRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if current_user.role == EmployeeRole.RETAILER:
        raise HTTPException(status_code=403, detail="Retailers cannot mark deliveries")

    if payload.accuracy_m is not None and payload.accuracy_m > DELIVERY_GPS_ACCURACY_MAX_M:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Your location signal is weak. Step outside and try again.",
        )

    retailer = (
        db.query(Retailer)
        .filter(Retailer.external_id == payload.retailer_external_id)
        .first()
    )
    if not retailer:
        raise HTTPException(status_code=404, detail="Retailer not found")

    stored_lat = retailer.latitude
    stored_lng = retailer.longitude
    distance_m = None
    if stored_lat is not None and stored_lng is not None:
        distance_m = _haversine_m(
            stored_lat, stored_lng, payload.latitude, payload.longitude
        )

    if (
        distance_m is not None
        and distance_m > DELIVERY_COORD_OUTER_LIMIT_M
        and not payload.confirm_outer_limit
    ):
        km = distance_m / 1000.0
        return MarkDeliveredResponse(
            delivered=False,
            distance_m=distance_m,
            retailer_updated=False,
            requires_confirmation=True,
            message=f"This outlet is recorded {km:.1f} km from your current location. Are you sure this is the right one?",
        )

    if stored_lat is None or stored_lng is None:
        retailer.latitude = payload.latitude
        retailer.longitude = payload.longitude
        retailer_updated = True
    elif distance_m < DELIVERY_COORD_THRESHOLD_M:
        retailer_updated = False
    else:
        retailer.latitude = payload.latitude
        retailer.longitude = payload.longitude
        retailer_updated = True

    delivery = OutletDelivery(
        user_id=current_user.id,
        retailer_id=retailer.id,
        driver_latitude=payload.latitude,
        driver_longitude=payload.longitude,
        driver_accuracy_m=payload.accuracy_m,
        distance_m=distance_m,
        stored_lat_before=stored_lat,
        stored_lng_before=stored_lng,
        retailer_updated=retailer_updated,
        outer_limit_overridden=bool(
            distance_m is not None
            and distance_m > DELIVERY_COORD_OUTER_LIMIT_M
            and payload.confirm_outer_limit
        ),
    )
    db.add(delivery)
    db.commit()

    return MarkDeliveredResponse(
        delivered=True,
        distance_m=distance_m,
        retailer_updated=retailer_updated,
        new_latitude=retailer.latitude if retailer_updated else None,
        new_longitude=retailer.longitude if retailer_updated else None,
    )


def _assignment_to_response(db: Session, assignment: OutletAssignment) -> AssignmentResponse:
    items = (
        db.query(OutletAssignmentItem, Retailer)
        .join(Retailer, Retailer.id == OutletAssignmentItem.retailer_id)
        .filter(OutletAssignmentItem.assignment_id == assignment.id)
        .all()
    )
    retailer_ids = [r.id for _, r in items]

    driver = db.query(Employee).filter(Employee.id == assignment.driver_employee_id).first()
    driver_user_ids = [
        uid for (uid,) in db.query(User.id).filter(User.employee_id == assignment.driver_employee_id).all()
    ]

    delivered_ids: set[int] = set()
    if retailer_ids and driver_user_ids:
        q = db.query(OutletDelivery.retailer_id).filter(
            OutletDelivery.retailer_id.in_(retailer_ids),
            OutletDelivery.user_id.in_(driver_user_ids),
        )
        if assignment.accepted_at is not None:
            q = q.filter(OutletDelivery.created_at >= assignment.accepted_at)
        delivered_ids = {rid for (rid,) in q.all()}

    item_models = []
    for _, r in items:
        item_models.append(AssignmentItem(
            external_id=r.external_id,
            name=r.name,
            address=_build_address(r),
            latitude=r.latitude,
            longitude=r.longitude,
            delivered=r.id in delivered_ids,
        ))

    return AssignmentResponse(
        id=assignment.id,
        driver_employee_id=assignment.driver_employee_id,
        driver_name=driver.name if driver else None,
        status=assignment.status,
        note=assignment.note,
        created_at=assignment.created_at,
        accepted_at=assignment.accepted_at,
        items=item_models,
        delivered_count=len(delivered_ids),
        total_count=len(item_models),
    )


@router.post("/assignments", response_model=AssignmentResponse)
def create_assignment(
    payload: AssignmentCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("ADMIN", "WAREHOUSE_MANAGER")),
):
    driver = (
        db.query(Employee)
        .filter(Employee.id == payload.driver_employee_id, Employee.role == EmployeeRole.DRIVER)
        .first()
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    deduped = sorted({eid.strip() for eid in payload.external_ids if eid.strip()})
    if not deduped:
        raise HTTPException(status_code=400, detail="external_ids must contain at least one non-empty value")

    retailers = db.query(Retailer).filter(Retailer.external_id.in_(deduped)).all()
    if not retailers:
        raise HTTPException(status_code=400, detail="None of the given outlet IDs matched a retailer")

    assignment = OutletAssignment(
        driver_employee_id=driver.id,
        assigned_by_user_id=current_user.id,
        status=AssignmentStatus.PENDING.value,
        note=payload.note,
    )
    db.add(assignment)
    db.flush()

    for r in retailers:
        db.add(OutletAssignmentItem(assignment_id=assignment.id, retailer_id=r.id))

    db.commit()
    db.refresh(assignment)
    return _assignment_to_response(db, assignment)


@router.get("/assignments", response_model=list[AssignmentResponse])
def list_assignments(
    status_filter: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("ADMIN", "WAREHOUSE_MANAGER")),
):
    q = db.query(OutletAssignment)
    if status_filter:
        q = q.filter(OutletAssignment.status == status_filter)
    assignments = q.order_by(OutletAssignment.created_at.desc()).all()
    return [_assignment_to_response(db, a) for a in assignments]


@router.get("/assignments/mine", response_model=list[AssignmentResponse])
def my_assignments(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if current_user.employee_id is None:
        raise HTTPException(
            status_code=409,
            detail="Your login isn't linked to a driver profile. Ask an admin to link your account.",
        )
    assignments = (
        db.query(OutletAssignment)
        .filter(OutletAssignment.driver_employee_id == current_user.employee_id)
        .order_by(OutletAssignment.status.asc(), OutletAssignment.created_at.desc())
        .all()
    )
    return [_assignment_to_response(db, a) for a in assignments]


@router.post("/assignments/{assignment_id}/accept", response_model=AssignmentResponse)
def accept_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    if current_user.employee_id is None:
        raise HTTPException(
            status_code=409,
            detail="Your login isn't linked to a driver profile. Ask an admin to link your account.",
        )
    assignment = db.query(OutletAssignment).filter(OutletAssignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    if assignment.driver_employee_id != current_user.employee_id:
        raise HTTPException(status_code=403, detail="This job isn't assigned to you")

    if assignment.status != AssignmentStatus.ACCEPTED.value:
        assignment.status = AssignmentStatus.ACCEPTED.value
        assignment.accepted_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(assignment)

    return _assignment_to_response(db, assignment)
