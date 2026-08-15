from enum import Enum

from sqlalchemy import CheckConstraint

# 28 states + 8 union territories. This module is the single source of truth for the
# canonical set; app/db/migrations/versions/0043_state_check_constraint.py imports this
# constant so the migration and the models cannot drift from each other.
# Union territories: Andaman and Nicobar Islands, Chandigarh,
# Dadra and Nagar Haveli and Daman and Diu, Delhi, Jammu and Kashmir, Ladakh,
# Lakshadweep, Puducherry.
INDIAN_STATES: tuple[str, ...] = (
    "Andaman and Nicobar Islands",
    "Andhra Pradesh",
    "Arunachal Pradesh",
    "Assam",
    "Bihar",
    "Chandigarh",
    "Chhattisgarh",
    "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi",
    "Goa",
    "Gujarat",
    "Haryana",
    "Himachal Pradesh",
    "Jammu and Kashmir",
    "Jharkhand",
    "Karnataka",
    "Kerala",
    "Ladakh",
    "Lakshadweep",
    "Madhya Pradesh",
    "Maharashtra",
    "Manipur",
    "Meghalaya",
    "Mizoram",
    "Nagaland",
    "Odisha",
    "Puducherry",
    "Punjab",
    "Rajasthan",
    "Sikkim",
    "Tamil Nadu",
    "Telangana",
    "Tripura",
    "Uttar Pradesh",
    "Uttarakhand",
    "West Bengal",
)


def state_check_constraint(table_name: str, column: str = "state") -> CheckConstraint:
    states_list = ", ".join(f"'{s}'" for s in INDIAN_STATES)
    return CheckConstraint(
        f"{column} IS NULL OR {column} IN ({states_list})",
        name=f"ck_{table_name}_{column}",
    )


class EmployeeRole(str, Enum):
    ADMIN = "ADMIN"
    SALESMAN = "SALESMAN"
    ACCOUNTANT = "ACCOUNTANT"
    WAREHOUSE_MANAGER = "WAREHOUSE_MANAGER"
    DRIVER = "DRIVER"
    RETAILER = "RETAILER"
    BRAND = "BRAND"

class OrderStatus(str, Enum):
    PENDING = "PENDING"
    READY_TO_SHIP = "READY_TO_SHIP"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    RETURNED = "RETURNED"
    CANCELLED = "CANCELLED"

class AssignmentStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"

class TransactionType(str, Enum):
    IN = "IN"
    OUT = "OUT"
    RETURN = "RETURN"

class EntityType(str, Enum):
    BRAND = "BRAND"
    RETAILER = "RETAILER"

class PaymentStatus(str, Enum):
    CREDIT = "CREDIT"
    PARTIAL = "PARTIAL"
    PAID = "PAID"
    UNPAID = "UNPAID"

class PaymentMode(str, Enum):
    CASH = "CASH"
    UPI = "UPI"
    CHEQUE = "CHEQUE"
    ONLINE = "ONLINE"

class IssueCategory(str, Enum):
    STOCK_SHORTAGE = "STOCK_SHORTAGE"
    EXPIRY = "EXPIRY"
    RETURN = "RETURN"
    GST_ISSUE = "GST_ISSUE"
    SALESMAN_ISSUE = "SALESMAN_ISSUE"
    LOW_AMOUNT = "LOW_AMOUNT"
    SHOP_CLOSED = "SHOP_CLOSED"
    OTHER = "OTHER"

class InvoiceStatus(str, Enum):
    ISSUED = "ISSUED"

class InvoiceType(str, Enum):
    B2B = "B2B"
    B2C = "B2C"

class SupplyType(str, Enum):
    REGULAR = "REGULAR"
    SEZWP = "SEZWP"
    SEZWOP = "SEZWOP"
    EXPWP = "EXPWP"
    EXPWOP = "EXPWOP"
    DEXP = "DEXP"
