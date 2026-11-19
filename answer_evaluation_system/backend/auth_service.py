"""Registration, login (with lockout) and password changes."""
from datetime import timedelta

import config
from backend import user_service
from database import repository as repo
from database.connection import transaction
from utils.helpers import AuthenticationError, ValidationError, fmt_dt, now, parse_dt
from utils.security import hash_password, verify_password
from utils.validators import validate_email, validate_password

_DUMMY_HASH = hash_password("timing-equaliser-1")
GENERIC_ERROR = "Invalid email or password."


def register_student(name, email, registration_number, password, class_id=None):
    with transaction() as conn:
        return user_service.create_user_record(conn, name, email, password, "student", registration_number, class_id)


def register_teacher(name, email, password):
    """Teachers register inactive; an administrator must activate the account."""
    with transaction() as conn:
        return user_service.create_user_record(conn, name, email, password, "teacher", is_active=False)


def login(email, password, at=None):
    at = at or now()
    email = (email or "").strip().lower()
    row = repo.fetch_one("SELECT * FROM users WHERE email = ?", (email,))
    if not row:
        verify_password(password or "", _DUMMY_HASH)
        raise AuthenticationError(GENERIC_ERROR)
    if row["locked_until"] and parse_dt(row["locked_until"]) > at:
        raise AuthenticationError(f"Too many failed attempts. Try again after {row['locked_until']}.")
    if not verify_password(password or "", row["password_hash"]):
        attempts = row["failed_attempts"] + 1
        data = {"failed_attempts": attempts}
        if attempts >= config.MAX_LOGIN_ATTEMPTS:
            data.update(failed_attempts=0, locked_until=fmt_dt(at + timedelta(minutes=config.LOCKOUT_MINUTES)))
        with transaction() as conn:
            repo.update("users", row["id"], data, conn)
        raise AuthenticationError(GENERIC_ERROR)
    if not row["is_active"]:
        raise AuthenticationError("Your account is inactive or awaiting administrator approval.")
    with transaction() as conn:
        repo.update("users", row["id"], {"failed_attempts": 0, "locked_until": None, "last_login": fmt_dt(at)}, conn)
    return user_service.public_user(repo.fetch_one("SELECT * FROM users WHERE id=?", (row["id"],)))


def change_password(actor, old_password, new_password):
    row = repo.fetch_one("SELECT password_hash FROM users WHERE id=?", (actor["id"],))
    if not row or not verify_password(old_password or "", row["password_hash"]):
        raise AuthenticationError("Current password is incorrect.")
    validate_password(new_password)
    if old_password == new_password:
        raise ValidationError("New password must differ from the current password.")
    with transaction() as conn:
        repo.update("users", actor["id"], {"password_hash": hash_password(new_password)}, conn)
