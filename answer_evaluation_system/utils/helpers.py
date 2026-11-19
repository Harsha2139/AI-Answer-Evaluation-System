"""Shared exceptions and small helpers."""
import json
from datetime import datetime

DT_FORMAT = "%Y-%m-%d %H:%M:%S"


class AppError(Exception):
    """Base class for errors that are safe to show to the user."""


class ValidationError(AppError):
    pass


class PermissionDenied(AppError):
    pass


class NotFound(AppError):
    pass


class AuthenticationError(AppError):
    pass


class ConflictError(AppError):
    pass


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def fmt_dt(value: datetime) -> str:
    return value.strftime(DT_FORMAT)


def now_str() -> str:
    return fmt_dt(now())


def parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    if not value:
        raise ValidationError("A date and time is required.")
    text = str(value).strip().replace("T", " ")
    for fmt in (DT_FORMAT, "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValidationError(f"Invalid date/time: {value!r}. Use YYYY-MM-DD HH:MM.")


def round_half(value: float) -> float:
    """Round to the nearest 0.5 mark."""
    return round(value * 2) / 2


def percent(part, whole) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(text, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default
