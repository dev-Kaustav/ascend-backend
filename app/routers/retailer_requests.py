from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import require_admin, require_roles
from app.db.session import get_db
from app.schemas.retailer_request import (
    RetailerRequestCreate,
    RetailerRequestResponse,
    RetailerRequestReview,
    RetailerRequestUpdate,
)
from app.services.retailer_request import (
    RequestNotFoundError,
    RequestScopeError,
    RequestStateError,
    approve_request,
    create_request,
    list_requests,
    reject_request,
    update_request,
    with_requester_names,
    withdraw_request,
)

router = APIRouter()


def _handle(call):
    """One mapping for the three service errors, so every endpoint answers consistently:
    403 for someone else's row, 404 for a missing one, 409 for an already-decided one."""
    try:
        return call()
    except RequestScopeError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except RequestNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RequestStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("", response_model=RetailerRequestResponse)
def create_retailer_request(
    payload: RetailerRequestCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("SALESMAN")),
):
    return _handle(lambda: create_request(db, payload, current_user))


@router.get("", response_model=list[RetailerRequestResponse])
def list_retailer_requests(
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("ADMIN", "SALESMAN")),
    status: str | None = Query(None),
):
    requests = _handle(lambda: list_requests(db, current_user, status))
    return with_requester_names(db, requests)


@router.patch("/{request_id}", response_model=RetailerRequestResponse)
def update_retailer_request(
    request_id: int,
    payload: RetailerRequestUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("SALESMAN")),
):
    return _handle(lambda: update_request(db, request_id, payload, current_user))


@router.post("/{request_id}/withdraw", response_model=RetailerRequestResponse)
def withdraw_retailer_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("SALESMAN")),
):
    return _handle(lambda: withdraw_request(db, request_id, current_user))


@router.post("/{request_id}/approve", response_model=RetailerRequestResponse)
def approve_retailer_request(
    request_id: int,
    payload: RetailerRequestReview | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    return _handle(lambda: approve_request(db, request_id, payload, current_user))


@router.post("/{request_id}/reject", response_model=RetailerRequestResponse)
def reject_retailer_request(
    request_id: int,
    payload: RetailerRequestReview | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    return _handle(lambda: reject_request(db, request_id, payload, current_user))
