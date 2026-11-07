"""Paying online: Pay, the return page, Check payment, switching to cash, PayMongo's webhook,
the Sandbox's payment page, and the staff's Refund button. The rules are in payments.py."""
import json
import secrets

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .. import db, forms, gateways, orders, payments
from ..dates import utcnow
from ..gateways import GatewayError
from ..models import AWAITING_PAYMENT, CONFIRMED, Order, Payment, Store
from ..permissions import PRICE_ORDERS, SHOP, needs
from .cart import STAFF_CANT_SHOP

bp = Blueprint("payments", __name__)

PAID = "Thank you! Your payment is in, and order {ref} is paid."
ALREADY = "Thank you! Your payment for order {ref} is in."
LATE = ("Your payment came in after the order changed, so the store will give it back. "
        "You don't need to do anything.")


def my_order(ref):
    order = db.session().scalar(select(Order).where(Order.ref == ref, Order.user_id == g.user.id)
                                .options(selectinload(Order.lines), selectinload(Order.payments)))
    if order is None:
        abort(404)
    return order


def my_payment(order, payment_id):
    payment = next((p for p in order.payments if p.id == payment_id), None)
    if payment is None:
        abort(404)
    return payment


def back(order):
    return redirect(url_for("my_orders.detail", ref=order.ref))


def say(result, order):
    messages = {"paid": (PAID, None), "already": (ALREADY, None), "needs_refund": (LATE, "error"),
                "expired": ("That payment page expired. Press Pay to start again.", "error"),
                "unpaid": ("We haven't received the payment yet. If you paid, it can take a minute: "
                           "press Check payment again.", None)}
    message, category = messages.get(result, ("Something unexpected happened. Please call the store.", "error"))
    flash(message.format(ref=order.ref), category or "message")


@bp.post("/orders/<ref>/pay")
@needs(SHOP, STAFF_CANT_SHOP)
def pay(ref):
    s = db.session()
    order = my_order(ref)
    gateway = gateways.for_store(g.store)
    if gateway is None:
        flash("Online payment is off for now. Please pay in cash.", "error")
        return back(order)

    def urls(payment):
        return (url_for("payments.returned", ref=ref, payment_id=payment.id, _external=True),
                url_for("payments.left", ref=ref, payment_id=payment.id, _external=True))
    try:
        payment = payments.start(s, order, gateway, utcnow(), urls)
        s.commit()
    except (payments.PaymentProblem, GatewayError) as problem:
        s.rollback()
        flash(str(problem), "error")
        return back(order)
    return redirect(payment.checkout_url)


@bp.route("/orders/<ref>/payments/<int:payment_id>/done", methods=["GET", "POST"])
@needs(SHOP, STAFF_CANT_SHOP)
def returned(ref, payment_id):
    """Where the payment page sends the shopper back (and the Check payment button).
    We never trust the address alone: we ask the gateway."""
    s = db.session()
    order = my_order(ref)
    payment = my_payment(order, payment_id)
    gateway = gateways.for_store(g.store)
    if payment.status == "paid":
        say("already" if not payment.needs_refund else "needs_refund", order)
        return back(order)
    if gateway is None or gateway.name != payment.gateway:
        flash("We can't check this payment right now. Please call the store.", "error")
        return back(order)
    try:
        result = payments.check(s, payment, gateway, utcnow())
        s.commit()
    except GatewayError as problem:
        s.rollback()
        flash(str(problem), "error")
        return back(order)
    except (orders.OrderChanged, IntegrityError):
        s.rollback()
        result = "already"  # the webhook recorded it at the same moment
    say(result, order)
    return back(order)


@bp.get("/orders/<ref>/payments/<int:payment_id>/left")
@needs(SHOP, STAFF_CANT_SHOP)
def left(ref, payment_id):
    order = my_order(ref)
    my_payment(order, payment_id)
    flash("You left the payment page, so nothing was paid. Press Pay to try again, or pay in cash.")
    return back(order)


@bp.post("/orders/<ref>/pay-cash")
@needs(SHOP, STAFF_CANT_SHOP)
def pay_cash(ref):
    s = db.session()
    order = my_order(ref)
    if order.status != AWAITING_PAYMENT:
        abort(400, "This order isn't waiting for an online payment.")
    try:
        payments.switch_to_cash(s, order, g.user, utcnow(), gateways.for_store(g.store))
        s.commit()
    except (orders.OrderChanged, orders.MoveNotAllowed) as problem:
        s.rollback()
        flash(str(problem), "error")
        return back(order)
    flash("Done: you'll pay in cash " + ("when you pick it up." if order.fulfilment == "pickup" else "to the rider."))
    return back(order)


@bp.post("/orders/<ref>/pay-online")
@needs(SHOP, STAFF_CANT_SHOP)
def pay_online(ref):
    s = db.session()
    order = my_order(ref)
    if order.status != CONFIRMED:
        abort(400, "This order can't switch to paying online now.")
    try:
        payments.switch_to_online(s, order, g.user, utcnow())
        s.commit()
    except (payments.PaymentProblem, orders.OrderChanged, orders.MoveNotAllowed) as problem:
        s.rollback()
        flash(str(problem), "error")
        return back(order)
    flash("Done: you can pay online now. Press Pay when you're ready.")
    return back(order)


@bp.post("/payments/webhook")
def webhook():
    """PayMongo tells us about payments here. No login and no form token: the signature is the proof.
    Always answer quickly with 200 for anything we understood, or PayMongo keeps resending."""
    s = db.session()
    body = request.get_data()
    from .. import stores
    gateway = gateways.for_store(stores.main_store(s))
    if gateway is None or not gateway.verify_webhook(body, request.headers.get("Paymongo-Signature", "")):
        return {"error": "bad signature"}, 400
    try:
        data = json.loads(body)
    except ValueError:
        return {"error": "not JSON"}, 400
    event_id, event_type, session_id, gateway_payment_id, paid_with = payments.read_paymongo_event(data)
    if not event_id:
        return {"error": "no event id"}, 400
    try:
        result = payments.handle_event(s, event_id, event_type, session_id, gateway_payment_id, paid_with, utcnow())
        s.commit()
    except (IntegrityError, orders.OrderChanged):
        s.rollback()  # the same event, or the return page, got there first
        result = "duplicate"
    current_app.logger.info("PayMongo %s %s: %s", event_type, event_id, result)
    return {"result": result}


# ---------- The Sandbox (practice) payment page ----------

def sandbox_payment(session_id):
    s = db.session()
    payment = s.scalar(select(Payment).where(Payment.session_id == session_id, Payment.gateway == "sandbox"))
    if payment is None or payment.order.user_id != g.user.id:
        abort(404)
    return payment


@bp.route("/sandbox/pay/<session_id>", methods=["GET", "POST"])
@needs(SHOP, STAFF_CANT_SHOP)
def sandbox(session_id):
    """Looks like a payment page, but no money moves. Pay records the same event a webhook would."""
    s = db.session()
    payment = sandbox_payment(session_id)
    order = payment.order
    if request.method == "POST":
        action = request.form.get("action")
        if action == "cancel":
            return redirect(url_for("payments.left", ref=order.ref, payment_id=payment.id))
        event_type = {"pay": payments.PAID_EVENT, "fail": payments.FAILED_EVENT}.get(action)
        if event_type is None:
            abort(400)
        paid_with = request.form.get("method") if request.form.get("method") in ("gcash", "paymaya", "card") else "gcash"
        try:
            payments.handle_event(s, "sbx_evt_" + secrets.token_urlsafe(10), event_type, session_id,
                                  f"sbx_pay_{payment.id}", paid_with, utcnow())
            s.commit()
        except (IntegrityError, orders.OrderChanged):
            s.rollback()
        if action == "fail":
            flash("The payment was turned down. This was a practice failure, so no real money was involved. "
                  "Try again, or pay in cash.", "error")
            return redirect(url_for("my_orders.detail", ref=order.ref))
        return redirect(url_for("payments.returned", ref=order.ref, payment_id=payment.id))
    store = s.get(Store, order.store_id)
    return render_template("shop/sandbox.html", payment=payment, order=order, store=store)


# ---------- Staff: give the money back ----------

@bp.post("/manage/orders/<ref>/refund")
@needs(PRICE_ORDERS, "Only a cashier or the owner can give refunds.")
def refund(ref):
    s = db.session()
    order = s.scalar(select(Order).where(Order.ref == ref, Order.store_id == g.store.id)
                     .options(selectinload(Order.lines), selectinload(Order.payments)))
    if order is None:
        abort(404)
    reason, _ = forms.text(request.form, "reason", 300)
    if not reason:
        flash("Say why the money is given back. The shopper sees it.", "error")
        return redirect(url_for("staff_orders.detail", ref=ref))
    try:
        done = payments.refund(s, order, g.user, utcnow(), reason, gateways.for_store(g.store))
    except (payments.PaymentProblem, GatewayError) as problem:
        s.rollback()
        flash(f"The refund wasn't sent: {problem}", "error")
        return redirect(url_for("staff_orders.detail", ref=ref))
    flash(f"{done.amount / 100:,.2f} pesos are on the way back to {order.customer_name}.")
    return redirect(url_for("staff_orders.detail", ref=ref))
