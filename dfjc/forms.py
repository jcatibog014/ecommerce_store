"""Reading form fields: trimmed text, whole numbers and prices.

Each helper returns (value, problem). The problem is None when the value is fine,
else a short sentence for the page.
"""
from .money import MAX_PRICE, parse_peso, peso


def text(form, name, limit, required=False, label="This"):
    value = (form.get(name) or "").replace("\r\n", "\n").strip()
    if required and not value:
        return value, f"{label} is needed."
    if len(value) > limit:
        return value, f"Use at most {limit:,} characters."
    return value, None


def whole_number(form, name, low=0, high=1_000_000, default=None):
    """Digits with optional commas ("1,200") -> 1200. Empty -> default."""
    raw = (form.get(name) or "").replace(",", "").replace(" ", "").strip()
    if not raw:
        return default, None if default is not None else f"Use a whole number from {low:,} to {high:,}."
    if not raw.isdigit() or not low <= int(raw) <= high:
        return default, f"Use a whole number from {low:,} to {high:,}."
    return int(raw), None


def price(form, name, required=True):
    """A peso amount -> centavos ("20.50" -> 2050). Empty -> None."""
    raw = (form.get(name) or "").strip()
    if not raw:
        return None, "A price is needed." if required else None
    value = parse_peso(raw)
    if value is None:
        return None, "Use a price like 20 or 20.50."
    if value > MAX_PRICE:
        return None, f"Use a price up to {peso(MAX_PRICE)}."
    return value, None


def checkbox(form, name):
    return form.get(name) == "1"
