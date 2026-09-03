from datetime import datetime
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.admin import (
    create_brand,
    create_warehouse,
    create_retailer,
    create_sku,
    add_inventory_receipt,
    get_inventory,
    get_orders_page,
    export_orders_excel,
    list_retailers,
    set_retailer_beat,
    list_brands,
    list_salesmen,
    list_warehouse_managers,
    list_drivers,
    list_skus,
    list_warehouses,
    get_admin_summary,
    get_order_status_summary,
    list_users,
    set_user_active,
    set_user_role,
    set_user_password,
    list_groups,
    create_group,
    create_user,
    soft_delete_user,
    set_user_group,
    get_company_profile,
    update_company_profile,
    set_payment_qr_image,
)
from app.services.invoice import missing_company_invoice_fields
from app.services.invoice_export import export_invoices_xlsx, export_invoices_csv
from app.schemas.admin import (
    BrandCreate,
    BrandResponse,
    WarehouseCreate,
    WarehouseResponse,
    EmployeeResponse,
    RetailerCreate,
    RetailerResponse,
    RetailerBeatUpdate,
    SKUCreate,
    SKUResponse,
    InventoryReceiptCreate,
    InventoryPage,
    UserCreate,
    UserAdminResponse,
    UserRoleUpdate,
    UserPasswordUpdate,
    GroupCreate,
    GroupResponse,
    UserGroupUpdate,
    CompanyProfileUpdate,
    CompanyProfileResponse,
)
from app.schemas.order import OrderListPage
from app.core.deps import require_admin, require_roles, require_warehouse_manager
from app.models.beat import Beat

router = APIRouter()

@router.post("/brands", response_model=BrandResponse)
def create_brand_endpoint(
    brand: BrandCreate,
    db: Session = Depends(get_db),
    current_user = Depends(require_roles("ADMIN", "ACCOUNTANT")),
):
    return create_brand(db, brand)

@router.post("/warehouses", response_model=WarehouseResponse)
def create_warehouse_endpoint(
    warehouse: WarehouseCreate,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
):
    try:
        return create_warehouse(db, warehouse)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/retailers", response_model=RetailerResponse)
def create_retailer_endpoint(
    retailer: RetailerCreate,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
):
    return create_retailer(db, retailer)

@router.post("/skus", response_model=SKUResponse)
def create_sku_endpoint(
    sku: SKUCreate,
    db: Session = Depends(get_db),
    current_user = Depends(require_roles("ADMIN", "WAREHOUSE_MANAGER", "ACCOUNTANT")),
):
    try:
        return create_sku(db, sku, current_user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/inventory")
def add_inventory_receipt_endpoint(
    payload: InventoryReceiptCreate,
    db: Session = Depends(get_db),
    current_user = Depends(require_warehouse_manager),
):
    return add_inventory_receipt(db, payload)

@router.get("/inventory", response_model=InventoryPage)
def get_inventory_endpoint(
    db: Session = Depends(get_db),
    current_user = Depends(require_warehouse_manager),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    warehouse_id: int | None = Query(None, ge=1),
):
    items, total = get_inventory(db, limit=limit, offset=offset, warehouse_id=warehouse_id)
    return {"items": items, "total": total}

@router.get("/orders", response_model=OrderListPage)
def get_orders_endpoint(
    db: Session = Depends(get_db),
    current_user = Depends(require_roles("ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER")),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    payment_status: str | None = Query(None),
    search: str | None = Query(None),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    has_invoice: bool | None = Query(None),
):
    items, total = get_orders_page(
        db,
        current_user,
        limit=limit,
        offset=offset,
        status=status,
        payment_status=payment_status,
        search=search,
        from_date=from_date,
        to_date=to_date,
        has_invoice=has_invoice,
    )
    return {"items": items, "total": total}

@router.get("/orders/export")
def export_orders_endpoint(
    db: Session = Depends(get_db),
    current_user = Depends(require_roles("ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER")),
    status: str | None = Query(None),
    payment_status: str | None = Query(None),
    search: str | None = Query(None),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    has_invoice: bool | None = Query(None),
):
    output = export_orders_excel(
        db,
        status=status,
        payment_status=payment_status,
        search=search,
        from_date=from_date,
        to_date=to_date,
        has_invoice=has_invoice,
    )
    filename = f"orders_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )

@router.get("/invoices/export")
def export_invoices_endpoint(
    db: Session = Depends(get_db),
    current_user = Depends(require_roles("ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER")),
    format: str = Query("xlsx"),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
):
    if format == "xlsx":
        output = export_invoices_xlsx(db, from_date=from_date, to_date=to_date)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ext = "xlsx"
    elif format == "csv":
        output = export_invoices_csv(db, from_date=from_date, to_date=to_date)
        media_type = "text/csv"
        ext = "csv"
    else:
        raise HTTPException(status_code=400, detail="format must be 'xlsx' or 'csv'")

    filename = f"invoices_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{ext}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(output, media_type=media_type, headers=headers)

@router.get("/summary")
def get_admin_summary_endpoint(
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
):
    return get_admin_summary(db, from_date=from_date, to_date=to_date)

def _company_profile_view(profile) -> CompanyProfileResponse:
    """Attach the computed completeness list the stored row does not carry."""
    view = CompanyProfileResponse.model_validate(profile)
    view.missing_invoice_fields = missing_company_invoice_fields(profile)
    return view


@router.get("/company-profile", response_model=CompanyProfileResponse)
def get_company_profile_endpoint(db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return _company_profile_view(get_company_profile(db))

@router.patch("/company-profile", response_model=CompanyProfileResponse)
def update_company_profile_endpoint(
    payload: CompanyProfileUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
):
    return _company_profile_view(update_company_profile(db, payload))

@router.get("/orders/status-summary")
def get_order_status_summary_endpoint(
    db: Session = Depends(get_db),
    current_user = Depends(require_roles("ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER")),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
):
    return get_order_status_summary(db, from_date=from_date, to_date=to_date)

@router.get("/retailers", response_model=list[RetailerResponse])
def list_retailers_endpoint(db: Session = Depends(get_db), current_user = Depends(require_roles("ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER"))):
    return list_retailers(db)

@router.patch("/retailers/{retailer_id}/beat", response_model=RetailerResponse)
def set_retailer_beat_endpoint(
    retailer_id: int,
    payload: RetailerBeatUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
):
    try:
        return set_retailer_beat(db, retailer_id, payload.beat_id)
    except ValueError as e:
        message = str(e)
        status_code = 404 if message == "Retailer not found" else 400
        raise HTTPException(status_code=status_code, detail=message)

@router.get("/salesmen", response_model=list[EmployeeResponse])
def list_salesmen_endpoint(db: Session = Depends(get_db), current_user = Depends(require_roles("ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER"))):
    return list_salesmen(db)

@router.get("/drivers", response_model=list[EmployeeResponse])
def list_drivers_endpoint(db: Session = Depends(get_db), current_user = Depends(require_roles("ADMIN", "WAREHOUSE_MANAGER"))):
    return list_drivers(db)

@router.get("/skus", response_model=list[SKUResponse])
def list_skus_endpoint(db: Session = Depends(get_db), current_user = Depends(require_roles("ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER"))):
    return list_skus(db)

@router.get("/warehouses", response_model=list[WarehouseResponse])
def list_warehouses_endpoint(db: Session = Depends(get_db), current_user = Depends(require_roles("ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER"))):
    return list_warehouses(db)

@router.get("/beats")
def list_beats_endpoint(db: Session = Depends(get_db), current_user = Depends(require_roles("ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER"))):
    return db.query(Beat).order_by(Beat.name).all()

@router.post("/beats")
def create_beat_endpoint(payload: dict, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    name = (payload.get("name") or "").strip()
    if not name:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Beat name is required")
    existing = db.query(Beat).filter(Beat.name == name).first()
    if existing:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Beat already exists")
    beat = Beat(name=name, warehouse_id=payload.get("warehouse_id") or None)
    db.add(beat)
    db.commit()
    db.refresh(beat)
    return beat

@router.get("/lookups")
def list_lookups_endpoint(db: Session = Depends(get_db), current_user = Depends(require_roles("ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER"))):
    return {
        "brands": list_brands(db),
        "retailers": list_retailers(db),
        "warehouses": list_warehouses(db),
        "skus": list_skus(db),
        "salesmen": list_salesmen(db),
        "warehouse_managers": list_warehouse_managers(db),
        "drivers": list_drivers(db),
        "beats": db.query(Beat).order_by(Beat.name).all(),
    }

@router.get("/groups", response_model=list[GroupResponse])
def list_groups_endpoint(db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return list_groups(db)

@router.post("/groups", response_model=GroupResponse)
def create_group_endpoint(payload: GroupCreate, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    try:
        return create_group(db, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/users", response_model=list[UserAdminResponse])
def list_users_endpoint(db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return list_users(db)

@router.post("/users", response_model=UserAdminResponse)
def create_user_endpoint(payload: UserCreate, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    try:
        return create_user(
            db,
            payload.email,
            payload.password,
            payload.role,
            payload.employee_id,
            payload.phone_number,
            payload.is_active,
            payload.group_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.patch("/users/{user_id}/active", response_model=UserAdminResponse)
def set_user_active_endpoint(
    user_id: int,
    is_active: bool = Query(...),
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
):
    try:
        return set_user_active(db, user_id, is_active, current_user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.patch("/users/{user_id}/role", response_model=UserAdminResponse)
def set_user_role_endpoint(
    user_id: int,
    payload: UserRoleUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
):
    try:
        return set_user_role(db, user_id, payload.role, current_user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.patch("/users/{user_id}/password", response_model=UserAdminResponse)
def set_user_password_endpoint(
    user_id: int,
    payload: UserPasswordUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
):
    try:
        return set_user_password(db, user_id, payload.password, current_user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/users/{user_id}", response_model=UserAdminResponse)
def delete_user_endpoint(
    user_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
):
    try:
        return soft_delete_user(db, user_id, current_user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.patch("/users/{user_id}/group", response_model=UserAdminResponse)
def set_user_group_endpoint(
    user_id: int,
    payload: UserGroupUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
):
    try:
        return set_user_group(db, user_id, payload.group_id, current_user)
    except ValueError as e:
        message = str(e)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)
