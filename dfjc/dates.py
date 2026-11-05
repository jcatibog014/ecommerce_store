"""Times and days.

Moments (an order placed, a payment) are stored as naive UTC datetimes from
utcnow(). People see them on the store's clock: DFJC is in the Philippines,
so every page uses Asia/Manila.
"""
from datetime import UTC, date, datetime, timedelta
from functools import cache
from zoneinfo import ZoneInfo

STORE_TIMEZONE = "Asia/Manila"


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


@cache
def zone(name=STORE_TIMEZONE):
    return ZoneInfo(name)


def to_local(moment, tz_name=STORE_TIMEZONE):
    """Naive UTC from the database -> an aware datetime on the store's clock."""
    return moment.replace(tzinfo=UTC).astimezone(zone(tz_name))


def to_utc(local, tz_name=STORE_TIMEZONE):
    """A naive datetime on the store's clock -> naive UTC for the database."""
    return local.replace(tzinfo=zone(tz_name)).astimezone(UTC).replace(tzinfo=None)


def local_today(moment=None):
    """The calendar day it is at the store now (or at moment, naive UTC)."""
    return to_local(moment or utcnow()).date()


def day_start_utc(day):
    """Midnight at the start of day on the store's clock, as naive UTC."""
    return to_utc(datetime(day.year, day.month, day.day))


def parse_day(text):
    """What a <input type="date"> sends ("2026-11-12") -> a date, or None if empty or wrong."""
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return None


def week_start(day):
    """The Monday on or before day."""
    return day - timedelta(days=day.weekday())
