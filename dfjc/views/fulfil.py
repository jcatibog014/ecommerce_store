"""Packing, pickup, deliveries and the rider's cash. The rules are in fulfil.py and orders.py."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import db, forms, fulfil, orders
from ..dates import day_start_utc, local_today, utcnow
from ..models import (
    CONFIRMED, DELIVERED, OUT_FOR_DELIVERY, PACKING, PAID, PICKED_UP, READY, RIDER, Order, User,
)
from ..permissions import PACK_ORDERS, RECEIVE_CASH, SEE_DELIVERIES, can, needs
from ..stock import StockProblem
from ..stores import get_in_store

bp = Blueprint("fulfil", __name__)

BOARD = [
    ("to-pack", "To Pack", (CONFIRMED, PAID)),
    ("packing", "Packing", (PACKING,)),
    ("ready", "Ready", (READY,)),
    ("out", "Out for Delivery", (OUT_FOR_DELIVERY,)),
]
STEP_WORDS = {"pack": "Packing started", "packed": "Packed and ready", "picked_up": "Picked up",
              "send_out": "Sent out"}


def store_order(ref):
    order = db.session().scalar(select(Order).where(Order.ref == ref, Order.store_id == g.store.id)
                                .options(selectinload(Order.lines), selectinload(Order.payments)))
    if order is None:
        abort(404)
    return order


def riders(s):
    return s.scalars(select(User).where(User.store_id == g.store.id, User.role == RIDER, User.is_active.is_(True))
                     .order_by(User.name)).all()


def acting_rider(s):
    """Who "my" deliveries belong to. In the demo the visitor plays the rider through View As,
    so they act as the demo store's rider (Jun); everywhere else it is the person logged in."""
    if g.user.is_demo and g.role == RIDER:
        rider = s.scalar(select(User).where(User.store_id == g.store.id, User.role == RIDER).order_by(User.id))
        if rider is not None:
            return rider
    return g.user


def back_to(default):
    target = request.form.get("next", "")
    return redirect(target if target.startswith("/") and not target.startswith("//") else default)


@bp.get("/manage/packing")
@needs(PACK_ORDERS)
def board():
    s = db.session()
    statuses = [status for _, _, group in BOARD for status in group]
    found = s.scalars(select(Order).where(Order.store_id == g.store.id, Order.status.in_(statuses))
                      .order_by(Order.updated_at).options(selectinload(Order.lines), selectinload(Order.rider))).all()
    columns = [(key, label, [order for order in found if order.status in group]) for key, label, group in BOARD]
    today = day_start_utc(local_today())
    done_today = s.scalar(select(func.count()).select_from(Order).where(
        Order.store_id == g.store.id, Order.status.in_((PICKED_UP, DELIVERED)), Order.updated_at >= today))
    return render_template("manage/packing.html", columns=columns, riders=riders(s), done_today=done_today)


@bp.post("/manage/orders/<ref>/step")
@needs(PACK_ORDERS, "Only a cashier or the owner can move orders along.")
def step(ref):
    s = db.session()
    order = store_order(ref)
    action = request.form.get("action")
    if request.form.get("version") != str(order.version):
        flash("This order was changed a moment ago. Here it is now.", "error")
        return back_to(url_for("fulfil.board"))
    moment = utcnow()
    try:
        if action == "pack":
            fulfil.start_packing(s, order, g.user, moment)
        elif action == "packed":
            fulfil.packed(s, order, g.user, moment)
        elif action == "picked_up":
            fulfil.hand_over(s, order, g.user, moment)
        elif action == "send_out":
            rider = s.get(User, request.form.get("rider_id", type=int) or 0)
            fulfil.send_out(s, order, rider, g.user, moment)
        else:
            abort(400, "Choose a step.")
        s.commit()
    except (orders.MoveNotAllowed, orders.OrderChanged, fulfil.FulfilProblem, StockProblem) as problem:
        s.rollback()
        flash(str(problem), "error")
        return back_to(url_for("fulfil.board"))
    message = f"{order.ref}: {STEP_WORDS[action].lower()}."
    if action == "picked_up" and order.payment_method in orders.CASH_METHODS:
        message += f" {order.total / 100:,.2f} pesos in cash received."
    flash(message)
    return back_to(url_for("fulfil.board"))


# ---------- The rider ----------

@bp.get("/deliveries")
@needs(SEE_DELIVERIES)
def deliveries():
    s = db.session()
    query = select(Order).where(Order.store_id == g.store.id, Order.status == OUT_FOR_DELIVERY)
    mine = g.role == RIDER
    me = acting_rider(s)
    if mine:
        query = query.where(Order.rider_id == me.id)
    found = s.scalars(query.order_by(Order.updated_at).options(selectinload(Order.lines),
                                                               selectinload(Order.rider))).all()
    done = select(Order).where(Order.store_id == g.store.id, Order.status == DELIVERED,
                               Order.updated_at >= day_start_utc(local_today()))
    if mine:
        done = done.where(Order.rider_id == me.id)
    delivered_today = s.scalars(done.order_by(Order.updated_at.desc())).all()
    held = fulfil.cash_with(s, me) if mine else []
    return render_template("deliveries.html", orders_found=found, delivered_today=delivered_today, mine=mine,
                           cash_held=sum(payment.amount for payment in held), cash_count=len(held))


@bp.post("/deliveries/<ref>/<outcome>")
@needs(SEE_DELIVERIES)
def deliver(ref, outcome):
    s = db.session()
    order = store_order(ref)
    if request.form.get("version") != str(order.version):
        flash("This delivery was changed a moment ago. Here it is now.", "error")
        return redirect(url_for("fulfil.deliveries"))
    try:
        me = acting_rider(s)
        if outcome == "delivered":
            fulfil.delivered(s, order, me, g.role, utcnow())
        elif outcome == "not-home":
            note, _ = forms.text(request.form, "note", 300)
            fulfil.not_home(s, order, me, g.role, utcnow(), note)
        else:
            abort(404)
        s.commit()
    except (orders.MoveNotAllowed, orders.OrderChanged, fulfil.FulfilProblem) as problem:
        s.rollback()
        flash(str(problem), "error")
        return redirect(url_for("fulfil.deliveries"))
    if outcome == "delivered":
        cash = f" You have {order.total / 100:,.2f} pesos of cash for it." if order.payment_method == "cod" else ""
        flash(f"{order.ref} delivered to {order.customer_name}.{cash}")
    else:
        flash(f"{order.ref} is back at the store as Ready. The cashier can send it again.")
    return redirect(url_for("fulfil.deliveries"))


# ---------- The rider's cash at the counter ----------

@bp.get("/manage/rider-cash")
@needs(RECEIVE_CASH)
def rider_cash():
    s = db.session()
    return render_template("manage/rider_cash.html", holding=fulfil.riders_with_cash(s, g.store),
                           history=fulfil.remittances(s, g.store))


@bp.post("/manage/rider-cash/<int:rider_id>")
@needs(RECEIVE_CASH, "Only a cashier or the owner can receive cash.")
def receive(rider_id):
    s = db.session()
    rider = get_in_store(s, User, rider_id, g.store)
    ids = [int(value) for value in request.form.getlist("payment_id") if value.isdigit()]
    try:
        remittance = fulfil.remit(s, g.store, rider, ids, g.user, utcnow())
        s.commit()
    except fulfil.FulfilProblem as problem:
        s.rollback()
        flash(str(problem), "error")
        return redirect(url_for("fulfil.rider_cash"))
    flash(f"Received {remittance.amount / 100:,.2f} pesos from {rider.name}. Count it before putting it away.")
    return redirect(url_for("fulfil.rider_cash"))


def init_app(app):
    @app.context_processor
    def fulfil_badges():
        user = g.get("user")
        if user is None:
            return {"to_pack_count": 0, "out_count": 0}
        s = db.session()
        to_pack = out = 0
        if can(PACK_ORDERS):
            to_pack = s.scalar(select(func.count()).select_from(Order).where(
                Order.store_id == g.store.id, Order.status.in_((CONFIRMED, PAID))))
        if can(SEE_DELIVERIES):
            query = select(func.count()).select_from(Order).where(Order.store_id == g.store.id,
                                                                  Order.status == OUT_FOR_DELIVERY)
            if g.role == RIDER:
                query = query.where(Order.rider_id == acting_rider(s).id)
            out = s.scalar(query)
        return {"to_pack_count": to_pack, "out_count": out}
