from datetime import datetime, date, timedelta
import re
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload
from app.models import (
    Brand,
    Warehouse,
    Employee,
    Retailer,
    SKU,
    SKUBatch,
    Inventory,
    InventoryTransaction,
    Order,
    OrderItem,
    OrderItemTax,
    CreditNote,
    User,
    Permission,
    RolePermission,
    UserPermission,
    Group,
    GroupPermission,
)
from app.schemas.admin import (
    BrandCreate,
    WarehouseCreate,
    RetailerCreate,
    SKUCreate,
    InventoryReceiptCreate,
    GroupCreate,
    GroupPermissionUpdate,
    UserGroupUpdate,
)
from app.models.enums import TransactionType, OrderStatus, EmployeeRole, PaymentStatus
from app.services.transactions import transactional_session
from app.core.security import get_password_hash
from app.services.finance import calculate_order_outstanding

def create_brand(db: Session, brand: BrandCreate):
    db_brand = Brand(**brand.dict())
    db.add(db_brand)
    db.commit()
    db.refresh(db_brand)
    return db_brand

def create_warehouse(db: Session, warehouse: WarehouseCreate):
    manager = db.query(Employee).filter(Employee.id == warehouse.manager_id).first()
    if not manager:
        raise ValueError("Warehouse manager not found")
    if manager.role != EmployeeRole.WAREHOUSE_MANAGER:
        raise ValueError("Selected user is not a warehouse manager")
    with transactional_session(db):
        db_warehouse = Warehouse(**warehouse.dict(exclude={"manager_id"}))
        db.add(db_warehouse)
        db.flush()
        manager.warehouse_id = db_warehouse.id
    db.refresh(db_warehouse)
    return db_warehouse

def create_retailer(db: Session, retailer: RetailerCreate):
    db_retailer = Retailer(**retailer.dict())
    db.add(db_retailer)
    db.commit()
    db.refresh(db_retailer)
    return db_retailer

def create_sku(db: Session, sku: SKUCreate, current_user=None):
    if current_user:
        role_value = getattr(current_user.role, "value", None) or getattr(current_user, "role", None)
        if role_value not in {"ADMIN", "WAREHOUSE_MANAGER"}:
            raise ValueError("Only admins or warehouse managers can create SKUs")
    db_sku = SKU(**sku.dict())
    db.add(db_sku)
    db.commit()
    db.refresh(db_sku)
    return db_sku

def add_inventory_receipt(db: Session, receipt: InventoryReceiptCreate):
    with transactional_session(db):
        db_order = Order(
            from_entity_type="BRAND",
            from_entity_id=receipt.brand_id,
            to_entity_type="WAREHOUSE",
            to_entity_id=receipt.warehouse_id,
            status=OrderStatus.CONFIRMED
        )
        db.add(db_order)
        db.flush()

        for item in receipt.items:
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
                mfg_date=datetime.strptime(item.mfg_date, "%Y-%m-%d") if item.mfg_date else None,
                expiry_date=datetime.strptime(item.expiry_date, "%Y-%m-%d") if item.expiry_date else None,
                quantity_received=item.quantity,
                remaining_quantity=item.quantity
            )
            db.add(db_batch)
            db.flush()

            inventory = db.query(Inventory).filter(
                Inventory.sku_id == item.sku_id,
                Inventory.warehouse_id == receipt.warehouse_id
            ).with_for_update().first()
            if not inventory:
                inventory = Inventory(sku_id=item.sku_id, warehouse_id=receipt.warehouse_id, total_quantity=0)
                db.add(inventory)
            inventory.total_quantity += item.quantity

            transaction = InventoryTransaction(
                sku_id=item.sku_id,
                warehouse_id=receipt.warehouse_id,
                batch_id=db_batch.id,
                transaction_type=TransactionType.IN,
                quantity=item.quantity
            )
            db.add(transaction)
    return db_order

def _outgoing_orders_query(db: Session):
    return db.query(Order).filter(
        Order.from_entity_type == "WAREHOUSE",
        Order.to_entity_type == "RETAILER",
    )

def get_inventory(db: Session, limit: int = 50, offset: int = 0):
    query = db.query(Inventory)
    total = query.count()
    items = (
        query.order_by(Inventory.warehouse_id.asc(), Inventory.sku_id.asc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return items, total

def _parse_date(value: str, is_end: bool) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    if is_end:
        return datetime.combine(parsed + timedelta(days=1), datetime.min.time())
    return datetime.combine(parsed, datetime.min.time())

def _totals_subqueries(db: Session):
    base_expr = (OrderItem.quantity * OrderItem.unit_price) - OrderItem.discount_amount
    base_sub = (
        db.query(
            OrderItem.order_id.label("order_id"),
            func.sum(base_expr).label("base_total"),
        )
        .group_by(OrderItem.order_id)
        .subquery()
    )
    tax_sub = (
        db.query(
            OrderItem.order_id.label("order_id"),
            func.sum(base_expr * (OrderItemTax.rate / 100.0)).label("tax_total"),
        )
        .join(OrderItemTax, OrderItemTax.order_item_id == OrderItem.id)
        .group_by(OrderItem.order_id)
        .subquery()
    )
    return base_sub, tax_sub

def get_orders_page(
    db: Session,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    payment_status: str | None = None,
    search: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    has_invoice: bool | None = None,
):
    query = _outgoing_orders_query(db)
    if status:
        try:
            query = query.filter(Order.status == OrderStatus(status))
        except ValueError:
            pass
    if payment_status:
        try:
            query = query.filter(Order.payment_status == PaymentStatus(payment_status))
        except ValueError:
            pass
    if search:
        query = query.filter(Order.invoice_number.ilike(f"%{search}%"))
    if has_invoice is True:
        query = query.filter(Order.invoice_number.isnot(None))
    if has_invoice is False:
        query = query.filter(Order.invoice_number.is_(None))

    from_dt = _parse_date(from_date, False)
    to_dt = _parse_date(to_date, True)
    if from_dt:
        query = query.filter(Order.created_at >= from_dt)
    if to_dt:
        query = query.filter(Order.created_at < to_dt)

    total = query.count()

    base_sub, tax_sub = _totals_subqueries(db)
    total_expr = func.coalesce(base_sub.c.base_total, 0) + func.coalesce(tax_sub.c.tax_total, 0)

    rows = (
        query.outerjoin(base_sub, base_sub.c.order_id == Order.id)
        .outerjoin(tax_sub, tax_sub.c.order_id == Order.id)
        .add_columns(total_expr.label("total_amount"))
        .order_by(Order.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    items = []
    for order, total_amount in rows:
        setattr(order, "total_amount", float(total_amount or 0))
        items.append(order)

    order_ids = [order.id for order in items]
    if order_ids:
        detailed_orders = (
            db.query(Order)
            .options(
                selectinload(Order.items).selectinload(OrderItem.taxes),
                selectinload(Order.payments),
                selectinload(Order.credit_notes).selectinload(CreditNote.items),
            )
            .filter(Order.id.in_(order_ids))
            .all()
        )
        detailed_map = {order.id: order for order in detailed_orders}
        for order in items:
            detailed = detailed_map.get(order.id)
            pending_amount = calculate_order_outstanding(detailed) if detailed else 0
            setattr(order, "pending_amount", float(pending_amount))
    return items, total

def get_admin_summary(db: Session):
    orders_count = _outgoing_orders_query(db).count()

    payment_rows = (
        db.query(Order.payment_status, func.count(Order.id))
        .filter(Order.from_entity_type == "WAREHOUSE", Order.to_entity_type == "RETAILER")
        .group_by(Order.payment_status)
        .all()
    )
    payment_status = {row[0].value if hasattr(row[0], "value") else row[0]: row[1] for row in payment_rows}

    base_sub, tax_sub = _totals_subqueries(db)
    total_expr = func.coalesce(base_sub.c.base_total, 0) + func.coalesce(tax_sub.c.tax_total, 0)
    outgoing_total = (
        db.query(func.coalesce(func.sum(total_expr), 0))
        .select_from(Order)
        .outerjoin(base_sub, base_sub.c.order_id == Order.id)
        .outerjoin(tax_sub, tax_sub.c.order_id == Order.id)
        .filter(Order.from_entity_type == "WAREHOUSE", Order.to_entity_type == "RETAILER")
        .scalar()
        or 0
    )
    outstanding_total = (
        db.query(func.coalesce(func.sum(total_expr), 0))
        .select_from(Order)
        .outerjoin(base_sub, base_sub.c.order_id == Order.id)
        .outerjoin(tax_sub, tax_sub.c.order_id == Order.id)
        .filter(
            Order.from_entity_type == "WAREHOUSE",
            Order.to_entity_type == "RETAILER",
            Order.payment_status != PaymentStatus.PAID,
        )
        .scalar()
        or 0
    )

    since = datetime.utcnow() - timedelta(days=30)
    timeline_rows = (
        db.query(func.date(Order.created_at), func.count(Order.id))
        .filter(
            Order.created_at >= since,
            Order.from_entity_type == "WAREHOUSE",
            Order.to_entity_type == "RETAILER",
        )
        .group_by(func.date(Order.created_at))
        .order_by(func.date(Order.created_at))
        .all()
    )
    orders_over_time = [
        {"label": day.strftime("%d %b"), "orders": count}
        for day, count in timeline_rows
        if day
    ]

    warehouse_map = {w.id: w.name for w in db.query(Warehouse).all()}
    sales_rows = (
        db.query(Order.from_entity_id, func.count(Order.id))
        .filter(Order.from_entity_type == "WAREHOUSE", Order.to_entity_type == "RETAILER")
        .group_by(Order.from_entity_id)
        .all()
    )
    sales_by_warehouse = [
        {
            "warehouse_id": warehouse_id,
            "warehouse_name": warehouse_map.get(warehouse_id) or f"WH-{warehouse_id}",
            "orders": count,
        }
        for warehouse_id, count in sales_rows
    ]

    inventory_rows = (
        db.query(Inventory.warehouse_id, func.sum(Inventory.total_quantity))
        .group_by(Inventory.warehouse_id)
        .all()
    )
    inventory_by_warehouse = [
        {
            "warehouse_id": warehouse_id,
            "warehouse_name": warehouse_map.get(warehouse_id) or f"WH-{warehouse_id}",
            "units": float(units or 0),
        }
        for warehouse_id, units in inventory_rows
    ]
    quantities = [row[1] for row in inventory_rows]
    max_qty = max(quantities) if quantities else 0
    low_stock_threshold = max(10, int(max_qty * 0.1)) if max_qty else 10
    low_stock_count = (
        db.query(func.count(Inventory.sku_id))
        .filter(Inventory.total_quantity <= low_stock_threshold)
        .scalar()
        or 0
    )

    return {
        "orders": {
            "total": orders_count,
            "revenue": float(outgoing_total),
            "outstanding": float(outstanding_total),
            "payment_status": payment_status,
        },
        "inventory": {
            "inventory_value": None,
            "low_stock_count": low_stock_count,
            "low_stock_threshold": low_stock_threshold,
            "by_warehouse": inventory_by_warehouse,
        },
        "orders_over_time": orders_over_time,
        "sales_by_warehouse": sales_by_warehouse,
    }

def list_retailers(db: Session):
    return db.query(Retailer).order_by(Retailer.name.asc()).all()

def list_brands(db: Session):
    return db.query(Brand).order_by(Brand.name.asc()).all()

def list_salesmen(db: Session):
    return db.query(Employee).filter(Employee.role == EmployeeRole.SALESMAN).order_by(Employee.name.asc()).all()

def list_warehouse_managers(db: Session):
    return db.query(Employee).filter(Employee.role == EmployeeRole.WAREHOUSE_MANAGER).order_by(Employee.name.asc()).all()

def list_drivers(db: Session):
    return db.query(Employee).filter(Employee.role == EmployeeRole.DRIVER).order_by(Employee.name.asc()).all()

def list_skus(db: Session):
    return db.query(SKU).order_by(SKU.name.asc()).all()

def list_warehouses(db: Session):
    return db.query(Warehouse).order_by(Warehouse.name.asc()).all()

def get_order_status_summary(db: Session):
    rows = (
        db.query(Order.status, func.count(Order.id))
        .filter(Order.from_entity_type == "WAREHOUSE", Order.to_entity_type == "RETAILER")
        .group_by(Order.status)
        .all()
    )
    summary = {}
    for status, count in rows:
        key = status.value if hasattr(status, "value") else status
        summary[key] = count
    return summary

def _role_value(role) -> str:
    return role.value if hasattr(role, "value") else role

def _all_permissions(db: Session):
    perms = db.query(Permission).order_by(Permission.code.asc()).all()
    perm_ids = [perm.id for perm in perms]
    return perms, perm_ids

def _role_permissions_map(db: Session):
    role_map: dict[str, set[int]] = {}
    for role, permission_id in db.query(RolePermission.role, RolePermission.permission_id).all():
        role_map.setdefault(role, set()).add(permission_id)
    return role_map

def _group_permissions_map(db: Session, group_ids: list[int] | None = None):
    query = db.query(GroupPermission.group_id, GroupPermission.permission_id, GroupPermission.is_allowed)
    if group_ids:
        query = query.filter(GroupPermission.group_id.in_(group_ids))
    mapping: dict[int, dict[int, bool]] = {}
    for group_id, permission_id, is_allowed in query.all():
        mapping.setdefault(group_id, {})[permission_id] = is_allowed
    return mapping

def _user_permissions_map(db: Session, user_ids: list[int] | None = None):
    query = db.query(UserPermission.user_id, UserPermission.permission_id, UserPermission.is_allowed)
    if user_ids:
        query = query.filter(UserPermission.user_id.in_(user_ids))
    mapping: dict[int, dict[int, bool]] = {}
    for user_id, permission_id, is_allowed in query.all():
        mapping.setdefault(user_id, {})[permission_id] = is_allowed
    return mapping

def _base_permission_ids_for_role(all_perm_ids: list[int], role_value: str, role_permissions: dict[str, set[int]]):
    if role_value == "ADMIN":
        return set(all_perm_ids)
    return set(role_permissions.get(role_value, set()))

def _build_permission_entries(all_permissions, base_ids: set[int], *overrides: dict[int, bool]):
    state = {perm_id: True for perm_id in base_ids}
    for override in overrides:
        if override:
            state.update(override)
    return [{"code": perm.code, "is_allowed": state.get(perm.id, False)} for perm in all_permissions]

def _enrich_users_with_permissions(db: Session, users: list[User]):
    if not users:
        return users
    all_permissions, perm_ids = _all_permissions(db)
    role_permissions = _role_permissions_map(db)
    group_ids = [u.group_id for u in users if u.group_id]
    group_map = {}
    if group_ids:
        group_map = {g.id: g for g in db.query(Group).filter(Group.id.in_(group_ids)).all()}
    group_permission_map = _group_permissions_map(db, group_ids)
    user_permission_map = _user_permissions_map(db, [u.id for u in users])
    for user in users:
        role_value = _role_value(user.role)
        base_ids = _base_permission_ids_for_role(perm_ids, role_value, role_permissions)
        overrides = []
        if user.group_id:
            overrides.append(group_permission_map.get(user.group_id, {}))
        overrides.append(user_permission_map.get(user.id, {}))
        user.permissions = _build_permission_entries(all_permissions, base_ids, *overrides)
        group = group_map.get(user.group_id)
        user.group_name = group.name if group else None
    return users

def _enrich_groups_with_permissions(db: Session, groups: list[Group]):
    if not groups:
        return groups
    all_permissions, perm_ids = _all_permissions(db)
    role_permissions = _role_permissions_map(db)
    group_permission_map = _group_permissions_map(db, [g.id for g in groups])
    user_counts = dict(db.query(User.group_id, func.count(User.id)).group_by(User.group_id).all())
    for group in groups:
        role_value = _role_value(group.role)
        base_ids = _base_permission_ids_for_role(perm_ids, role_value, role_permissions)
        overrides = [group_permission_map.get(group.id, {})]
        group.permissions = _build_permission_entries(all_permissions, base_ids, *overrides)
        group.user_count = int(user_counts.get(group.id, 0))
    return groups

def list_users(db: Session):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return _enrich_users_with_permissions(db, users)

def _count_active_admins(db: Session):
    return (
        db.query(User)
        .filter(User.role == EmployeeRole.ADMIN, User.is_active == True)  # noqa: E712
        .count()
    )

def _normalize_email(email: str):
    return email.strip().lower()

def _default_employee_name(email: str) -> str:
    local_part = email.split("@")[0]
    parts = [part for part in re.split(r"[._-]+", local_part) if part]
    if not parts:
        return local_part
    return " ".join(part.capitalize() for part in parts)

def create_user(
    db: Session,
    email: str,
    password: str,
    role: str | None,
    employee_id: int | None,
    phone_number: int,
    is_active: bool = True,
    group_id: int | None = None,
):
    normalized_email = _normalize_email(email)
    existing = db.query(User).filter(User.email == normalized_email).first()
    if existing:
        raise ValueError("User already exists")
    group = None
    if group_id:
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group:
            raise ValueError("Group not found")

    resolved_role = None
    if group:
        resolved_role = group.role
    if role:
        try:
            role_value = EmployeeRole(role)
        except ValueError:
            raise ValueError("Invalid role")
        if resolved_role and role_value != resolved_role:
            raise ValueError("Group role does not match selected role")
        resolved_role = role_value
    if not resolved_role:
        raise ValueError("Role is required")

    employee = None
    if employee_id:
        employee = db.query(Employee).filter(Employee.id == employee_id).first()
        if not employee:
            raise ValueError("Employee not found")
        if employee.role != resolved_role:
            raise ValueError("Employee role does not match selected role")
        employee.phone_number = phone_number
    else:
        employee = db.query(Employee).filter(Employee.email == normalized_email).first()
        if employee:
            if employee.role != resolved_role:
                raise ValueError("Employee role does not match selected role")
            employee.phone_number = phone_number
        else:
            employee = Employee(
                name=_default_employee_name(normalized_email),
                email=normalized_email,
                role=resolved_role,
                phone_number=phone_number,
            )
            db.add(employee)
            db.flush()

    password_hash = get_password_hash(password)
    user = User(
        email=normalized_email,
        password_hash=password_hash,
        role=resolved_role,
        group_id=group.id if group else None,
        employee_id=employee.id if employee else None,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _enrich_users_with_permissions(db, [user])[0]

def set_user_active(db: Session, user_id: int, is_active: bool, acting_user: User):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError("User not found")
    if user.role == EmployeeRole.ADMIN and not is_active:
        active_admins = _count_active_admins(db)
        if active_admins <= 1:
            raise ValueError("Cannot disable the last active admin")
        if acting_user.id == user_id:
            raise ValueError("Admins cannot disable themselves")
    user.is_active = is_active
    db.commit()
    db.refresh(user)
    return _enrich_users_with_permissions(db, [user])[0]

def set_user_role(db: Session, user_id: int, role: str, acting_user: User):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError("User not found")
    try:
        new_role = EmployeeRole(role)
    except ValueError:
        raise ValueError("Invalid role")
    if acting_user.id == user_id and new_role != EmployeeRole.ADMIN:
        raise ValueError("Admins cannot demote themselves")
    if user.role == EmployeeRole.ADMIN and new_role != EmployeeRole.ADMIN:
        active_admins = _count_active_admins(db)
        if active_admins <= 1:
            raise ValueError("Cannot remove the last active admin role")
    if user.group_id and user.group and user.group.role != new_role:
        user.group_id = None
    user.role = new_role
    db.commit()
    db.refresh(user)
    return _enrich_users_with_permissions(db, [user])[0]

def ensure_permission(db: Session, code: str, description: str | None = None):
    perm = db.query(Permission).filter(Permission.code == code).first()
    if perm:
        return perm
    perm = Permission(code=code, description=description or code)
    db.add(perm)
    db.commit()
    db.refresh(perm)
    return perm

def set_user_permission(db: Session, user_id: int, code: str, is_allowed: bool):
    perm = ensure_permission(db, code)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError("User not found")
    entry = (
        db.query(UserPermission)
        .filter(UserPermission.user_id == user_id, UserPermission.permission_id == perm.id)
        .first()
    )
    if entry:
        entry.is_allowed = is_allowed
    else:
        entry = UserPermission(user_id=user_id, permission_id=perm.id, is_allowed=is_allowed)
        db.add(entry)
    db.commit()
    return {"code": perm.code, "is_allowed": is_allowed}

def list_groups(db: Session):
    groups = db.query(Group).order_by(Group.name.asc()).all()
    return _enrich_groups_with_permissions(db, groups)

def create_group(db: Session, payload: GroupCreate):
    name = payload.name.strip()
    if not name:
        raise ValueError("Group name is required")
    existing = db.query(Group).filter(func.lower(Group.name) == name.lower()).first()
    if existing:
        raise ValueError("Group with this name already exists")
    try:
        role_value = EmployeeRole(payload.role)
    except ValueError:
        raise ValueError("Invalid role")
    group = Group(name=name, role=role_value)
    db.add(group)
    db.flush()

    all_permissions, perm_ids = _all_permissions(db)
    role_permissions = _role_permissions_map(db)
    base_ids = _base_permission_ids_for_role(perm_ids, _role_value(role_value), role_permissions)
    for perm_id in base_ids:
        db.add(GroupPermission(group_id=group.id, permission_id=perm_id, is_allowed=True))

    db.commit()
    db.refresh(group)
    return _enrich_groups_with_permissions(db, [group])[0]

def set_group_permission(db: Session, group_id: int, code: str, is_allowed: bool):
    perm = ensure_permission(db, code)
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise ValueError("Group not found")
    entry = (
        db.query(GroupPermission)
        .filter(GroupPermission.group_id == group_id, GroupPermission.permission_id == perm.id)
        .first()
    )
    if entry:
        entry.is_allowed = is_allowed
    else:
        entry = GroupPermission(group_id=group_id, permission_id=perm.id, is_allowed=is_allowed)
        db.add(entry)
    db.flush()

    # Propagate to all users in this group by aligning their explicit permission to the group value.
    user_ids = [row[0] for row in db.query(User.id).filter(User.group_id == group_id).all()]
    for user_id in user_ids:
        user_entry = (
            db.query(UserPermission)
            .filter(UserPermission.user_id == user_id, UserPermission.permission_id == perm.id)
            .first()
        )
        if user_entry:
            user_entry.is_allowed = is_allowed
        else:
            db.add(UserPermission(user_id=user_id, permission_id=perm.id, is_allowed=is_allowed))

    db.commit()
    return {"code": perm.code, "is_allowed": is_allowed}

def set_user_group(db: Session, user_id: int, group_id: int | None, acting_user: User):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError("User not found")
    if group_id is None:
        user.group_id = None
        db.commit()
        db.refresh(user)
        return _enrich_users_with_permissions(db, [user])[0]

    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise ValueError("Group not found")
    new_role = group.role
    if acting_user.id == user_id and new_role != EmployeeRole.ADMIN:
        raise ValueError("Admins cannot demote themselves")
    if user.role == EmployeeRole.ADMIN and new_role != EmployeeRole.ADMIN:
        active_admins = _count_active_admins(db)
        if active_admins <= 1:
            raise ValueError("Cannot remove the last active admin role")

    user.group_id = group_id
    user.role = new_role
    db.commit()
    db.refresh(user)
    return _enrich_users_with_permissions(db, [user])[0]

def list_permissions(db: Session):
    return db.query(Permission).order_by(Permission.code.asc()).all()
