from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.db.session import get_db
from app.models import User
from app.schemas.bill_label import BillLabelCreate, BillLabelPage, BillLabelResponse, BillLabelUpdate
from app.services import bill_label as svc

router = APIRouter()

_allowed = require_roles("ADMIN", "ACCOUNTANT")


@router.post("", response_model=BillLabelResponse, status_code=status.HTTP_201_CREATED)
def create_bill_label(
    body: BillLabelCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(_allowed),
):
    record = svc.create_bill_label(
        db=db,
        user_id=current_user.id,
        **body.model_dump(),
    )
    return svc.enrich_record(record)


@router.get("", response_model=BillLabelPage)
def list_bill_labels(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc"),
    db: Session = Depends(get_db),
    current_user: User = Depends(_allowed),
):
    items, total = svc.list_bill_labels(db, current_user.id, skip, limit, sort_by, sort_dir)
    return BillLabelPage(
        items=[svc.enrich_record(i) for i in items],
        total=total,
    )


@router.get("/{bill_label_id}", response_model=BillLabelResponse)
def get_bill_label(
    bill_label_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_allowed),
):
    record = svc.get_bill_label(db, bill_label_id, current_user.id)
    if not record:
        raise HTTPException(status_code=404, detail="Bill label not found")
    return svc.enrich_record(record)


@router.patch("/{bill_label_id}", response_model=BillLabelResponse)
def update_bill_label(
    bill_label_id: int,
    body: BillLabelUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(_allowed),
):
    try:
        record = svc.update_bill_label(
            db, bill_label_id, current_user.id, **body.model_dump(exclude_unset=True)
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Bill label not found")
    return svc.enrich_record(record)


@router.delete("/{bill_label_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bill_label(
    bill_label_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(_allowed),
):
    if not svc.delete_bill_label(db, bill_label_id, current_user.id):
        raise HTTPException(status_code=404, detail="Bill label not found")
