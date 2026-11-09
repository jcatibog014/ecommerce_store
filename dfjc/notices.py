"""Order emails, with an OUTBOX.

queue() writes a notifications row inside the same transaction as the order's
change. If that change rolls back, so does the email: nobody gets "your order
is paid" for a payment that didn't save. deliver() sends what is waiting after
the request has committed; a failed send is tried again (3 times) and never
undoes the order. Demo stores send nothing (their people are made up).

Shoppers can turn these emails off in Settings.
"""
from flask import g, has_request_context, url_for
from sqlalchemy import select

from . import mailer
from .models import (
    AWAITING_PAYMENT, CANCELLED, CONFIRMED, DELIVERED, OUT_FOR_DELIVERY, PAID, PICKED_UP, PICKUP, PLACED, READY,
    REFUNDED, Notification, Store, User,
)

MAX_TRIES = 3


def peso(centavos):
    return f"₱{centavos / 100:,.2f}"


def message(order, to_status, from_status, store):
    """(subject, the first lines) for this change, or None when it isn't worth an email."""
    ref, total = order.ref, peso(order.total)
    if to_status == PLACED and from_status is not None:
        return None  # the shopper changed their own order: the new price comes by email
    if to_status == PLACED:
        return (f"We got your order {ref}",
                "Thank you! The cashier checks your order next and gives the wholesale price where it applies. "
                "We'll email you the final total.")
    if to_status == AWAITING_PAYMENT and from_status == PLACED:
        return f"Order {ref}: {total}. Please pay now", f"Your order is priced: {total}. Pay online on the order page."
    if to_status == CONFIRMED and from_status == PLACED:
        how = "when you pick it up" if order.fulfilment == PICKUP else "to the rider"
        return f"Order {ref}: {total}", f"Your order is priced: {total}. Please pay in cash {how}."
    if to_status == PAID:
        return f"Order {ref} is paid", f"Thank you! We received {total}. We'll tell you when it's ready."
    if to_status == READY and order.fulfilment == PICKUP:
        return (f"Order {ref} is ready for pickup",
                f"Come to {store.name} and say your order number, {ref}." +
                (f" Please bring {total}." if order.payment_method == "cash_pickup" else ""))
    if to_status == OUT_FOR_DELIVERY:
        return (f"Order {ref} is on the way",
                "Our rider is bringing it now." + (f" Please have {total} ready." if order.payment_method == "cod" else ""))
    if to_status in (PICKED_UP, DELIVERED):
        return f"Thank you for your order {ref}", f"Thank you for shopping at {store.name}!"
    if to_status == CANCELLED:
        return f"Order {ref} is cancelled", "Your order was cancelled. Nothing was charged."
    if to_status == REFUNDED:
        return f"Order {ref}: refunded", f"Your order was cancelled and {total} was sent back to how you paid."
    return None


def queue(s, order, to_status, from_status, moment, note=""):
    """Add an email to the outbox (inside the caller's transaction)."""
    store = s.get(Store, order.store_id)
    user = s.get(User, order.user_id) if order.user_id else None
    if store is None or store.is_demo or user is None or not user.order_emails or not user.is_active:
        return None
    found = message(order, to_status, from_status, store)
    if found is None:
        return None
    subject, opening = found
    link = url_for("my_orders.detail", ref=order.ref, _external=True) if has_request_context() else ""
    body = f"Hello {user.first_name},\n\n{opening}\n"
    if note and to_status in (CANCELLED, REFUNDED):
        body += f"\nThe store says: {note}\n"
    if link:
        body += f"\nSee your order: {link}\n"
    body += f"\n{store.name}\n{store.phone}\n\nYou can turn these emails off in Settings.\n"
    notice = Notification(store_id=store.id, user_id=user.id, order_id=order.id, to_email=user.email,
                          subject=subject, body=body, created_at=moment)
    s.add(notice)
    if has_request_context():
        g.notices_waiting = True
    return notice


def deliver(s, moment, limit=20):
    """Send what is waiting. Each email is its own small commit, so one failure stops nothing else."""
    waiting = s.scalars(select(Notification).where(Notification.status.in_(("pending", "failed")),
                                                   Notification.tries < MAX_TRIES)
                        .order_by(Notification.created_at).limit(limit)).all()
    for notice in waiting:
        try:
            mailer.send(notice.to_email, notice.subject, notice.body)
        except Exception as problem:  # any mail error: keep it for the next try
            notice.tries += 1
            notice.status, notice.error = "failed", str(problem)[:300]
        else:
            notice.tries += 1
            notice.status, notice.sent_at = "sent", moment
        s.commit()
    return len(waiting)


def init_app(app):
    from . import db
    from .dates import utcnow

    @app.after_request
    def send_waiting(response):
        # Only after a request that queued emails, and only if its work was saved (2xx/3xx).
        if g.pop("notices_waiting", False) and response.status_code < 400:
            try:
                deliver(db.session(), utcnow())
            except Exception:  # the page must never fail because of email
                app.logger.exception("Sending order emails failed")
        return response

    import click
    from flask.cli import with_appcontext

    @click.command("send-emails")
    @with_appcontext
    def send_emails_command():
        """Send order emails still waiting in the outbox."""
        click.echo(f"Tried {deliver(db.session(), utcnow(), limit=200)} emails.")

    app.cli.add_command(send_emails_command)
