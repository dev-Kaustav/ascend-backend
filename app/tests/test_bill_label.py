from datetime import datetime, timedelta

from app.models import BillLabel, User
from app.models.enums import EmployeeRole
from app.services import bill_label as svc


def _create_user(db, email="bill-labels@ascend.com"):
    user = User(email=email, password_hash="x", role=EmployeeRole.ADMIN)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_create_bill_label_persists_metadata_only(db):
    user = _create_user(db)

    record = svc.create_bill_label(
        db=db,
        user_id=user.id,
        bill_no="VN25-26/10216",
        invoice_date="28-03-2026",
        salesman_name="GGN GTM-34",
        beat="Monday_GGN",
        gst_no="07ABCDE1234F1Z5",
        outlet_name="Balaji Kiryana Store",
        original_filename="Bizom Orders.pdf — page 1",
    )

    assert record.bill_no == "VN25-26/10216"
    assert record.beat == "Monday_GGN"
    assert record.gst_no == "07ABCDE1234F1Z5"
    assert record.original_filename == "Bizom Orders.pdf — page 1"


def test_update_bill_label_allows_metadata_edits_for_owner(db):
    user = _create_user(db)
    record = BillLabel(
        user_id=user.id,
        bill_no="VN25-26/10213",
        beat="Old Beat",
        original_filename="bill.pdf",
    )
    db.add(record)
    db.commit()

    updated = svc.update_bill_label(
        db,
        record.id,
        user.id,
        beat="Monday_GGN GTM-34",
        gst_no="07ABCDE1234F1Z5",
    )

    assert updated.beat == "Monday_GGN GTM-34"
    assert updated.gst_no == "07ABCDE1234F1Z5"


def test_list_bill_labels_only_returns_last_seven_days(db):
    user = _create_user(db)
    recent = BillLabel(
        user_id=user.id,
        bill_no="RECENT",
        created_at=datetime.utcnow() - timedelta(days=2),
    )
    stale = BillLabel(
        user_id=user.id,
        bill_no="STALE",
        created_at=datetime.utcnow() - timedelta(days=10),
    )
    db.add_all([recent, stale])
    db.commit()

    items, total = svc.list_bill_labels(db, user.id)

    assert total == 1
    assert [item.bill_no for item in items] == ["RECENT"]


def test_list_bill_labels_supports_sorting_by_columns(db):
    user = _create_user(db)
    first = BillLabel(
        user_id=user.id,
        bill_no="RB25-26/000120",
        outlet_name="Hari Om",
        invoice_date="28-03-2026",
        beat="Saturday_GGN",
        gst_no="06DLXPB3523G1Z3",
        original_filename="Bizom Orders.pdf — page 10",
        created_at=datetime.utcnow() - timedelta(days=1),
    )
    second = BillLabel(
        user_id=user.id,
        bill_no="RB25-26/000119",
        outlet_name="Balaji",
        invoice_date="27-03-2026",
        beat="Friday_GGN",
        gst_no="07ABCDE1234F1Z5",
        original_filename="Bizom Orders.pdf — page 11",
        created_at=datetime.utcnow() - timedelta(days=2),
    )
    db.add_all([first, second])
    db.commit()

    items, total = svc.list_bill_labels(db, user.id, sort_by="bill_no", sort_dir="asc")
    assert total == 2
    assert [item.bill_no for item in items] == ["RB25-26/000119", "RB25-26/000120"]

    items, _ = svc.list_bill_labels(db, user.id, sort_by="invoice_date", sort_dir="desc")
    assert [item.invoice_date for item in items] == ["28-03-2026", "27-03-2026"]
