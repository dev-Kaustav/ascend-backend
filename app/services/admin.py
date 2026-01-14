from sqlalchemy.orm import Session
from app.models import Brand, Warehouse, Employee, Retailer, SKU, SKUBatch, Inventory, InventoryTransaction, Order, OrderItem
from app.schemas.admin import BrandCreate, WarehouseCreate, EmployeeCreate, RetailerCreate, SKUCreate, IncomingOrderCreate
from app.models.enums import OrderType, TransactionType, OrderStatus, EmployeeRole
from app.services.transactions import transactional_session
from datetime import datetime

def create_brand(db: Session, brand: BrandCreate):
    db_brand = Brand(**brand.dict())
    db.add(db_brand)
    db.commit()
    db.refresh(db_brand)
    return db_brand

def create_warehouse(db: Session, warehouse: WarehouseCreate):
    db_warehouse = Warehouse(**warehouse.dict())
    db.add(db_warehouse)
    db.commit()
    db.refresh(db_warehouse)
    return db_warehouse

def create_employee(db: Session, employee: EmployeeCreate):
    data = employee.dict()
    data["role"] = EmployeeRole(data["role"])
    db_employee = Employee(**data)
    db.add(db_employee)
    db.commit()
    db.refresh(db_employee)
    return db_employee

def create_retailer(db: Session, retailer: RetailerCreate):
    db_retailer = Retailer(**retailer.dict())
    db.add(db_retailer)
    db.commit()
    db.refresh(db_retailer)
    return db_retailer

def create_sku(db: Session, sku: SKUCreate):
    db_sku = SKU(**sku.dict())
    db.add(db_sku)
    db.commit()
    db.refresh(db_sku)
    return db_sku

def create_incoming_order(db: Session, order: IncomingOrderCreate):
    with transactional_session(db):
        db_order = Order(
            order_type=OrderType.INCOMING,
            from_entity_type="BRAND",
            from_entity_id=order.brand_id,
            to_entity_type="WAREHOUSE",
            to_entity_id=order.warehouse_id,
            status=OrderStatus.CONFIRMED
        )
        db.add(db_order)
        db.flush()

        for item in order.items:
            db_item = OrderItem(
                order_id=db_order.id,
                sku_id=item.sku_id,
                quantity=item.quantity,
                unit_price=0,
                discount_amount=0
            )
            db.add(db_item)

            db_batch = SKUBatch(
                sku_id=item.sku_id,
                batch_number=item.batch_number,
                mfg_date=datetime.strptime(item.mfg_date, "%Y-%m-%d") if item.mfg_date else None,
                expiry_date=datetime.strptime(item.expiry_date, "%Y-%m-%d") if item.expiry_date else None,
                quantity_received=item.quantity,
                remaining_quantity=item.quantity
            )
            db.add(db_batch)
            db.flush()

            inventory = db.query(Inventory).filter(
                Inventory.sku_id == item.sku_id,
                Inventory.warehouse_id == order.warehouse_id
            ).with_for_update().first()
            if not inventory:
                inventory = Inventory(sku_id=item.sku_id, warehouse_id=order.warehouse_id, total_quantity=0)
                db.add(inventory)
            inventory.total_quantity += item.quantity

            transaction = InventoryTransaction(
                sku_id=item.sku_id,
                warehouse_id=order.warehouse_id,
                batch_id=db_batch.id,
                transaction_type=TransactionType.IN,
                quantity=item.quantity
            )
            db.add(transaction)
    return db_order

def get_inventory(db: Session):
    return db.query(Inventory).all()

def get_orders(db: Session):
    return db.query(Order).all()

def list_retailers(db: Session):
    return db.query(Retailer).order_by(Retailer.name.asc()).all()

def list_salesmen(db: Session):
    return db.query(Employee).filter(Employee.role == EmployeeRole.SALESMAN).order_by(Employee.name.asc()).all()

def list_skus(db: Session):
    return db.query(SKU).order_by(SKU.name.asc()).all()

def list_warehouses(db: Session):
    return db.query(Warehouse).order_by(Warehouse.name.asc()).all()
