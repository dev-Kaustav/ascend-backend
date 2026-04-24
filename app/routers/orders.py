from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.order import create_outgoing_order, update_order_status, get_order_detail, get_order_invoice_view, InsufficientStockError, RetailerAccessError
from app.services.invoice_pdf import generate_invoice_pdf
from app.schemas.order import OrderCreate, OrderResponse, StatusUpdate, InvoiceView
from app.core.deps import require_roles, get_role_value
from app.models import User, Order, Retailer

router = APIRouter()

def _ensure_order_access(db: Session, order_id: int, current_user: User):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if get_role_value(current_user) == "SALESMAN":
        retailer = db.query(Retailer).filter(Retailer.id == order.to_entity_id).first()
        if not retailer or retailer.assigned_salesman_id != current_user.employee_id:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
    return order

@router.post("", response_model=OrderResponse)
def create_order(
    order: OrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "SALESMAN", "ACCOUNTANT")),
):
    try:
        return create_outgoing_order(db, order, current_user)
    except InsufficientStockError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RetailerAccessError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "SALESMAN", "ACCOUNTANT", "WAREHOUSE_MANAGER", "DRIVER")),
):
    _ensure_order_access(db, order_id, current_user)
    try:
        return get_order_detail(db, order_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.patch("/{order_id}/status")
def update_status(
    order_id: int,
    status: StatusUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(require_roles("WAREHOUSE_MANAGER", "DRIVER", "ACCOUNTANT")),
):
    try:
        return update_order_status(db, order_id, status)
    except InsufficientStockError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{order_id}/invoice-view", response_model=InvoiceView)
def get_invoice_view(order_id: int, db: Session = Depends(get_db), current_user = Depends(require_roles("ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER", "SALESMAN"))):
    try:
        _ensure_order_access(db, order_id, current_user)
        return get_order_invoice_view(db, order_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/{order_id}/invoice.pdf")
def get_invoice_pdf(order_id: int, db: Session = Depends(get_db), current_user = Depends(require_roles("ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER", "SALESMAN"))):
    try:
        _ensure_order_access(db, order_id, current_user)
        output, filename = generate_invoice_pdf(db, order_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(output, media_type="application/pdf", headers=headers)
