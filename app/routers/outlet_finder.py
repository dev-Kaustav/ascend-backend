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
from app.services.outlet_geo import (
    DELIVERY_COORD_OUTER_LIMIT_M,
    DELIVERY_GPS_ACCURACY_MAX_M,
    distance_to_retailer,
    record_delivery_location,
)
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

    # Checked before recording anything: a reading this far out is more likely the wrong shop
    # than a moved one, and the driver is the only one who can tell us which.
    distance_m = distance_to_retailer(retailer, payload.latitude, payload.longitude)
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

    _, distance_m, retailer_updated = record_delivery_location(
        db,
        retailer,
        user_id=current_user.id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        accuracy_m=payload.accuracy_m,
        outer_limit_overridden=bool(
            distance_m is not None
            and distance_m > DELIVERY_COORD_OUTER_LIMIT_M
            and payload.confirm_outer_limit
        ),
    )
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
