from enum import Enum

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
