from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.bill_label import BillLabel

HISTORY_RETENTION_DAYS = 7
DEFAULT_SORT_BY = "created_at"
DEFAULT_SORT_DIR = "desc"
_SORTABLE_FIELDS = {
    "bill_no": "bill_no",
    "outlet_name": "outlet_name",
    "gst_no": "gst_no",
    "beat": "beat",
    "invoice_date": "invoice_date",
    "original_filename": "original_filename",
    "created_at": "created_at",
}


def _parse_invoice_date(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = value.strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def _sort_key(record: BillLabel, sort_by: str):
    if sort_by == "invoice_date":
        return _parse_invoice_date(record.invoice_date) or datetime.min

    value = getattr(record, _SORTABLE_FIELDS[sort_by], None)
    if isinstance(value, str):
        return value.casefold()
    return value or datetime.min


def create_bill_label(
    db: Session,
    user_id: int,
    bill_no: str | None = None,
    invoice_date: str | None = None,
    salesman_name: str | None = None,
    beat: str | None = None,
    gst_no: str | None = None,
    outlet_name: str | None = None,
    original_filename: str | None = None,
) -> BillLabel:
    record = BillLabel(
        user_id=user_id,
        bill_no=bill_no,
        invoice_date=invoice_date,
        salesman_name=salesman_name,
        beat=beat,
        gst_no=gst_no,
        outlet_name=outlet_name,
        original_filename=original_filename,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def update_bill_label(db: Session, bill_label_id: int, user_id: int, **fields) -> BillLabel:
    record = (
        db.query(BillLabel)
        .filter(BillLabel.id == bill_label_id, BillLabel.user_id == user_id)
        .first()
    )
    if not record:
        raise ValueError("BillLabel not found")

    for key, value in fields.items():
        if value is not None and hasattr(record, key):
            setattr(record, key, value)

    db.commit()
    db.refresh(record)
    return record


def list_bill_labels(
    db: Session,
    user_id: int,
    skip: int = 0,
    limit: int = 50,
    sort_by: str = DEFAULT_SORT_BY,
    sort_dir: str = DEFAULT_SORT_DIR,
) -> tuple[list[BillLabel], int]:
    cutoff = datetime.utcnow() - timedelta(days=HISTORY_RETENTION_DAYS)
    items = db.query(BillLabel).filter(
        BillLabel.user_id == user_id,
        BillLabel.created_at >= cutoff,
    ).all()

    selected_sort = sort_by if sort_by in _SORTABLE_FIELDS else DEFAULT_SORT_BY
    reverse = sort_dir.lower() != "asc"

    present = []
    missing = []
    for item in items:
        value = getattr(item, _SORTABLE_FIELDS[selected_sort], None)
        if value in (None, ""):
            missing.append(item)
        else:
            present.append(item)

    present.sort(key=lambda item: _sort_key(item, selected_sort), reverse=reverse)
    ordered = present + missing
    total = len(ordered)
    return ordered[skip : skip + limit], total


def get_bill_label(db: Session, bill_label_id: int, user_id: int) -> BillLabel | None:
    return (
        db.query(BillLabel)
        .filter(BillLabel.id == bill_label_id, BillLabel.user_id == user_id)
        .first()
    )


def delete_bill_label(db: Session, bill_label_id: int, user_id: int) -> bool:
    record = (
        db.query(BillLabel)
        .filter(BillLabel.id == bill_label_id, BillLabel.user_id == user_id)
        .first()
    )
    if not record:
        return False

    db.delete(record)
    db.commit()
    return True


def enrich_record(record: BillLabel) -> dict:
    return {
        "id": record.id,
        "user_id": record.user_id,
        "bill_no": record.bill_no,
        "invoice_date": record.invoice_date,
        "salesman_name": record.salesman_name,
        "beat": record.beat,
        "gst_no": record.gst_no,
        "outlet_name": record.outlet_name,
        "original_filename": record.original_filename,
        "created_at": record.created_at,
    }
