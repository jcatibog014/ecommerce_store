"""The shopper changes an order they already placed: add items, change amounts, remove items.

Allowed until packing starts, and never once it is paid online (that would need a part
refund): placed, waiting for payment, or confirmed (cash). Each change is ONE transaction:

1. The line changes, and the stock it holds follows. More pieces are held with the same
   guarded UPDATE as checkout (stock - reserved >= n, so it never oversells); fewer pieces
   are given back.
2. The totals are worked out again at retail (the cashier hasn't priced the change yet).
3. The order is fenced against the cashier: a still-unpriced order gets its version bumped
   with a conditional UPDATE (a cashier pricing the old version is refused), and a priced
   order moves back to "placed" through orders.move(), so the cashier prices it again.
   An open online payment page is closed, so the old total can't be paid. If it is paid at
   that very moment anyway, payments.mark_paid sees the order isn't waiting and flags a refund.

Every change writes the timeline (what changed), so the cashier sees it.
"""
from sqlalchemy import select, update
from sqlalchemy.orm.attributes import set_committed_value

from . import checkout, orders, payments, vouchers
from .models import (
    AWAITING_PAYMENT, CONFIRMED, PAYMENT_PAID, PLACED, REQUEST_PENDING, RETAIL, Order, OrderEvent, OrderLine, Product,
    Store, Voucher,
)

EDITABLE = (PLACED, AWAITING_PAYMENT, CONFIRMED)


class EditProblem(ValueError):
    pass


def can_edit(order):
    """Before packing, and not once any money came in online."""
    return order.status in EDITABLE and not any(payment.status == PAYMENT_PAID for payment in order.payments)


# ---------- The changes ----------

def add_unit(s, order, unit, quantity, by, moment, gateway=None):
    """Add quantity of a size from the shop (on top of the same size already in the order)."""
    check(order)
    if unit.product.store_id != order.store_id or not unit.is_active or not unit.product.is_active:
        raise EditProblem("That isn't sold any more.")
    line = next((item for item in order.lines if item.unit_id == unit.id and not item.is_request), None)
    room = checkout.MAX_QUANTITY - (line.quantity if line else 0)
    quantity = min(quantity, room)
    if quantity < 1:
        raise EditProblem(f"Your order already has {checkout.MAX_QUANTITY} of those, the most for one item.")
    hold(s, order, unit.product, quantity * unit.pieces_per_unit)
    if line is None:
        order.lines.append(OrderLine(
            product_id=unit.product_id, unit_id=unit.id, product_name=unit.product.name, unit_name=unit.name,
            pieces_per_unit=unit.pieces_per_unit, quantity=quantity, pieces=quantity * unit.pieces_per_unit,
            retail_price=unit.retail_price, wholesale_price=unit.wholesale_price, price_type=RETAIL))
    else:
        line.quantity += quantity
        line.pieces = line.quantity * line.pieces_per_unit
    return finish(s, order, by, moment, gateway, f"Added {quantity} x {unit.product.name} ({unit.name})")


def add_request(s, order, product, wanted, unit_name, quantity, by, moment, gateway=None):
    """"Not in the list? Ask for it", into the order: no price and no stock until the cashier answers."""
    check(order)
    name = f"{product.group_title} - {wanted}"  # as an asked-for cart item is named
    order.lines.append(OrderLine(
        product_id=product.id, unit_id=None, product_name=name[:170], unit_name=unit_name, pieces_per_unit=1,
        quantity=quantity, pieces=0, retail_price=0, wholesale_price=0, price_type=RETAIL, is_request=True,
        request_status=REQUEST_PENDING))
    return finish(s, order, by, moment, gateway, f"Asked for {quantity} x \"{wanted}\" ({unit_name})")


def set_quantity(s, order, line, quantity, by, moment, gateway=None):
    """A new amount for one line; 0 removes it. The order keeps at least one item."""
    check(order)
    if line.order_id != order.id:
        raise EditProblem("That item isn't in this order.")
    quantity = max(0, min(quantity, checkout.MAX_QUANTITY))
    if quantity == line.quantity:
        raise EditProblem("That's the amount already in your order.")
    what = f"{line.product_name} ({line.unit_name})"
    if quantity == 0:
        if len(order.lines) == 1:
            raise EditProblem("This is the only item in the order. To stop the order, cancel it instead.")
        if not line.is_request and line.product_id:
            release(s, line.product_id, line.pieces)
        order.lines.remove(line)
        s.delete(line)
        note = f"Removed {what}"
    else:
        if not line.is_request:
            more = (quantity - line.quantity) * line.pieces_per_unit
            if more > 0:
                product = s.get(Product, line.product_id) if line.product_id else None
                unit = line.unit_id and product and next((u for u in product.units if u.id == line.unit_id), None)
                if product is None or not unit or not unit.is_active or not product.is_active:
                    raise EditProblem(f"{what} isn't sold any more, so you can only lower it or remove it.")
                hold(s, order, product, more)
            elif line.product_id:
                release(s, line.product_id, -more)
            line.pieces = quantity * line.pieces_per_unit
        note = f"{what}: {line.quantity} to {quantity}"
        line.quantity = quantity
    return finish(s, order, by, moment, gateway, note)


# ---------- The parts every change shares ----------

def check(order):
    if not can_edit(order):
        raise EditProblem("This order can't be changed any more: the store has started on it, or it is paid. "
                          "Please call the store.")


def hold(s, order, product, pieces):
    """Hold more pieces for this order, or say how many are left. The caller rolls back."""
    try:
        checkout.hold_stock(s, s.get(Store, order.store_id), {product.id: pieces}, {product.id: product.name})
    except checkout.OutOfStock as short:
        raise EditProblem(f"Sorry, only {short.available_pieces:,} "
                          f"{'piece' if short.available_pieces == 1 else 'pieces'} of {short.name} "
                          "can still be added.") from None


def release(s, product_id, pieces):
    s.execute(update(Product).where(Product.id == product_id, Product.reserved_pieces >= pieces)
              .values(reserved_pieces=Product.reserved_pieces - pieces).execution_options(synchronize_session=False))


def finish(s, order, by, moment, gateway, note):
    """Back to retail and unpriced, new totals, the voucher's least spend, then fence and log."""
    was = order.status
    if was != PLACED:
        # Priced before: every line goes back to retail, asked-for items back to "the cashier checks".
        for line in order.lines:
            line.price_type = RETAIL
            if line.is_request:
                line.request_status, line.retail_price, line.wholesale_price = REQUEST_PENDING, 0, 0
    subtotal = sum(line.line_total for line in order.lines)
    if order.voucher_id:
        voucher = s.get(Voucher, order.voucher_id)
        if voucher is not None and voucher.min_spend and subtotal < voucher.min_spend:
            raise EditProblem(f"Your voucher {order.voucher_code} is for orders of {voucher.min_spend / 100:,.2f} "
                              f"pesos or more. With this change the items come to {subtotal / 100:,.2f} pesos.")
    discount = vouchers.order_discount(order, subtotal)
    totals = {"subtotal": subtotal, "discount": discount,
              "total": max(0, subtotal + order.delivery_fee - discount), "price_type": RETAIL}
    if was == PLACED:
        bumped = s.execute(update(Order).where(Order.id == order.id, Order.status == PLACED,
                                               Order.version == order.version)
                           .values(version=Order.version + 1, updated_at=moment, **totals)
                           .execution_options(synchronize_session=False)).rowcount
        if bumped != 1:
            raise orders.OrderChanged("The cashier changed this order a moment ago. Here it is now.")
        for name, value in {"version": order.version + 1, "updated_at": moment, **totals}.items():
            set_committed_value(order, name, value)
        s.add(OrderEvent(order_id=order.id, from_status=PLACED, to_status=PLACED, by_id=by.id, note=note[:300],
                         created_at=moment))
    else:
        if was == AWAITING_PAYMENT:
            payments.close_open(s, order, gateway)  # the old total can't be paid any more
        orders.move(s, order, PLACED, by, moment, note=f"{note}. The cashier prices it again."[:300],
                    priced_at=None, priced_by_id=None, cashier_note="", **totals)
    return order


def editing(session, user, s):
    """The order this shopper is editing (session["editing_order"]), if it can still be edited."""
    ref = session.get("editing_order")
    if not ref or user is None:
        return None
    order = s.scalar(select(Order).where(Order.ref == ref, Order.user_id == user.id))
    if order is None or not can_edit(order):
        session.pop("editing_order", None)
        return None
    return order
