from sqlalchemy.orm import Session

from app.models import AccessRule


def list_access_rules(db: Session):
    return db.query(AccessRule).order_by(AccessRule.path.asc()).all()


def upsert_access_rule(db: Session, path: str, roles: list[str], permissions: list[str]):
    rule = db.query(AccessRule).filter(AccessRule.path == path).first()
    if rule:
        rule.roles = roles
        rule.permissions = permissions
    else:
        rule = AccessRule(path=path, roles=roles, permissions=permissions)
        db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def delete_access_rule(db: Session, rule_id: int):
    rule = db.query(AccessRule).filter(AccessRule.id == rule_id).first()
    if not rule:
        raise ValueError("Access rule not found")
    db.delete(rule)
    db.commit()
    return {"status": "ok"}
