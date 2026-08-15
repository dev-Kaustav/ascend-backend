from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.order import create_outgoing_order, update_order_status, get_order_detail, get_order_invoice_view, InsufficientStockError, RetailerAccessError, StatusTransitionForbiddenError
from app.services.invoice_pdf import regenerate_invoice_pdf
from app.services.admin import get_orders_page
from app.schemas.order import OrderCreate, OrderResponse, StatusUpdate, InvoiceView, OrderListPage
from app.core.deps import require_roles, get_role_value
from app.models import User, Order, Retailer

router = APIRouter()

def _ensure_order_access(db: Session, order_id: int, current_user: User):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    role = get_role_value(current_user)
    if role == "SALESMAN":
        retailer = db.query(Retailer).filter(Retailer.id == order.to_entity_id).first()
        if not retailer or retailer.assigned_salesman_id != current_user.employee_id:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
    elif role == "DRIVER":
        if order.delivery_driver_id != current_user.employee_id:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
    elif role == "RETAILER":
        if order.to_entity_id != current_user.retailer_id:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
    return order

def _scoped_base_query(db: Session, current_user: User):
    role = get_role_value(current_user)
    base = db.query(Order).filter(
        Order.from_entity_type == "WAREHOUSE",
        Order.to_entity_type == "RETAILER",
    )
    if role == "SALESMAN":
        return base.filter(Order.salesman_id == current_user.employee_id)
    if role == "DRIVER":
        return base.filter(Order.delivery_driver_id == current_user.employee_id)
    if role == "RETAILER":
        return base.filter(Order.to_entity_id == current_user.retailer_id)
    return base


@router.get("", response_model=OrderListPage)
def list_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER", "SALESMAN", "DRIVER", "RETAILER")),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
):
    items, total = get_orders_page(
        db,
        limit=limit,
        offset=offset,
        status=status,
        from_date=from_date,
        to_date=to_date,
        base_query=_scoped_base_query(db, current_user),
    )
    return {"items": items, "total": total}


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
    current_user: User = Depends(require_roles("ADMIN", "SALESMAN", "ACCOUNTANT", "WAREHOUSE_MANAGER", "DRIVER", "RETAILER")),
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
    current_user = Depends(require_roles("ADMIN", "WAREHOUSE_MANAGER", "DRIVER", "RETAILER")),
):
    try:
        return update_order_status(db, order_id, status, current_user)
    except StatusTransitionForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e))
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
    order = _ensure_order_access(db, order_id, current_user)
    if order.invoice is None:
        # A legitimate, expected state for any order that has not shipped (D-01) —
        # returning a PDF with a blank invoice number would be worse than an error.
        raise HTTPException(status_code=404, detail="No invoice has been issued for this order")
    output, filename = regenerate_invoice_pdf(db, order.invoice)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(output, media_type="application/pdf", headers=headers)
