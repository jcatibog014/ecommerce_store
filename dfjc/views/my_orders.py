"""A shopper's Order History and order pages. Shoppers only ever see their own orders."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import db, edits, forms, gateways, orders, payments
from ..dates import utcnow
from ..models import DONE_STATUSES, OPEN_STATUSES, Order
from ..permissions import SHOP, needs
from .cart import STAFF_CANT_SHOP

bp = Blueprint("my_orders", __name__)


def my_order(ref):
    order = db.session().scalar(select(Order).where(Order.ref == ref, Order.user_id == g.user.id)
                                .options(selectinload(Order.lines), selectinload(Order.events),
                                         selectinload(Order.payments)))
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
    order = my_order(ref)
    online_problem = payments.online_problem(g.store, order.total)
    is_editing = session.get("editing_order") == order.ref and edits.can_edit(order)
    if session.get("editing_order") == order.ref and not is_editing:
        session.pop("editing_order", None)  # the store started on it meanwhile
    return render_template("shop/order.html", order=order, online_problem=online_problem, is_editing=is_editing,
                           can_edit=edits.can_edit(order), MAX_QUANTITY=edits.checkout.MAX_QUANTITY)


# ---------- Changing a placed order (edits.py) ----------

@bp.post("/orders/<ref>/edit")
@needs(SHOP, STAFF_CANT_SHOP)
def start_edit(ref):
    """Editing mode: the shop's Add to cart buttons add to this order until Done editing."""
    order = my_order(ref)
    if not edits.can_edit(order):
        flash("This order can't be changed any more: the store has started on it, or it is paid. "
              "Please call the store.", "error")
        return redirect(url_for("my_orders.detail", ref=ref))
    session["editing_order"] = order.ref
    flash(f"You're editing {order.ref}. Change the amounts below, or add items from the shop: "
          "Add to cart adds them to this order. Press Done editing when you're finished.")
    return redirect(url_for("my_orders.detail", ref=ref, _anchor="items"))


@bp.post("/orders/<ref>/done")
@needs(SHOP, STAFF_CANT_SHOP)
def done_edit(ref):
    order = my_order(ref)
    session.pop("editing_order", None)
    flash(f"Done editing {order.ref}. The cashier checks it next and tells you the final total."
          if order.status == "placed" else f"Done editing {order.ref}. Nothing was changed.")
    return redirect(url_for("my_orders.detail", ref=ref))


@bp.post("/orders/<ref>/lines/<int:line_id>")
@needs(SHOP, STAFF_CANT_SHOP)
def change_line(ref, line_id):
    s = db.session()
    order = my_order(ref)
    line = next((item for item in order.lines if item.id == line_id), None)
    if line is None:
        abort(404)
    if request.form.get("action") == "remove":
        quantity = 0
    else:
        quantity, problem = forms.whole_number(request.form, "quantity", 0, edits.checkout.MAX_QUANTITY)
        if problem:
            flash(problem, "error")
            return redirect(url_for("my_orders.detail", ref=ref, _anchor="items"))
    what = f"{line.product_name} ({line.unit_name.lower()})"
    try:
        edits.set_quantity(s, order, line, quantity, g.user, utcnow(), gateways.for_store(g.store))
        s.commit()
    except (edits.EditProblem, orders.OrderChanged, orders.MoveNotAllowed) as problem:
        s.rollback()
        flash(str(problem), "error")
        return redirect(url_for("my_orders.detail", ref=ref, _anchor="items"))
    flash(f"Removed {what}." if quantity == 0 else f"{what}: now {quantity}.")
    return redirect(url_for("my_orders.detail", ref=ref, _anchor="items"))


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
        payments.close_open(s, order, gateways.for_store(g.store))
        orders.cancel(s, order, g.user, utcnow(), reason or "Cancelled by the shopper")
        s.commit()
    except (orders.OrderChanged, orders.MoveNotAllowed) as problem:
        s.rollback()
        flash(str(problem), "error")
        return redirect(url_for("my_orders.detail", ref=ref))
    flash(f"Order {order.ref} is cancelled.")
    return redirect(url_for("my_orders.detail", ref=ref))
