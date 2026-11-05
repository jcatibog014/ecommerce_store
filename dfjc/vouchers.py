"""Voucher codes: pesos off, percent off, or free delivery.

A voucher is used when the order is placed, inside checkout's one transaction:

    UPDATE vouchers SET used_count = used_count + 1
    WHERE id = ? AND is_active AND (max_uses IS NULL OR used_count < max_uses)

so a voucher with 50 uses is used at most 50 times, even if 51 shoppers press
"Place order" together (and a CHECK in the database refuses more anyway).
voucher_uses has UNIQUE(voucher, shopper): each account uses a code once.

The order keeps a copy of the voucher (code, kind, value). The discount is
worked out from that copy at checkout and again after the cashier's price
(wholesale lowers the items, so a percent discount gets smaller). Cancelling
an order gives the use back.
"""
import re

from sqlalchemy import delete, select, update

from .models import VOUCHER_AMOUNT, VOUCHER_DELIVERY, VOUCHER_PERCENT, Voucher, VoucherUse

CODE_PATTERN = re.compile(r"[A-Z0-9-]{3,30}")


class VoucherProblem(ValueError):
    pass


def clean_code(text):
    return re.sub(r"\s+", "", (text or "")).upper()


def find(s, store, code):
    return s.scalar(select(Voucher).where(Voucher.store_id == store.id, Voucher.code == clean_code(code)))


def problem(voucher, subtotal, today, user=None, s=None):
    """Why this voucher can't be used on these items, or None."""
    if voucher is None or not voucher.is_active:
        return "That code doesn't work. Check the spelling."
    if voucher.starts_on and today < voucher.starts_on:
        return f"That code starts on {voucher.starts_on:%b} {voucher.starts_on.day}."
    if voucher.ends_on and today > voucher.ends_on:
        return "That code has ended."
    if voucher.uses_left == 0:
        return "That code has been used up."
    if subtotal < voucher.min_spend:
        return f"That code is for orders of {voucher.min_spend / 100:,.2f} pesos or more."
    if user is not None and s is not None and s.scalar(select(VoucherUse.id).where(
            VoucherUse.voucher_id == voucher.id, VoucherUse.user_id == user.id)) is not None:
        return "You already used that code."
    return None


def discount(kind, value, subtotal, delivery_fee):
    """Pesos off for these items: never more than they cost."""
    if kind == VOUCHER_AMOUNT:
        return min(value, subtotal)
    if kind == VOUCHER_PERCENT:
        return min(subtotal, round(subtotal * value / 100))
    if kind == VOUCHER_DELIVERY:
        return delivery_fee
    return 0


def order_discount(order, subtotal):
    if not order.voucher_code:
        return 0
    return discount(order.voucher_kind, order.voucher_value, subtotal, order.delivery_fee)


def describe(voucher):
    if voucher.kind == VOUCHER_AMOUNT:
        words = f"{voucher.value / 100:,.0f} pesos off"
    elif voucher.kind == VOUCHER_PERCENT:
        words = f"{voucher.value}% off"
    else:
        words = "Free delivery"
    if voucher.min_spend:
        words += f" on orders of {voucher.min_spend / 100:,.0f} pesos or more"
    return words


def use(s, voucher, user, order, moment):
    """Inside checkout's transaction: count the use (if one is left) and remember who used it."""
    claimed = s.execute(update(Voucher)
                        .where(Voucher.id == voucher.id, Voucher.is_active.is_(True),
                               (Voucher.max_uses.is_(None)) | (Voucher.used_count < Voucher.max_uses))
                        .values(used_count=Voucher.used_count + 1)
                        .execution_options(synchronize_session=False)).rowcount
    if claimed != 1:
        raise VoucherProblem("Sorry, that code was just used up.")
    s.add(VoucherUse(voucher_id=voucher.id, user_id=user.id, order_id=order.id, created_at=moment))
    order.voucher_id = voucher.id
    order.voucher_code, order.voucher_kind, order.voucher_value = voucher.code, voucher.kind, voucher.value


def give_back(s, order):
    """A cancelled order doesn't use up the code."""
    if not order.voucher_id:
        return
    removed = s.execute(delete(VoucherUse).where(VoucherUse.order_id == order.id)
                        .execution_options(synchronize_session=False)).rowcount
    if removed:
        s.execute(update(Voucher).where(Voucher.id == order.voucher_id, Voucher.used_count > 0)
                  .values(used_count=Voucher.used_count - 1).execution_options(synchronize_session=False))


def home_promos(s, store, today):
    found = s.scalars(select(Voucher).where(Voucher.store_id == store.id, Voucher.is_active.is_(True),
                                            Voucher.show_on_home.is_(True))
                      .order_by(Voucher.created_at.desc())).all()
    return [voucher for voucher in found
            if (not voucher.starts_on or voucher.starts_on <= today) and (not voucher.ends_on or voucher.ends_on >= today)
            and voucher.uses_left != 0]
