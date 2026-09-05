"""Approval queue for salesman-proposed retailers.

A salesman may not create a retailer directly: an invented shop plus a booked order is a
fraud that leaves no trace. Instead they file a request here and an admin turns it into a
retailer. Every transition below is one-way out of PENDING, so a request can be acted on
exactly once.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.deps import get_role_value
from app.models import Employee, Retailer, RetailerRequest
from app.models.enums import RetailerRequestStatus
from app.schemas.retailer_request import (
    RetailerRequestCreate,
    RetailerRequestReview,
    RetailerRequestUpdate,
)
from app.services.transactions import transactional_session

REQUEST_FIELDS = (
    "name",
    "mobile_number",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "pincode",
    "gst_number",
)


class RequestNotFoundError(Exception):
    pass


class RequestScopeError(Exception):
    """The caller may not see or act on this request."""


class RequestStateError(Exception):
    """The request has already been decided, so this transition no longer applies."""


def _salesman_employee_id(current_user):
    employee_id = getattr(current_user, "employee_id", None)
    if not employee_id:
        raise RequestScopeError("Salesman is missing an employee record")
    return employee_id


def _get(db: Session, request_id: int) -> RetailerRequest:
    request = db.query(RetailerRequest).filter(RetailerRequest.id == request_id).first()
    if not request:
        raise RequestNotFoundError("Retailer request not found")
    return request


def _own_pending(db: Session, request_id: int, current_user) -> RetailerRequest:
    request = _get(db, request_id)
    if request.requested_by_employee_id != _salesman_employee_id(current_user):
        # Same answer as a missing request would give a stranger, but the caller is a salesman
        # acting on someone else's row, which is a permission problem, not a lookup one.
        raise RequestScopeError("Retailer request belongs to another salesman")
    if request.status != RetailerRequestStatus.PENDING.value:
        raise RequestStateError(f"Request is already {request.status.lower()}")
    return request


def create_request(db: Session, payload: RetailerRequestCreate, current_user) -> RetailerRequest:
    request = RetailerRequest(
        requested_by_employee_id=_salesman_employee_id(current_user),
        status=RetailerRequestStatus.PENDING.value,
        **{field: getattr(payload, field) for field in REQUEST_FIELDS},
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def list_requests(db: Session, current_user, status: str | None = None):
    """Scoped the same way order_form_lookups scopes retailers: a salesman sees only their own
    rows, so the list and the permission to act can never disagree."""
    query = db.query(RetailerRequest)
    if get_role_value(current_user) == "SALESMAN":
        query = query.filter(RetailerRequest.requested_by_employee_id == _salesman_employee_id(current_user))
    if status:
        query = query.filter(RetailerRequest.status == status.upper())
    return query.order_by(RetailerRequest.created_at.desc(), RetailerRequest.id.desc()).all()


def update_request(db: Session, request_id: int, payload: RetailerRequestUpdate, current_user):
    request = _own_pending(db, request_id, current_user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field in REQUEST_FIELDS:
            setattr(request, field, value)
    db.commit()
    db.refresh(request)
    return request


def withdraw_request(db: Session, request_id: int, current_user):
    request = _own_pending(db, request_id, current_user)
    request.status = RetailerRequestStatus.WITHDRAWN.value
    db.commit()
    db.refresh(request)
    return request


def approve_request(db: Session, request_id: int, payload: RetailerRequestReview, current_user):
    request = _get(db, request_id)
    if request.status != RetailerRequestStatus.PENDING.value:
        raise RequestStateError(f"Request is already {request.status.lower()}")

    # The row is built here rather than through create_retailer, which commits internally —
    # calling it inside this block would land the retailer before the status update and defeat
    # the point. One transaction: an approval that recorded the decision but failed to produce
    # the retailer would leave a salesman with an approved request they cannot order against.
    #
    # Field validation is not lost by skipping create_retailer: RetailerRequestCreate subclasses
    # RetailerCreate, so mobile, pincode and state were validated when the request was filed.
    with transactional_session(db):
        retailer = Retailer(
            **{field: getattr(request, field) for field in REQUEST_FIELDS},
            # The requester owns the outlet; order_form_lookups keys their picker off this.
            assigned_salesman_id=request.requested_by_employee_id,
            # Coverage stays an admin decision, as it is for a direct admin edit.
            beat_id=None,
        )
        db.add(retailer)
        db.flush()
        request.status = RetailerRequestStatus.APPROVED.value
        request.created_retailer_id = retailer.id
        request.reviewed_by_user_id = current_user.id
        request.reviewed_at = datetime.now(timezone.utc)
        request.review_note = payload.note if payload else None

    db.refresh(request)
    return request


def reject_request(db: Session, request_id: int, payload: RetailerRequestReview, current_user):
    request = _get(db, request_id)
    if request.status != RetailerRequestStatus.PENDING.value:
        raise RequestStateError(f"Request is already {request.status.lower()}")
    request.status = RetailerRequestStatus.REJECTED.value
    request.reviewed_by_user_id = current_user.id
    request.reviewed_at = datetime.now(timezone.utc)
    request.review_note = payload.note if payload else None
    db.commit()
    db.refresh(request)
    return request


def with_requester_names(db: Session, requests):
    """Attach the salesman's name for display. One query, not one per row."""
    employee_ids = {request.requested_by_employee_id for request in requests}
    names = {
        employee.id: employee.name
        for employee in db.query(Employee).filter(Employee.id.in_(employee_ids)).all()
    } if employee_ids else {}
    rows = []
    for request in requests:
        row = {field: getattr(request, field) for field in REQUEST_FIELDS}
        row.update(
            id=request.id,
            status=request.status,
            requested_by_employee_id=request.requested_by_employee_id,
            requested_by_name=names.get(request.requested_by_employee_id),
            review_note=request.review_note,
            reviewed_at=request.reviewed_at,
            created_retailer_id=request.created_retailer_id,
            created_at=request.created_at,
        )
        rows.append(row)
    return rows
