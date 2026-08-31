import re
from datetime import datetime, date, time as dtime

from sqlalchemy.orm import Session

from app.models import Brand, Beat, Retailer, Warehouse, SKU, Employee, Order, Account, OrderTrail, Invoice


class _ImportAbort(Exception):
    pass
from app.models.enums import EmployeeRole, OrderStatus, PaymentStatus, PaymentMode, IssueCategory
from app.services.excel_template import SHEET_HEADERS
from app.services.invoice import issue_invoice_for_order
from app.services.transactions import transactional_session


def _read_rows(ws) -> list[tuple[int, dict]]:
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    # Some sheets have a summary/totals row before the real header row.
    # Find the first row (within the first 5) that has ≥3 non-empty string cells.
    header_idx = 0
    for i, row in enumerate(rows[:5]):
        if sum(1 for v in row if v is not None and isinstance(v, str) and str(v).strip()) >= 3:
            header_idx = i
            break
    headers = [str(h).strip() if h is not None else "" for h in rows[header_idx]]
    result = []
    for row_num, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        result.append((row_num, dict(zip(headers, row))))
    return result


def _str(val) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _get_sheet(wb, sheet_name: str):
    """Case-insensitive sheet lookup."""
    for name in wb.sheetnames:
        if name.strip().lower() == sheet_name.lower():
            return wb[name]
    return None


# --- Validation phases (no DB writes) ---

def _validate_brands(rows: list[tuple[int, dict]]) -> tuple[list[dict], list[dict]]:
    errors, records = [], []
    for row_num, row in rows:
        name = _str(row.get("Name"))
        if not name:
            errors.append({"row": row_num, "error": "Name is required"})
            continue
        records.append({
            "name": name,
            "poc_name": _str(row.get("POC Name")),
            "poc_phone_number": _int(row.get("POC Phone")),
            "poc_email": _str(row.get("POC Email")),
        })
    return records, errors


def _validate_retailers(db: Session, rows: list[tuple[int, dict]], brand_inserts: list[dict]) -> tuple[list[dict], list[dict]]:
    errors, records = [], []
    salesman_cache: dict[str, int] = {}
    for row_num, row in rows:
        name = _str(row.get("Name"))
        if not name:
            errors.append({"row": row_num, "error": "Name is required"})
            continue
        salesman_name = _str(row.get("Assigned Salesman"))
        assigned_salesman_id = None
        if salesman_name:
            if salesman_name not in salesman_cache:
                emp = (
                    db.query(Employee)
                    .filter(Employee.name == salesman_name, Employee.role == EmployeeRole.SALESMAN)
                    .first()
                )
                if not emp:
                    errors.append({"row": row_num, "error": f"Salesman '{salesman_name}' not found"})
                    continue
                salesman_cache[salesman_name] = emp.id
            assigned_salesman_id = salesman_cache[salesman_name]
        records.append({
            "name": name,
            "mobile_number": _int(row.get("Mobile Number")),
            "address_line1": _str(row.get("Address Line 1")),
            "address_line2": _str(row.get("Address Line 2")),
            "city": _str(row.get("City")),
            "state": _str(row.get("State")),
            "pincode": _int(row.get("Pincode")),
            "gst_number": _str(row.get("GST Number")),
            "latitude": _float(row.get("Latitude")),
            "longitude": _float(row.get("Longitude")),
            "external_id": _str(row.get("External ID")),
            "assigned_salesman_id": assigned_salesman_id,
        })
    return records, errors


def _validate_warehouses(db: Session, rows: list[tuple[int, dict]]) -> tuple[list[dict], list[dict]]:
    errors, records = [], []
    manager_cache: dict[str, Employee] = {}
    for row_num, row in rows:
        name = _str(row.get("Name"))
        if not name:
            errors.append({"row": row_num, "error": "Name is required"})
            continue
        manager_name = _str(row.get("Manager Name"))
        if not manager_name:
            errors.append({"row": row_num, "error": "Manager Name is required"})
            continue
        if manager_name not in manager_cache:
            emp = (
                db.query(Employee)
                .filter(Employee.name == manager_name, Employee.role == EmployeeRole.WAREHOUSE_MANAGER)
                .first()
            )
            if not emp:
                errors.append({"row": row_num, "error": f"Warehouse manager '{manager_name}' not found"})
                continue
            manager_cache[manager_name] = emp
        records.append({
            "name": name,
            "location": _str(row.get("Location")),
            "address_line1": _str(row.get("Address Line 1")),
            "address_line2": _str(row.get("Address Line 2")),
            "city": _str(row.get("City")),
            "state": _str(row.get("State")),
            "pincode": _int(row.get("Pincode")),
            "_manager": manager_cache[manager_name],
        })
    return records, errors


def _validate_skus(db: Session, rows: list[tuple[int, dict]], new_brand_names: set[str]) -> tuple[list[dict], list[dict]]:
    errors, records = [], []
    brand_cache: dict[str, int] = {}
    for row_num, row in rows:
        name = _str(row.get("Name"))
        if not name:
            errors.append({"row": row_num, "error": "Name is required"})
            continue
        brand_name = _str(row.get("Brand Name"))
        if not brand_name:
            errors.append({"row": row_num, "error": "Brand Name is required"})
            continue
        # Brand might be from the same workbook (not yet in DB) or already in DB
        if brand_name not in brand_cache:
            if brand_name in new_brand_names:
                # Will be resolved after brands are inserted; store as sentinel
                brand_cache[brand_name] = None  # type: ignore
            else:
                brand = db.query(Brand).filter(Brand.name == brand_name).first()
                if not brand:
                    errors.append({"row": row_num, "error": f"Brand '{brand_name}' not found"})
                    continue
                brand_cache[brand_name] = brand.id
        records.append({
            "name": name,
            "code": _str(row.get("SKU Code")),
            "_brand_name": brand_name,
            "hsn_code": _str(row.get("HSN Code")),
            "mrp": _float(row.get("MRP")),
            "rate": _float(row.get("Rate")),
            "distributor_landing_price": _float(row.get("Distributor Landing Price")),
            "discount_amount": _float(row.get("Discount Amount")) or 0,
            "discount_percent": _float(row.get("Discount %")) or 0,
            "sgst_percent": _float(row.get("SGST %")),
            "cgst_percent": _float(row.get("CGST %")),
            "igst_percent": _float(row.get("IGST %")),
            "weight": _float(row.get("Weight")),
            "length_cm": _float(row.get("Length (cm)")),
            "width_cm": _float(row.get("Width (cm)")),
            "height_cm": _float(row.get("Height (cm)")),
        })
    return records, errors


# --- Main workbook ingestion ---

def _validate_beats(db: Session, rows: list[tuple[int, dict]], new_warehouse_names: set[str]) -> tuple[list[dict], list[dict]]:
    errors, records = [], []
    for row_num, row in rows:
        name = _str(row.get("Name"))
        if not name:
            errors.append({"row": row_num, "error": "Name is required"})
            continue
        warehouse_name = _str(row.get("Warehouse Name"))
        warehouse_id = None
        if warehouse_name and warehouse_name not in new_warehouse_names:
            wh = db.query(Warehouse).filter(Warehouse.name.ilike(warehouse_name)).first()
            if not wh:
                errors.append({"row": row_num, "error": f"Warehouse '{warehouse_name}' not found"})
                continue
            warehouse_id = wh.id
        records.append({"name": name, "_warehouse_name": warehouse_name, "warehouse_id": warehouse_id})
    return records, errors


def ingest_workbook(db: Session, wb) -> dict:
    """
    Validates all sheets first. If any sheet has errors, nothing is written.
    Processes in dependency order: Brands → Retailers → Warehouses → SKUs → Beats.
    """
    # Read rows from each sheet (missing sheet = empty, not an error)
    brand_ws = _get_sheet(wb, "Brands")
    retailer_ws = _get_sheet(wb, "Retailers")
    warehouse_ws = _get_sheet(wb, "Warehouses")
    sku_ws = _get_sheet(wb, "SKUs")
    beat_ws = _get_sheet(wb, "Beats")

    brand_rows = _read_rows(brand_ws) if brand_ws else []
    retailer_rows = _read_rows(retailer_ws) if retailer_ws else []
    warehouse_rows = _read_rows(warehouse_ws) if warehouse_ws else []
    sku_rows = _read_rows(sku_ws) if sku_ws else []
    beat_rows = _read_rows(beat_ws) if beat_ws else []

    # Validate all sheets
    brand_records, brand_errors = _validate_brands(brand_rows)
    retailer_records, retailer_errors = _validate_retailers(db, retailer_rows, brand_records)
    warehouse_records, warehouse_errors = _validate_warehouses(db, warehouse_rows)
    new_brand_names = {r["name"] for r in brand_records}
    new_warehouse_names = {r["name"] for r in warehouse_records}
    sku_records, sku_errors = _validate_skus(db, sku_rows, new_brand_names)
    beat_records, beat_errors = _validate_beats(db, beat_rows, new_warehouse_names)

    all_errors = {
        "brands": brand_errors,
        "retailers": retailer_errors,
        "warehouses": warehouse_errors,
        "skus": sku_errors,
        "beats": beat_errors,
    }
    has_errors = any(v for v in all_errors.values())

    if has_errors:
        return {
            entity: {"inserted": 0, "errors": errs}
            for entity, errs in all_errors.items()
        }

    # All valid — insert in dependency order within a single transaction
    with transactional_session(db):
        # Brands first (SKUs reference them)
        brand_id_map: dict[str, int] = {}
        for r in brand_records:
            brand = Brand(**r)
            db.add(brand)
            db.flush()
            brand_id_map[brand.name] = brand.id

        # Retailers
        for r in retailer_records:
            db.add(Retailer(**r))

        # Warehouses (need flush to get IDs for manager linking)
        warehouse_id_map: dict[str, int] = {}
        for r in warehouse_records:
            manager = r.pop("_manager")
            warehouse = Warehouse(**r)
            db.add(warehouse)
            db.flush()
            manager.warehouse_id = warehouse.id
            warehouse_id_map[warehouse.name] = warehouse.id

        # SKUs (resolve brand IDs — could be newly inserted or pre-existing)
        for r in sku_records:
            brand_name = r.pop("_brand_name")
            brand_id = brand_id_map.get(brand_name)
            if brand_id is None:
                brand = db.query(Brand).filter(Brand.name == brand_name).first()
                brand_id = brand.id if brand else None
            r["brand_id"] = brand_id
            db.add(SKU(**r))

        # Beats (resolve warehouse IDs)
        for r in beat_records:
            wh_name = r.pop("_warehouse_name")
            if wh_name and not r["warehouse_id"]:
                r["warehouse_id"] = warehouse_id_map.get(wh_name)
            db.add(Beat(**{k: v for k, v in r.items() if not k.startswith("_")}))

    return {
        "brands":     {"inserted": len(brand_records),    "errors": []},
        "retailers":  {"inserted": len(retailer_records), "errors": []},
        "warehouses": {"inserted": len(warehouse_records),"errors": []},
        "skus":       {"inserted": len(sku_records),      "errors": []},
        "beats":      {"inserted": len(beat_records),     "errors": []},
    }


# --- Daily Sales Import ---

_SKIP_SHEETS = {
    "recovery", "outstanding", "credit", "collection", "whole data",
    "statement", "pivot", "sheet1", "summary", "bank", "reconciliation",
}

_DATE_PATTERN = re.compile(r"^\d{1,2}\s+\w{3,9}$")

_ISSUE_MAP = {
    "stock shortage": IssueCategory.STOCK_SHORTAGE,
    "stock short": IssueCategory.STOCK_SHORTAGE,
    "expiry": IssueCategory.EXPIRY,
    "return": IssueCategory.RETURN,
    "gst issue": IssueCategory.GST_ISSUE,
    "gst": IssueCategory.GST_ISSUE,
    "salesman issue": IssueCategory.SALESMAN_ISSUE,
    "salesman": IssueCategory.SALESMAN_ISSUE,
    "low amount": IssueCategory.LOW_AMOUNT,
    "shop closed": IssueCategory.SHOP_CLOSED,
    "closed": IssueCategory.SHOP_CLOSED,
}

# Unified column mapping — handles both DLF and City format variants
_COL_MAP = {
    # order date
    "date": "order_date", "sale_date": "order_date",
    # beat / user
    "user": "beat_code",
    # outlet
    "outlet_name": "outlet_name",
    "outlet_id": "outlet_id", "sum of outlet_id": "outlet_id",
    # bill
    "bill_no": "bill_no",
    # amount
    "amount": "amount", "sum of net_sale_amount": "amount", "sale_amount": "amount",
    # payments
    "cash": "cash", "cash payment": "cash",
    "online": "online", "upi": "online", "upi payment": "online",
    "cheque": "cheque", "check": "cheque", "check/cheque": "cheque", "cheque payment": "cheque",
    "credit": "credit", "credit payment": "credit",
    # driver
    "rider": "driver", "driver name": "driver",
    # status
    "status": "status", "staus": "status",
    "panel status": "panel_status", "return on panel": "panel_status",
    # diff / issue / description
    "diff": "diff", "difference": "diff",
    "define": "issue",
    "description": "description", "remark": "description", "remarks": "description",
}

_CONCAT_DATE_PATTERN = re.compile(r"^\d{1,2}[a-z]{3,9}$")


def _is_date_sheet(name: str) -> bool:
    stripped = name.strip().lower()
    stripped = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", stripped)
    # Remove underscores and extra punctuation for matching
    cleaned = re.sub(r"[_\-/]", "", stripped).strip()
    if stripped in _SKIP_SHEETS:
        return False
    for skip in _SKIP_SHEETS:
        if skip in stripped:
            return False
    if _DATE_PATTERN.match(stripped):
        return True
    # Handle concatenated names like "3FEB", "3feb"
    if _CONCAT_DATE_PATTERN.match(cleaned):
        return True
    # Handle names with extra chars like "3 _APR" → after cleanup "3 apr"
    cleaned_spaced = re.sub(r"[_\-/]", " ", stripped).strip()
    cleaned_spaced = re.sub(r"\s+", " ", cleaned_spaced)
    if _DATE_PATTERN.match(cleaned_spaced):
        return True
    return cleaned.isdigit()


def _normalize_row(row: dict, col_map: dict) -> dict:
    normalized = {}
    for key, val in row.items():
        k = re.sub(r"\s+", " ", str(key).strip().lower())
        mapped = col_map.get(k)
        if mapped:
            normalized[mapped] = val
    return normalized


def _parse_issue(val) -> IssueCategory | None:
    if not val:
        return None
    s = str(val).strip().lower()
    return _ISSUE_MAP.get(s, IssueCategory.OTHER if s else None)


def _parse_order_status(val) -> OrderStatus:
    if not val:
        return OrderStatus.DELIVERED
    s = str(val).strip().lower()
    if "cancel" in s or "return" in s:
        return OrderStatus.CANCELLED
    return OrderStatus.DELIVERED


_MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
    "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _month_from_sheet_name(sheet_name: str) -> int | None:
    s = sheet_name.strip().lower()
    for part in s.split():
        cleaned = re.sub(r"[^a-z]", "", part)
        cleaned = re.sub(r"(st|nd|rd|th)$", "", cleaned)
        m = _MONTH_NAMES.get(cleaned)
        if m:
            return m
    # Handle concatenated names like "3feb" — extract alpha portion
    alpha = re.sub(r"[^a-z]", "", s)
    if alpha:
        m = _MONTH_NAMES.get(alpha)
        if m:
            return m
    return None


def _parse_date_from_sheet_or_row(sheet_name: str, row_date_val) -> datetime | None:
    expected_month = _month_from_sheet_name(sheet_name)

    if row_date_val:
        dt = None
        if isinstance(row_date_val, datetime):
            dt = row_date_val
        elif isinstance(row_date_val, date):
            dt = datetime.combine(row_date_val, dtime.min)
        else:
            s = str(row_date_val).strip()
            try:
                dt = datetime.fromisoformat(s)
            except (ValueError, TypeError):
                # Try slash/dash separated: could be d/m/y or m/d/y
                if expected_month:
                    nums = [int(float(p)) for p in re.split(r"[/\-.]", s) if p.strip()]
                    if len(nums) >= 2:
                        year = nums[2] if len(nums) >= 3 else 2026
                        if year < 100:
                            year += 2000
                        a, b = nums[0], nums[1]
                        if a == expected_month:
                            month, day = a, b
                        elif b == expected_month:
                            month, day = b, a
                        else:
                            month, day = expected_month, a
                        try:
                            dt = datetime(year, month, day)
                        except ValueError:
                            pass

        # Correct swapped day/month using sheet name as ground truth
        if dt and expected_month and dt.month != expected_month:
            if dt.day == expected_month and dt.month <= 31:
                try:
                    dt = dt.replace(month=expected_month, day=dt.month)
                except ValueError:
                    pass

        if dt:
            return dt

    # Fallback: derive date from sheet name alone
    s = re.sub(r"[_\-/]", " ", sheet_name.strip())
    s = re.sub(r"\s+", " ", s).strip()
    parts = s.split()
    if len(parts) == 2:
        day_part = re.sub(r"(?i)(st|nd|rd|th)$", "", parts[0])
        month_part = re.sub(r"[^a-zA-Z]", "", parts[1])
        try:
            return datetime.strptime(f"{day_part} {month_part} 2026", "%d %b %Y")
        except ValueError:
            try:
                return datetime.strptime(f"{day_part} {month_part} 2026", "%d %B %Y")
            except ValueError:
                pass
    # Handle concatenated like "3FEB"
    m = re.match(r"(\d{1,2})([a-zA-Z]{3,9})", s.replace(" ", ""))
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} 2026", "%d %b %Y")
        except ValueError:
            pass
    return None


def _add_import_payments(db: Session, order_id: int, cash: float, online: float, cheque: float, order_date, ts: int) -> int:
    count = 0
    if cash > 0:
        db.add(Account(
            order_id=order_id, amount=round(cash, 2),
            transaction_reference=f"IMP-CASH-{order_id}-{ts}",
            payment_mode=PaymentMode.CASH, created_at=order_date,
        ))
        count += 1
    if online > 0:
        db.add(Account(
            order_id=order_id, amount=round(online, 2),
            transaction_reference=f"IMP-UPI-{order_id}-{ts}",
            payment_mode=PaymentMode.UPI, created_at=order_date,
        ))
        count += 1
    if cheque > 0:
        db.add(Account(
            order_id=order_id, amount=round(cheque, 2),
            transaction_reference=f"IMP-CHQ-{order_id}-{ts}",
            payment_mode=PaymentMode.CHEQUE, created_at=order_date,
        ))
        count += 1
    return count


def _reconcile_payment_status(order: Order, cash: float, online: float, cheque: float, credit_amt: float):
    if credit_amt <= 0 and (cash > 0 or online > 0 or cheque > 0):
        order.payment_status = PaymentStatus.PAID
    elif cash > 0 or online > 0 or cheque > 0:
        order.payment_status = PaymentStatus.PARTIAL


class DailySalesImportBlockedError(ValueError):
    """Raised while the legacy importer cannot create accurate invoice line items."""


def ingest_daily_sales_workbook(db: Session, wb, warehouse_id: int, current_user=None) -> dict:
    raise DailySalesImportBlockedError(
        "RPT-10: daily-sales import is blocked because summary Amount values cannot "
        "safely produce invoice line items, taxes, or immutable invoice totals"
    )

    employee_cache: dict[str, Employee | None] = {}
    retailer_cache: dict[str, Retailer] = {}
    beat_cache: dict[str, Beat] = {}
    bill_cache: dict[str, Order] = {}
    errors = []
    orders_created = 0
    orders_updated = 0
    payments_created = 0
    recovery_entries = 0
    retailers_matched = 0
    retailers_new = 0
    beats_created = 0

    def _lookup_employee(name_or_code: str, role: EmployeeRole) -> Employee | None:
        key = f"{name_or_code}:{role.value}"
        if key not in employee_cache:
            emp = db.query(Employee).filter(Employee.name == name_or_code, Employee.role == role).first()
            if not emp:
                emp = db.query(Employee).filter(Employee.name.ilike(f"%{name_or_code}%"), Employee.role == role).first()
            employee_cache[key] = emp
        return employee_cache[key]

    def _lookup_or_create_retailer(name: str, external_id: str | None = None) -> Retailer:
        cache_key = name.strip().lower()
        if cache_key in retailer_cache:
            return retailer_cache[cache_key]
        if external_id:
            r = db.query(Retailer).filter(Retailer.external_id == str(external_id)).first()
            if r:
                retailer_cache[cache_key] = r
                return r
        r = db.query(Retailer).filter(Retailer.name.ilike(name.strip())).first()
        if r:
            if external_id and not r.external_id:
                r.external_id = str(external_id)
            retailer_cache[cache_key] = r
            return r
        nonlocal retailers_new
        retailers_new += 1
        r = Retailer(name=name.strip(), external_id=str(external_id) if external_id else None)
        db.add(r)
        db.flush()
        retailer_cache[cache_key] = r
        return r

    def _lookup_or_create_beat(name: str) -> Beat:
        key = name.strip()
        if key in beat_cache:
            return beat_cache[key]
        b = db.query(Beat).filter(Beat.name == key).first()
        if b:
            beat_cache[key] = b
            return b
        nonlocal beats_created
        beats_created += 1
        b = Beat(name=key, warehouse_id=warehouse_id)
        db.add(b)
        db.flush()
        beat_cache[key] = b
        return b

    try:
        # Process date sheets
        for sheet_name in wb.sheetnames:
            if not _is_date_sheet(sheet_name):
                continue

            ws = wb[sheet_name]
            rows = _read_rows(ws)
            if not rows:
                continue

            for row_num, raw_row in rows:
                row = _normalize_row(raw_row, _COL_MAP)

                bill_no = _str(row.get("bill_no"))
                outlet_name = _str(row.get("outlet_name"))
                amount = _float(row.get("amount"))

                if not outlet_name and not bill_no:
                    continue
                if not outlet_name:
                    errors.append({"sheet": sheet_name, "row": row_num, "error": "Missing outlet name"})
                    continue

                retailer = _lookup_or_create_retailer(
                    outlet_name, _str(row.get("outlet_id"))
                )
                retailers_matched += 1

                beat_code = _str(row.get("beat_code"))
                beat = _lookup_or_create_beat(beat_code) if beat_code else None

                driver = None
                driver_name = _str(row.get("driver"))
                if driver_name:
                    driver = _lookup_employee(driver_name, EmployeeRole.DRIVER)

                order_date = _parse_date_from_sheet_or_row(sheet_name, row.get("order_date"))
                status = _parse_order_status(row.get("status"))
                issue = _parse_issue(row.get("issue"))

                cash = _float(row.get("cash")) or 0
                online = _float(row.get("online")) or 0
                credit_amt = _float(row.get("credit")) or 0
                cheque = _float(row.get("cheque")) or 0

                changed_by_id = getattr(current_user, "id", None)

                # Duplicate bill → later sheet is the latest truth
                existing_order = bill_cache.get(bill_no) if bill_no else None
                if existing_order:
                    old_status = existing_order.status
                    existing_order.status = status
                    if beat and not existing_order.beat_id:
                        existing_order.beat_id = beat.id
                    if driver:
                        existing_order.delivery_driver_id = driver.id
                    if status == OrderStatus.DELIVERED and order_date:
                        existing_order.delivery_date = order_date
                    if issue:
                        existing_order.issue_category = issue
                    panel = _str(row.get("panel_status"))
                    if panel:
                        existing_order.panel_status = panel
                    desc = _str(row.get("description"))
                    if desc:
                        existing_order.description = desc

                    db.add(OrderTrail(
                        order_id=existing_order.id,
                        order_status=status,
                        description=f"Import: {old_status.value} → {status.value}",
                        changed_by_id=changed_by_id,
                        created_at=order_date,
                    ))
                    orders_updated += 1

                    ts = int(order_date.timestamp()) if order_date else 0
                    payments_created += _add_import_payments(
                        db, existing_order.id, cash, online, cheque, order_date, ts
                    )
                    _reconcile_payment_status(existing_order, cash, online, cheque, credit_amt)
                    continue

                payment_status = PaymentStatus.CREDIT
                if credit_amt <= 0 and (cash > 0 or online > 0 or cheque > 0):
                    payment_status = PaymentStatus.PAID
                elif cash > 0 or online > 0 or cheque > 0:
                    payment_status = PaymentStatus.PARTIAL

                db_order = Order(
                    from_entity_type="WAREHOUSE",
                    from_entity_id=warehouse_id,
                    to_entity_type="RETAILER",
                    to_entity_id=retailer.id,
                    status=status,
                    payment_status=payment_status,
                    beat_id=beat.id if beat else None,
                    delivery_driver_id=driver.id if driver else None,
                    delivery_date=order_date if status == OrderStatus.DELIVERED else None,
                    panel_status=_str(row.get("panel_status")),
                    issue_category=issue,
                    description=_str(row.get("description")),
                    created_at=order_date,
                )
                db.add(db_order)
                db.flush()
                orders_created += 1

                # This importer creates no OrderItem rows for these summary-level bills, so
                # there is no item-ordering constraint to respect — issue immediately after
                # the order flush. A historical bill number is an externally assigned
                # identity, not a candidate for the sequence (D-04); it is dated when the
                # supply happened (order_date), not now.
                if bill_no:
                    issue_invoice_for_order(db, db_order, invoice_number=bill_no, invoice_date=order_date)

                db.add(OrderTrail(
                    order_id=db_order.id,
                    order_status=status,
                    description="Imported from Excel",
                    changed_by_id=changed_by_id,
                    created_at=order_date,
                ))

                if bill_no:
                    bill_cache[bill_no] = db_order

                ts = int(order_date.timestamp()) if order_date else 0
                payments_created += _add_import_payments(
                    db, db_order.id, cash, online, cheque, order_date, ts
                )


        # Process Recovery sheet (DLF only)
        recovery_ws = _get_sheet(wb, "Recovery")
        if recovery_ws:
            recovery_rows = _read_rows(recovery_ws)
            for row_num, raw_row in recovery_rows:
                bill_no = _str(raw_row.get("Full Bill_No"))
                if not bill_no:
                    continue
                amount = _float(raw_row.get("Amount"))
                if not amount or amount <= 0:
                    continue

                order = bill_cache.get(bill_no)
                if order is None:
                    matched_invoice = db.query(Invoice).filter(Invoice.invoice_number == bill_no).first()
                    order = matched_invoice.order if matched_invoice else None
                if not order:
                    errors.append({"sheet": "Recovery", "row": row_num, "error": f"Bill {bill_no} not found"})
                    continue

                rec_date_val = raw_row.get("Date")
                rec_date = None
                if isinstance(rec_date_val, datetime):
                    rec_date = rec_date_val
                elif isinstance(rec_date_val, date):
                    rec_date = datetime.combine(rec_date_val, dtime.min)

                collector_name = _str(raw_row.get("Sm Name")) or _str(raw_row.get("Sm name"))
                collector = _lookup_employee(collector_name, EmployeeRole.SALESMAN) if collector_name else None
                if not collector and collector_name:
                    collector = _lookup_employee(collector_name, EmployeeRole.DRIVER)

                status_str = _str(raw_row.get("Status")) or ""
                mode = PaymentMode.CASH
                sl = status_str.lower()
                if "online" in sl or "upi" in sl:
                    mode = PaymentMode.UPI
                elif "cheque" in sl or "cheaq" in sl:
                    mode = PaymentMode.CHEQUE

                cheque_num = _str(raw_row.get("Che Num")) or _str(raw_row.get("Cheque Number"))
                cheque_name = _str(raw_row.get("Che Name")) or _str(raw_row.get("Cheque Name"))

                ref = f"IMP-REC-{order.id}-{row_num}-{int(rec_date.timestamp()) if rec_date else 0}"
                db.add(Account(
                    order_id=order.id, amount=round(amount, 2),
                    transaction_reference=ref,
                    payment_mode=mode,
                    collected_by_id=collector.id if collector else None,
                    collection_date=rec_date,
                    cheque_number=cheque_num,
                    cheque_name=cheque_name,
                    created_at=rec_date,
                ))
                recovery_entries += 1

                # Update payment status
                from app.services.finance import calculate_order_outstanding
                db.flush()
                outstanding = calculate_order_outstanding(order)
                if outstanding <= 0:
                    order.payment_status = PaymentStatus.PAID
                else:
                    order.payment_status = PaymentStatus.PARTIAL

        if errors:
            raise _ImportAbort()

    except _ImportAbort:
        db.rollback()
        return {
            "format_detected": "unified",
            "orders_created": 0,
            "orders_updated": 0,
            "payments_created": 0,
            "recovery_entries": 0,
            "retailers_matched": 0,
            "retailers_new": 0,
            "beats_created": 0,
            "errors": errors,
        }

    db.commit()
    return {
        "format_detected": "unified",
        "orders_created": orders_created,
        "orders_updated": orders_updated,
        "payments_created": payments_created,
        "recovery_entries": recovery_entries,
        "retailers_matched": retailers_matched,
        "retailers_new": retailers_new,
        "beats_created": beats_created,
        "errors": [],
    }
