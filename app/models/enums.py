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
    BOOKED = "BOOKED"
    READY_TO_SHIP = "READY_TO_SHIP"
    PENDING = "PENDING"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    CONFIRMED = "CONFIRMED"
    DELIVERED = "DELIVERED"
    RETURNED = "RETURNED"
    CANCELLED = "CANCELLED"

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
