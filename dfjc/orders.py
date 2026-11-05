"""Order state: the only place an order's status changes.

    placed ──cashier prices──> awaiting_payment (online, stage 3) ──paid──> paid ──┐
       │                  └──> confirmed (cash) ───────────────────────────────────┴─> packing ─> ready ─┬─> picked_up
       │                                                                                                 └─> out_for_delivery ─> delivered
       └──> cancelled  (before packing; a paid order is refunded instead, stage 3)

TRANSITIONS lists every allowed move. move() checks it, changes the row with
a conditional UPDATE (... WHERE status = <what we read> AND version = <what we
read>), and writes the timeline. If someone else changed the order first, the
UPDATE finds no row and OrderChanged is raised, so nothing is overwritten.

Stock: placing an order HOLDS its pieces (products.reserved_pieces, see
checkout.py). Cancelling gives them back. Packing takes them off the shelf
for real (stock.take_for_order), in the same transaction as the move.
"""
from collections import defaultdict

from sqlalchemy import update
from sqlalchemy.orm.attributes import set_committed_value

from .models import (
    AWAITING_PAYMENT, CANCELLED, CASH_PICKUP, COD, CONFIRMED, DELIVERED, DELIVERY, MIXED, OUT_FOR_DELIVERY, PACKING,
    PAID, PICKED_UP, PICKUP, PLACED, READY, REFUNDED, RETAIL, WHOLESALE, Order, OrderEvent, Product,
)

TRANSITIONS = {
    PLACED: (AWAITING_PAYMENT, CONFIRMED, CANCELLED),
    AWAITING_PAYMENT: (PAID, CONFIRMED, CANCELLED),
    CONFIRMED: (PACKING, AWAITING_PAYMENT, CANCELLED),
    PAID: (PACKING, REFUNDED),
    PACKING: (READY,),
    READY: (PICKED_UP, OUT_FOR_DELIVERY),
    OUT_FOR_DELIVERY: (DELIVERED, READY),  # back to ready when nobody was home
    PICKED_UP: (),
    DELIVERED: (),
    CANCELLED: (),
    REFUNDED: (),
}
# Only one way out of "ready", depending on how the shopper gets the order.
ONLY_FOR = {PICKED_UP: PICKUP, OUT_FOR_DELIVERY: DELIVERY, DELIVERED: DELIVERY}
# While an order is in one of these, its pieces are held (reserved) but still on the shelf.
HOLDS_STOCK = (PLACED, AWAITING_PAYMENT, CONFIRMED, PAID)
CASH_METHODS = (CASH_PICKUP, COD)

STATUS_LABELS = {
    PLACED: "Waiting for the cashier",
    AWAITING_PAYMENT: "Waiting for payment",
    CONFIRMED: "Confirmed",
    PAID: "Paid",
    PACKING: "Packing",
    READY: "Ready",
    OUT_FOR_DELIVERY: "Out for delivery",
    PICKED_UP: "Picked up",
    DELIVERED: "Delivered",
    CANCELLED: "Cancelled",
    REFUNDED: "Refunded",
}
# What the timeline says happened when an order reached a status.
EVENT_WORDS = {
    PLACED: "Order placed",
    AWAITING_PAYMENT: "Priced by the cashier. Waiting for payment",
    CONFIRMED: "Priced by the cashier",
    PAID: "Paid",
    PACKING: "Packing started",
    READY: "Ready",
    OUT_FOR_DELIVERY: "On the way with the rider",
    PICKED_UP: "Picked up",
    DELIVERED: "Delivered",
    CANCELLED: "Cancelled",
    REFUNDED: "Refunded",
}
CANCEL_REASON_MAX = 300


class MoveNotAllowed(ValueError):
    pass


class OrderChanged(Exception):
    """Someone else changed this order first (another cashier, or the shopper)."""


def status_label(order, status=None):
    status = status or order.status
    if status == READY:
        return "Ready for pickup" if order.fulfilment == PICKUP else "Ready for delivery"
    return STATUS_LABELS[status]


def can_move(order, to_status):
    if to_status not in TRANSITIONS[order.status]:
        return False
    return ONLY_FOR.get(to_status, order.fulfilment) == order.fulfilment


def move(s, order, to_status, by, moment, note="", **changes):
    """Change the order's status (and any other columns in changes) and write the timeline.
    The caller commits. Raises MoveNotAllowed or OrderChanged."""
    if not can_move(order, to_status):
        raise MoveNotAllowed(f"An order that is {status_label(order).lower()} can't become "
                             f"{status_label(order, to_status).lower()}.")
    from_status = order.status
    result = s.execute(
        update(Order)
        .where(Order.id == order.id, Order.status == from_status, Order.version == order.version)
        .values(status=to_status, version=Order.version + 1, updated_at=moment, **changes)
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise OrderChanged("This order was changed by someone else a moment ago. Reload the page to see it.")
    # Tell the loaded order what the database now says, without making SQLAlchemy update it again.
    for name, value in {"status": to_status, "version": order.version + 1, "updated_at": moment, **changes}.items():
        set_committed_value(order, name, value)
    if to_status in (CANCELLED, REFUNDED) and from_status in HOLDS_STOCK:
        release_stock(s, order)
    if to_status == PACKING and from_status in HOLDS_STOCK:
        from . import stock  # (stock.py uses this module too)
        stock.take_for_order(s, order, by, moment)
    s.add(OrderEvent(order_id=order.id, from_status=from_status, to_status=to_status,
                     by_id=by.id if by else None, note=note, created_at=moment))
    return order


def pieces_by_product(order):
    pieces = defaultdict(int)
    for line in order.lines:
        if line.product_id:
            pieces[line.product_id] += line.pieces
    return pieces


def release_stock(s, order):
    """Give back the pieces this order was holding."""
    for product_id, pieces in sorted(pieces_by_product(order).items()):
        s.execute(update(Product)
                  .where(Product.id == product_id, Product.reserved_pieces >= pieces)
                  .values(reserved_pieces=Product.reserved_pieces - pieces)
                  .execution_options(synchronize_session=False))


# ---------- What people may do ----------

def shopper_can_cancel(order):
    """Before packing and before any money is paid."""
    return order.status in (PLACED, AWAITING_PAYMENT, CONFIRMED)


def staff_can_cancel(order):
    return order.status in (PLACED, AWAITING_PAYMENT, CONFIRMED)


def cancel(s, order, by, moment, reason=""):
    return move(s, order, CANCELLED, by, moment, note=reason[:CANCEL_REASON_MAX])


# ---------- The cashier's price ----------

def totals(order):
    subtotal = sum(line.line_total for line in order.lines)
    return subtotal, max(0, subtotal + order.delivery_fee - order.discount)


def price_type_of(lines):
    kinds = {line.price_type for line in lines}
    return kinds.pop() if len(kinds) == 1 else MIXED


def price_order(s, order, choices, cashier, moment, version, note=""):
    """The cashier gives each line its price: choices is {line id: "retail" or "wholesale"}.
    Lines not in choices keep retail. Then the order is confirmed (cash) or waits for payment
    (online), all in the caller's one transaction."""
    if order.status != PLACED:
        raise MoveNotAllowed("This order was already priced.")
    if str(order.version) != str(version):
        raise OrderChanged("This order was changed by someone else a moment ago. Reload the page to see it.")
    for line in order.lines:
        line.price_type = WHOLESALE if choices.get(line.id) == WHOLESALE else RETAIL
    s.flush()
    subtotal, total = totals(order)
    to_status = CONFIRMED if order.payment_method in CASH_METHODS else AWAITING_PAYMENT
    words = {RETAIL: "Retail prices", WHOLESALE: "Wholesale prices", MIXED: "Retail and wholesale prices"}
    kind = price_type_of(order.lines)
    return move(s, order, to_status, cashier, moment, note=words[kind] + (f". {note}" if note else ""),
                subtotal=subtotal, total=total, price_type=kind, priced_at=moment, priced_by_id=cashier.id,
                cashier_note=note)


# ---------- The steps shown on the order page ----------

def steps(order):
    """[(label, state)] where state is "done" (reached), "now" (the latest step reached) or "next".
    A cancelled order shows only "Placed" as reached."""
    # Online orders have a Paid step; cash is paid at the end (at pickup or on delivery).
    online = getattr(order, "payment_method", "") == "online"
    road = [PLACED, CONFIRMED] + ([PAID] if online else []) + [PACKING, READY]
    road += [PICKED_UP] if order.fulfilment == PICKUP else [OUT_FOR_DELIVERY, DELIVERED]
    labels = {PLACED: "Placed", CONFIRMED: "Priced", PACKING: "Packing",
              READY: "Ready for pickup" if order.fulfilment == PICKUP else "Ready to go",
              PICKED_UP: "Picked up", OUT_FOR_DELIVERY: "On the way", DELIVERED: "Delivered", PAID: "Paid"}
    where = {AWAITING_PAYMENT: CONFIRMED}.get(order.status, order.status)
    if where == PAID and not online:
        where = CONFIRMED
    if where in (CANCELLED, REFUNDED):
        return [(labels[step], "done" if index == 0 else "next") for index, step in enumerate(road)]
    current = road.index(where)
    return [(labels[step], "done" if index < current else "now" if index == current else "next")
            for index, step in enumerate(road)]
