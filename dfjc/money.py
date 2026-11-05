"""Money. Every amount is a whole number of CENTAVOS (₱1.00 = 100).

Floats can't hold 0.10 exactly, so adding prices as floats slowly goes wrong
(0.1 + 0.2 = 0.30000000000000004). Whole centavos always add up.
"""
import re

MAX_PRICE = 10_000_000_00  # ₱10 million: more than any one price in a store
PESO_PATTERN = re.compile(r"\d+(\.\d{1,2})?")


def parse_peso(text):
    """What a person types ("20", "20.5", "1,250.75", "₱ 20") -> centavos, or None if it isn't a price."""
    raw = (text or "").replace(",", "").replace("₱", "").replace("PHP", "").replace(" ", "").strip()
    if not PESO_PATTERN.fullmatch(raw):
        return None
    whole, _, cents = raw.partition(".")
    return int(whole) * 100 + int((cents + "00")[:2])


def peso(centavos):
    """2050 -> "₱20.50". Negative amounts (a discount, change owed) get a minus in front."""
    sign = "-" if centavos < 0 else ""
    whole, cents = divmod(abs(centavos), 100)
    return f"{sign}₱{whole:,}.{cents:02d}"


def plain_peso(centavos):
    """2050 -> "20.50", for a form field (no sign, no commas)."""
    whole, cents = divmod(centavos, 100)
    return f"{whole}.{cents:02d}"
