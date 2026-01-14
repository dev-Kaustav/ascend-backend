from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.admin import create_brand, create_warehouse, create_employee, create_retailer, create_sku, create_incoming_order, get_inventory, get_orders, list_retailers, list_salesmen, list_skus, list_warehouses
from app.schemas.admin import BrandCreate, BrandResponse, WarehouseCreate, WarehouseResponse, EmployeeCreate, EmployeeResponse, RetailerCreate, RetailerResponse, SKUCreate, SKUResponse, IncomingOrderCreate, InventoryResponse
from app.schemas.order import OrderResponse
from app.core.deps import require_admin, require_warehouse_manager

router = APIRouter()

@router.post("/brands", response_model=BrandResponse)
def create_brand_endpoint(brand: BrandCreate, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return create_brand(db, brand)

@router.post("/warehouses", response_model=WarehouseResponse)
def create_warehouse_endpoint(warehouse: WarehouseCreate, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return create_warehouse(db, warehouse)

@router.post("/employees", response_model=EmployeeResponse)
def create_employee_endpoint(employee: EmployeeCreate, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return create_employee(db, employee)

@router.post("/retailers", response_model=RetailerResponse)
def create_retailer_endpoint(retailer: RetailerCreate, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return create_retailer(db, retailer)

@router.post("/skus", response_model=SKUResponse)
def create_sku_endpoint(sku: SKUCreate, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return create_sku(db, sku)

@router.post("/incoming-orders")
def create_incoming_order_endpoint(order: IncomingOrderCreate, db: Session = Depends(get_db), current_user = Depends(require_warehouse_manager)):
    return create_incoming_order(db, order)

@router.get("/inventory", response_model=list[InventoryResponse])
def get_inventory_endpoint(db: Session = Depends(get_db), current_user = Depends(require_warehouse_manager)):
    return get_inventory(db)

@router.get("/orders", response_model=list[OrderResponse])
def get_orders_endpoint(db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return get_orders(db)

@router.get("/retailers", response_model=list[RetailerResponse])
def list_retailers_endpoint(db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return list_retailers(db)

@router.get("/salesmen", response_model=list[EmployeeResponse])
def list_salesmen_endpoint(db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return list_salesmen(db)

@router.get("/skus", response_model=list[SKUResponse])
def list_skus_endpoint(db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return list_skus(db)

@router.get("/warehouses", response_model=list[WarehouseResponse])
def list_warehouses_endpoint(db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return list_warehouses(db)
