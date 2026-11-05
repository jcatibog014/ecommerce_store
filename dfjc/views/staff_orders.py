"""The cashier's orders: the queue, pricing an order (retail or wholesale), and cancelling.

Pricing is orders.price_order, in one transaction: every line's price, the
totals and the new status are saved together, or not at all.
"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import db, forms, gateways, orders, payments
from ..dates import utcnow
from ..models import (
    AWAITING_PAYMENT, CANCELLED, CONFIRMED, DELIVERED, PAID, PICKED_UP, PLACED, REFUNDED, RIDER, WHOLESALE, Order, User,
)
from ..permissions import PRICE_ORDERS, SEE_ORDERS, can, needs

bp = Blueprint("staff_orders", __name__, url_prefix="/manage/orders")

# The queue's tabs: (statuses, oldest first?)
TABS = {
    "to-price": ("To Price", (PLACED,), True),
    "waiting": ("Waiting for Payment", (AWAITING_PAYMENT,), True),
    "confirmed": ("Confirmed", (CONFIRMED, PAID), True),
    "done": ("Done", (PICKED_UP, DELIVERED), False),
    "cancelled": ("Cancelled", (CANCELLED, REFUNDED), False),
    "all": ("All Orders", None, False),
}


def store_order(ref):
    order = db.session().scalar(select(Order).where(Order.ref == ref, Order.store_id == g.store.id)
                                .options(selectinload(Order.lines), selectinload(Order.events),
                                         selectinload(Order.payments)))
    if order is None:
        abort(404)
    return order


def to_price_count(s, store):
    return s.scalar(select(func.count()).select_from(Order).where(Order.store_id == store.id, Order.status == PLACED))


@bp.get("")
@needs(SEE_ORDERS)
def index():
    s = db.session()
    tab = request.args.get("tab") if request.args.get("tab") in TABS else "to-price"
    _, statuses, oldest_first = TABS[tab]
    query = select(Order).where(Order.store_id == g.store.id)
    if statuses:
        query = query.where(Order.status.in_(statuses))
    query = query.order_by(Order.placed_at if oldest_first else Order.placed_at.desc()).limit(200)
    found = s.scalars(query.options(selectinload(Order.lines))).all()
    counts = dict(s.execute(select(Order.status, func.count()).where(Order.store_id == g.store.id)
                            .group_by(Order.status)).all())
    tab_counts = {key: sum(counts.get(status, 0) for status in (value[1] or ())) for key, value in TABS.items()}
    return render_template("manage/orders.html", orders_found=found, tab=tab, tabs=TABS, tab_counts=tab_counts,
                           moment=utcnow())


@bp.get("/<ref>")
@needs(SEE_ORDERS)
def detail(ref):
    riders = db.session().scalars(select(User).where(User.store_id == g.store.id, User.role == RIDER,
                                                     User.is_active.is_(True)).order_by(User.name)).all()
    return render_template("manage/order.html", order=store_order(ref), riders=riders)


@bp.post("/<ref>/price")
@needs(PRICE_ORDERS, "Only a cashier or the owner can price orders.")
def price(ref):
    s = db.session()
    order = store_order(ref)
    note, problem = forms.text(request.form, "cashier_note", 300)
    if problem:
        flash(problem, "error")
        return redirect(url_for("staff_orders.detail", ref=ref))
    choices = {line.id: request.form.get(f"line_{line.id}") for line in order.lines}
    # Asked-for lines: "available" with a typed price, or "unavailable".
    asked = {}
    for line in order.lines:
        if not line.is_request:
            continue
        answer = request.form.get(f"request_{line.id}")
        if answer == "unavailable":
            asked[line.id] = None
        elif answer == "available":
            price, trouble = forms.price(request.form, f"price_{line.id}")
            if trouble or not price:
                flash(f"Type the price of \"{line.product_name}\" (each {line.unit_name.lower()}), "
                      "or choose Not available.", "error")
                return redirect(url_for("staff_orders.detail", ref=ref))
            asked[line.id] = price
    try:
        orders.price_order(s, order, choices, g.user, utcnow(), request.form.get("version"), note, requests=asked)
        s.commit()
    except (orders.OrderChanged, orders.MoveNotAllowed) as problem:
        s.rollback()
        flash(str(problem), "error")
        return redirect(url_for("staff_orders.detail", ref=ref))
    wholesale = sum(1 for line in order.lines if line.price_type == WHOLESALE)
    flash(f"Order {order.ref} is priced: {wholesale} of {len(order.lines)} lines at wholesale. "
          f"The shopper now sees the final total.")
    return redirect(url_for("staff_orders.index"))


@bp.post("/<ref>/cancel")
@needs(PRICE_ORDERS, "Only a cashier or the owner can cancel orders.")
def cancel(ref):
    s = db.session()
    order = store_order(ref)
    reason, _ = forms.text(request.form, "reason", orders.CANCEL_REASON_MAX)
    if not reason:
        flash("Say why the order is cancelled. The shopper sees it.", "error")
        return redirect(url_for("staff_orders.detail", ref=ref))
    if not orders.staff_can_cancel(order):
        flash("This order can't be cancelled any more.", "error")
        return redirect(url_for("staff_orders.detail", ref=ref))
    try:
        payments.close_open(s, order, gateways.for_store(g.store))
        orders.cancel(s, order, g.user, utcnow(), reason)
        s.commit()
    except (orders.OrderChanged, orders.MoveNotAllowed) as problem:
        s.rollback()
        flash(str(problem), "error")
        return redirect(url_for("staff_orders.detail", ref=ref))
    flash(f"Order {order.ref} is cancelled, and its items are back on sale.")
    return redirect(url_for("staff_orders.detail", ref=ref))


def init_app(app):
    @app.context_processor
    def orders_badge():
        if g.get("user") is not None and can(SEE_ORDERS):
            return {"to_price_count": to_price_count(db.session(), g.store)}
        return {"to_price_count": 0}

    app.jinja_env.globals.update(refundable=payments.refundable, PAID_WITH_LABELS=payments.PAID_WITH_LABELS)
    app.jinja_env.globals.update(status_label=orders.status_label, steps_of=orders.steps,
                                 shopper_can_cancel=orders.shopper_can_cancel,
                                 staff_can_cancel=orders.staff_can_cancel, EVENT_WORDS=orders.EVENT_WORDS)
