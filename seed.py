from app.db.session import SessionLocal
from app.models import User, Employee
from app.models.enums import EmployeeRole
from app.core.security import get_password_hash

db = SessionLocal()

admin_employee = db.query(Employee).filter(Employee.email == "admin@ascend.com").first()
if not admin_employee:
    admin_employee = Employee(name="Admin", email="admin@ascend.com", role=EmployeeRole.ADMIN)
    db.add(admin_employee)
    db.commit()
    db.refresh(admin_employee)

admin_user = db.query(User).filter(User.email == "admin@ascend.com").first()
if not admin_user:
    admin_user = User(
        email="admin@ascend.com",
        password_hash=get_password_hash("password"),
        employee_id=admin_employee.id,
        role=EmployeeRole.ADMIN
    )
    db.add(admin_user)
    db.commit()

db.close()

print("Admin user ensured")
