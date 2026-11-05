"""A shopper's Order History and order pages. Shoppers only ever see their own orders."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import db, forms, orders
from ..dates import utcnow
from ..models import DONE_STATUSES, OPEN_STATUSES, Order
from ..permissions import SHOP, needs
from .cart import STAFF_CANT_SHOP

bp = Blueprint("my_orders", __name__)


def my_order(ref):
    order = db.session().scalar(select(Order).where(Order.ref == ref, Order.user_id == g.user.id)
                                .options(selectinload(Order.lines), selectinload(Order.events)))
    if order is None:
        abort(404)
    return order


@bp.get("/orders")
@needs(SHOP, STAFF_CANT_SHOP)
def index():
    found = db.session().scalars(select(Order).where(Order.user_id == g.user.id).order_by(Order.placed_at.desc())
                                 .options(selectinload(Order.lines))).all()
    return render_template("shop/orders.html", open_orders=[o for o in found if o.status in OPEN_STATUSES],
                           past_orders=[o for o in found if o.status in DONE_STATUSES])


@bp.get("/orders/<ref>")
@needs(SHOP, STAFF_CANT_SHOP)
def detail(ref):
    return render_template("shop/order.html", order=my_order(ref))


@bp.post("/orders/<ref>/cancel")
@needs(SHOP, STAFF_CANT_SHOP)
def cancel(ref):
    s = db.session()
    order = my_order(ref)
    if not orders.shopper_can_cancel(order):
        flash("This order can't be cancelled any more, because the store has started on it. "
              "Please call the store.", "error")
        return redirect(url_for("my_orders.detail", ref=ref))
    reason, _ = forms.text(request.form, "reason", orders.CANCEL_REASON_MAX)
    try:
        orders.cancel(s, order, g.user, utcnow(), reason or "Cancelled by the shopper")
        s.commit()
    except (orders.OrderChanged, orders.MoveNotAllowed) as problem:
        s.rollback()
        flash(str(problem), "error")
        return redirect(url_for("my_orders.detail", ref=ref))
    flash(f"Order {order.ref} is cancelled.")
    return redirect(url_for("my_orders.detail", ref=ref))
