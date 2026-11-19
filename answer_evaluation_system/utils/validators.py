"""Input validation and sanitisation."""
import re

import config
from utils.helpers import ValidationError

_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value, max_len: int = 5000) -> str:
    text = _CONTROL.sub("", str(value or "")).strip()
    return text[:max_len]


def require_text(value, label: str, max_len: int = 5000) -> str:
    text = clean_text(value, max_len)
    if not text:
        raise ValidationError(f"{label} cannot be empty.")
    return text


def validate_email(email: str) -> str:
    email = clean_text(email, 254).lower()
    if not _EMAIL.match(email):
        raise ValidationError("Please enter a valid email address.")
    return email


def validate_name(name: str) -> str:
    name = require_text(name, "Name", 120)
    if len(name) < 2:
        raise ValidationError("Name must be at least 2 characters.")
    return name


def validate_password(password: str) -> str:
    if not password or len(password) < config.MIN_PASSWORD_LENGTH:
        raise ValidationError(f"Password must be at least {config.MIN_PASSWORD_LENGTH} characters.")
    if not (any(c.isalpha() for c in password) and any(c.isdigit() for c in password)):
        raise ValidationError("Password must contain both letters and digits.")
    return password


def validate_registration_number(value: str) -> str:
    value = clean_text(value, 40).upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9/\-_.]{2,39}", value):
        raise ValidationError("Registration number must be 3-40 characters (letters, digits, - _ / .).")
    return value


def validate_positive_number(value, label: str, integer: bool = False) -> float:
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{label} must be a number.")
    if number <= 0:
        raise ValidationError(f"{label} must be greater than zero.")
    return number


def parse_list(value) -> list:
    """Split comma/newline/semicolon separated text into a clean list (keeps 'a|b' synonym syntax)."""
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = re.split(r"[,\n;]+", str(value or ""))
    seen, out = set(), []
    for item in items:
        item = clean_text(item, 300)
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out
