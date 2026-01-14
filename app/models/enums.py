from enum import Enum

class EmployeeRole(str, Enum):
    ADMIN = "ADMIN"
    SALESMAN = "SALESMAN"
    ACCOUNTANT = "ACCOUNTANT"
    WAREHOUSE_MANAGER = "WAREHOUSE_MANAGER"
    RETAILER = "RETAILER"
    BRAND = "BRAND"

class OrderType(str, Enum):
    INCOMING = "INCOMING"
    OUTGOING = "OUTGOING"

class OrderStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"

class TransactionType(str, Enum):
    IN = "IN"
    OUT = "OUT"
    RETURN = "RETURN"

class EntityType(str, Enum):
    BRAND = "BRAND"
    RETAILER = "RETAILER"

class PaymentStatus(str, Enum):
    UNPAID = "UNPAID"
    PARTIAL = "PARTIAL"
    PAID = "PAID"
