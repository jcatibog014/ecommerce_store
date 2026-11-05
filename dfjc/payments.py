"""Paying online, and every way it can go wrong.

1. The cashier prices an online order: it waits for payment (orders.py).
2. The shopper presses Pay: start() writes a Payment row (pending) and asks the
   gateway for a checkout session, then the shopper goes to the payment page.
3. The money arrives. We hear about it TWO ways, and either may come first,
   twice, or never:
     - PayMongo's webhook (handle_event), signed with our webhook secret;
     - the shopper coming back to our site (the return page asks PayMongo).
   Both end in mark_paid(), whose conditional UPDATE
       UPDATE payments SET status = 'paid' WHERE id = ? AND status IN ('pending', 'expired', 'failed')
   lets only the FIRST one through. The rest change nothing.
4. The payment and the order's move to Paid are saved in one transaction.

A webhook event id is UNIQUE in payment_events, so the same event sent again
is ignored. A payment for an order that was cancelled (or already paid) in
the meantime isn't lost: it is marked needs_refund for the staff.

Refunds call another company, which no database rollback can undo, so the
Refund row is written FIRST (pending), then PayMongo is asked, then the
result is saved.
"""
from datetime import timedelta

from sqlalchemy import select, update

from . import gateways, orders
from .gateways import GatewayError
from .models import (
    AWAITING_PAYMENT, CASH_PICKUP, COD, CONFIRMED, ONLINE, PAID, PAYMENT_EXPIRED, PAYMENT_FAILED,
    PAYMENT_PAID, PAYMENT_PENDING, PAYMENT_REFUNDED, PICKUP, REFUNDED, OrderEvent, Payment, PaymentEvent, Refund,
)

PENDING_LIFETIME = timedelta(minutes=30)
PAID_EVENT = "checkout_session.payment.paid"
FAILED_EVENT = "payment.failed"
PAID_WITH_LABELS = {"gcash": "GCash", "paymaya": "Maya", "card": "card", "grab_pay": "GrabPay", "cash": "cash"}


class PaymentProblem(ValueError):
    pass


def online_problem(store, total):
    """Why this store can't take total online right now, or None."""
    if gateways.for_store(store) is None:
        return "Online payment is off for now. Please pay in cash."
    if total < gateways.ONLINE_MINIMUM:
        return "Online payment starts at ₱20. Please pay in cash for this order."
    return None


def refresh(s, *rows):
    for row in rows:
        s.refresh(row)


# ---------- Starting ----------

def start(s, order, gateway, moment, urls):
    """A pending Payment with a checkout session, or the open one from a moment ago.
    urls(payment) -> (success_url, cancel_url). The caller commits; a GatewayError
    rolls everything back (nothing is half made)."""
    if order.status != AWAITING_PAYMENT:
        raise PaymentProblem("This order isn't waiting for an online payment.")
    if order.total < gateways.ONLINE_MINIMUM:
        raise PaymentProblem("Online payment starts at ₱20. Please pay in cash for this order.")
    for payment in order.payments:
        if payment.status == PAYMENT_PENDING:
            if payment.expires_at > moment and payment.amount == order.total and payment.gateway == gateway.name:
                return payment  # pressing Pay again goes back to the same page
            close_session(s, payment, gateway)
    payment = Payment(order_id=order.id, gateway=gateway.name, amount=order.total, created_at=moment,
                      expires_at=moment + PENDING_LIFETIME)
    s.add(payment)
    s.flush()  # its id goes into the session's metadata and our return address
    success_url, cancel_url = urls(payment)
    payment.session_id, payment.checkout_url = gateway.create_checkout(
        amount=payment.amount, ref=order.ref, description=f"{order.item_count} items from DFJC Store",
        success_url=success_url, cancel_url=cancel_url, payment_id=payment.id)
    order.payments.append(payment)
    return payment


def close_open(s, order, gateway):
    """Before cancelling: close any payment page still open, so it can't be paid any more.
    (If it is paid at that very moment anyway, mark_paid flags it for a refund.)"""
    for payment in order.payments:
        if payment.status == PAYMENT_PENDING and gateway is not None and gateway.name == payment.gateway:
            close_session(s, payment, gateway)


def close_session(s, payment, gateway):
    """An unpaid session we no longer want: stop it at the gateway and call it expired here."""
    if payment.session_id:
        gateway.expire(payment.session_id)
    s.execute(update(Payment).where(Payment.id == payment.id, Payment.status == PAYMENT_PENDING)
              .values(status=PAYMENT_EXPIRED).execution_options(synchronize_session=False))
    s.refresh(payment)


# ---------- The money arrives (or doesn't) ----------

def mark_paid(s, payment, gateway_payment_id, paid_with, moment):
    """Record a payment once. Returns "paid", "already" (someone else recorded it first)
    or "needs_refund" (the order was cancelled or already paid). The caller commits."""
    changed = s.execute(
        update(Payment)
        .where(Payment.id == payment.id, Payment.status.in_((PAYMENT_PENDING, PAYMENT_EXPIRED, PAYMENT_FAILED)))
        .values(status=PAYMENT_PAID, paid_at=moment, gateway_payment_id=gateway_payment_id, paid_with=paid_with or "")
        .execution_options(synchronize_session=False)).rowcount
    if changed != 1:
        return "already"
    order = payment.order
    refresh(s, payment, order)
    if order.status == AWAITING_PAYMENT and order.total == payment.amount:
        label = PAID_WITH_LABELS.get(paid_with, "online")
        orders.move(s, order, PAID, None, moment, note=f"{order.total / 100:,.2f} pesos with {label}")
        for other in order.payments:
            if other.id != payment.id and other.status == PAYMENT_PENDING:
                s.execute(update(Payment).where(Payment.id == other.id, Payment.status == PAYMENT_PENDING)
                          .values(status=PAYMENT_EXPIRED).execution_options(synchronize_session=False))
        return "paid"
    # Paid for an order that was cancelled, switched to cash, or paid by another session.
    payment.needs_refund = True
    return "needs_refund"


def mark_failed(s, payment):
    changed = s.execute(update(Payment).where(Payment.id == payment.id, Payment.status == PAYMENT_PENDING)
                        .values(status=PAYMENT_FAILED).execution_options(synchronize_session=False)).rowcount
    refresh(s, payment)
    return "failed" if changed else "already"


def check(s, payment, gateway, moment):
    """Ask the gateway (the return page and the Check payment button). Returns what happened."""
    status, gateway_payment_id, paid_with = gateway.checkout_status(payment.session_id)
    if status == "paid":
        return mark_paid(s, payment, gateway_payment_id, paid_with, moment)
    if status == "expired" or (payment.expires_at and payment.expires_at <= moment):
        if payment.status == PAYMENT_PENDING:
            close_session(s, payment, gateway)
        return "expired"
    return "unpaid"


def handle_event(s, event_id, event_type, session_id, gateway_payment_id, paid_with, moment):
    """One webhook event (or the Sandbox's Pay button). The UNIQUE event id stops a second copy:
    a copy that slips past the check below fails on INSERT instead (the caller rolls back)."""
    if s.scalar(select(PaymentEvent.id).where(PaymentEvent.event_id == event_id)) is not None:
        return "duplicate"
    event = PaymentEvent(event_id=event_id, event_type=event_type, session_id=session_id or "", received_at=moment)
    s.add(event)
    s.flush()
    payment = s.scalar(select(Payment).where(Payment.session_id == session_id)) if session_id else None
    if payment is None:
        event.result = "unknown session"
    elif event_type == PAID_EVENT:
        event.result = mark_paid(s, payment, gateway_payment_id, paid_with, moment)
    elif event_type == FAILED_EVENT:
        event.result = mark_failed(s, payment)
    else:
        event.result = "ignored"
    return event.result


def read_paymongo_event(data):
    """(event id, type, session id, payment id, paid with) from PayMongo's webhook JSON."""
    event = data.get("data") or {}
    attributes = event.get("attributes") or {}
    resource = attributes.get("data") or {}
    session_id = resource.get("id", "") if resource.get("type") == "checkout_session" else ""
    payment_id, paid_with = "", ""
    for payment in (resource.get("attributes") or {}).get("payments") or []:
        details = payment.get("attributes") or {}
        if details.get("status") == "paid":
            payment_id, paid_with = payment.get("id", ""), (details.get("source") or {}).get("type", "")
    return event.get("id", ""), attributes.get("type", ""), session_id, payment_id, paid_with


# ---------- Changing the way to pay ----------

def switch_to_cash(s, order, by, moment, gateway):
    """Waiting for payment -> confirmed, paid in cash. Open sessions are closed first; if one
    is paid at that very moment, mark_paid sees the order isn't waiting and flags a refund."""
    for payment in order.payments:
        if payment.status == PAYMENT_PENDING and gateway is not None:
            close_session(s, payment, gateway)
    method = CASH_PICKUP if order.fulfilment == PICKUP else COD
    return orders.move(s, order, CONFIRMED, by, moment, note="Switched to cash", payment_method=method)


def switch_to_online(s, order, by, moment):
    problem = online_problem(order_store(s, order), order.total)
    if problem:
        raise PaymentProblem(problem)
    return orders.move(s, order, AWAITING_PAYMENT, by, moment, note="Switched to paying online",
                       payment_method=ONLINE)


def order_store(s, order):
    from .models import Store
    return s.get(Store, order.store_id)


# ---------- Giving money back ----------

def refundable(order):
    """The payment to give back: a paid order not packed yet, or a payment flagged needs_refund."""
    for payment in order.payments:
        if payment.status == PAYMENT_PAID and (payment.needs_refund or order.status == PAID):
            return payment
    return None


def refund(s, order, by, moment, reason, gateway):
    """Give the money back and (for a paid order) move it to Refunded, giving back its stock.
    Commits twice on purpose: the pending Refund first, then the result."""
    payment = refundable(order)
    if payment is None:
        raise PaymentProblem("There is no online payment to give back on this order.")
    if gateway is None:
        raise PaymentProblem("Online payment is off, so the refund can't be sent. Pay back in cash.")
    amount = payment.amount - payment.refunded_amount
    pending = Refund(payment_id=payment.id, amount=amount, reason=reason, by_id=by.id, created_at=moment)
    s.add(pending)
    s.commit()  # what we are about to try is on record before we call PayMongo
    try:
        pending.gateway_refund_id = gateway.refund(payment.gateway_payment_id, amount, reason)
    except GatewayError as problem:
        pending.status, pending.error = "failed", str(problem)[:300]
        s.commit()
        raise
    extra = payment.needs_refund  # money that came in after a cancel, or a second payment
    pending.status = "done"
    payment.refunded_amount += amount
    payment.status = PAYMENT_REFUNDED
    payment.needs_refund = False
    if order.status == PAID and not extra:
        orders.move(s, order, REFUNDED, by, moment, note=reason)
    else:
        # The order itself stays as it is; the timeline still says money went back.
        s.add(OrderEvent(order_id=order.id, from_status=order.status, to_status=REFUNDED, by_id=by.id,
                         created_at=moment, note=f"{amount / 100:,.2f} pesos given back. {reason}".strip()))
    s.commit()
    return pending
