"""Filters for the templates: money and times the way people read them.

Times are shown on the store's clock (Asia/Manila).
"""
from .dates import local_today, to_local
from .money import peso, plain_peso


def nice_day(day):
    """Nov 3, 2026 (written by hand: %-d doesn't work on Windows)."""
    return f"{day:%b} {day.day}, {day.year}"


def short_day(day):
    """Nov 3 this year, Nov 3, 2025 in other years."""
    return f"{day:%b} {day.day}" if day.year == local_today().year else nice_day(day)


def nice_time(moment):
    """2:30 PM"""
    local = to_local(moment)
    return f"{local.hour % 12 or 12}:{local:%M %p}"


def nice_date(moment):
    return nice_day(to_local(moment).date())


def nice_datetime(moment):
    """Nov 3, 2026, 2:30 PM"""
    return f"{nice_date(moment)}, {nice_time(moment)}"


def per_piece(unit):
    """What one piece costs when bought in this unit: a case of 24 for ₱440 is ₱18.33 each."""
    return peso(round(unit.retail_price / unit.pieces_per_unit))


def mobile(number):
    """09171234567 -> 0917 123 4567, the way people write it."""
    if len(number or "") == 11 and number.isdigit():
        return f"{number[:4]} {number[4:7]} {number[7:]}"
    return number or ""


def pieces(count):
    return f"{count:,} piece" if count == 1 else f"{count:,} pieces"


def init_app(app):
    for name, function in {
        "peso": peso, "plain_peso": plain_peso, "nice_day": nice_day, "short_day": short_day,
        "nice_time": nice_time, "nice_date": nice_date, "nice_datetime": nice_datetime, "per_piece": per_piece,
        "pieces": pieces, "mobile": mobile,
    }.items():
        app.jinja_env.filters[name] = function
