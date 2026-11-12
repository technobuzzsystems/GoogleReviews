"""Business-owner logins. One login sees only the business it is linked to."""

from sqlalchemy.orm import Session

from models.domain_models import BusinessConfigModel, User, UserRole
from services.auth_service import hash_password


def save_owner_login(db: Session, business_key: str, username: str, password: str) -> User:
    business = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == business_key).first()
    if not business:
        raise ValueError("Business not found.")

    username = (username or "").strip().lower()
    password = password or ""
    if len(username) < 3:
        raise ValueError("Username must be at least 3 characters.")

    owner = None
    if business.owner_user_id:
        owner = db.query(User).filter(User.id == business.owner_user_id).first()
        if owner and owner.role != UserRole.OWNER:
            owner = None

    taken = db.query(User).filter(User.username == username).first()
    if taken and (not owner or taken.id != owner.id):
        raise ValueError("That username is already in use.")

    if owner is None:
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters.")
        owner = User(
            username=username,
            password_hash=hash_password(password),
            role=UserRole.OWNER,
            full_name=business.name or "",
            is_active=True,
        )
        db.add(owner)
        db.flush()
        business.owner_user_id = owner.id
    else:
        owner.username = username
        owner.full_name = owner.full_name or business.name or ""
        owner.is_active = True
        owner.role = UserRole.OWNER
        if password:
            if len(password) < 6:
                raise ValueError("Password must be at least 6 characters.")
            owner.password_hash = hash_password(password)
        business.owner_user_id = owner.id

    db.commit()
    db.refresh(owner)
    return owner
