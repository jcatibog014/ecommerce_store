"""The shopper's cart and checkout. Placing the order is checkout.place_order (one transaction)."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError

from .. import checkout, db, forms, gateways
from ..checkout import CheckoutProblem
from ..dates import utcnow
from ..models import CartItem, ProductUnit
from ..permissions import SHOP, can, needs

bp = Blueprint("cart", __name__)

STAFF_CANT_SHOP = "Ordering is for shopper accounts. Staff sell at the counter."


def get_item(item_id):
    item = db.session().get(CartItem, item_id)
    if item is None or item.user_id != g.user.id:
        abort(404)
    return item


@bp.post("/cart/add")
def add():
    unit = db.session().get(ProductUnit, request.form.get("unit_id", type=int) or 0)
    if unit is None or g.store is None or unit.product.store_id != g.store.id:
        abort(404)
    back = url_for("shop.product", product_id=unit.product_id)
    if g.user is None:
        flash("Log in or sign up to order. Your account keeps your cart and your orders.")
        return redirect(url_for("auth.login", next=back))
    if not can(SHOP):
        abort(403, STAFF_CANT_SHOP)
    quantity, problem = forms.whole_number(request.form, "quantity", 1, checkout.MAX_QUANTITY, default=1)
    if problem or not unit.is_active or not unit.product.is_active:
        flash(problem or "That isn't sold any more.", "error")
        return redirect(back)
    s = db.session()
    item = checkout.add_to_cart(s, g.user, unit, quantity, utcnow())
    s.commit()
    flash(f"Added {quantity} x {unit.product.name} ({unit.name.lower()}) to your cart. "
          f"You have {item.quantity} of those now.")
    return redirect(back)


@bp.get("/cart")
@needs(SHOP, STAFF_CANT_SHOP)
def index():
    items = checkout.cart_items(db.session(), g.user)
    return render_template("shop/cart.html", items=items, summary=checkout.cart_summary(items))


@bp.post("/cart/<int:item_id>")
@needs(SHOP, STAFF_CANT_SHOP)
def update_item(item_id):
    item = get_item(item_id)
    s = db.session()
    quantity, problem = forms.whole_number(request.form, "quantity", 0, checkout.MAX_QUANTITY)
    if problem:
        flash(problem, "error")
    elif quantity == 0:
        s.delete(item)
        s.commit()
        flash(f"Removed {item.unit.product.name} from your cart.")
    else:
        item.quantity = quantity
        s.commit()
    return redirect(url_for("cart.index"))


@bp.post("/cart/<int:item_id>/remove")
@needs(SHOP, STAFF_CANT_SHOP)
def remove(item_id):
    item = get_item(item_id)
    s = db.session()
    s.delete(item)
    s.commit()
    flash(f"Removed {item.unit.product.name} from your cart.")
    return redirect(url_for("cart.index"))


@bp.route("/checkout", methods=["GET", "POST"])
@needs(SHOP, STAFF_CANT_SHOP)
def checkout_page():
    s = db.session()
    items = checkout.cart_items(s, g.user)
    summary = checkout.cart_summary(items)
    if not summary["can_check_out"]:
        if items:
            flash("Please fix the lines marked in your cart first.", "error")
        return redirect(url_for("cart.index"))
    areas = checkout.active_areas(s, g.store)
    addresses = checkout.saved_addresses(s, g.user)
    errors, form = {}, {}
    if request.method == "POST":
        form = request.form
        choice, errors = checkout.read_checkout(request.form, g.user, areas, addresses,
                                                online_ok=gateways.for_store(g.store) is not None)
        key = request.form.get("idempotency_key", "")
        if not errors:
            try:
                order, _ = checkout.place_order(s, g.user, g.store, choice, key, utcnow())
                s.commit()
            except CheckoutProblem as problem:
                s.rollback()
                flash(str(problem), "error")
                return redirect(url_for("cart.index"))
            except IntegrityError:
                # The same "Place order" twice at the very same moment: the other one won.
                s.rollback()
                order = checkout.find_by_key(s, g.user, key)
                if order is None:
                    raise
            session.pop("checkout_key", None)
            flash(f"Thank you! Order {order.ref} is placed. The cashier will check it and give the final price.")
            return redirect(url_for("my_orders.detail", ref=order.ref))
    # One key per visit to this page: pressing "Place order" twice sends the same one.
    key = session.get("checkout_key") or checkout.new_checkout_key()
    session["checkout_key"] = key
    return render_template("shop/checkout.html", items=items, summary=summary, areas=areas, addresses=addresses,
                           errors=errors, form=form, key=key, online=gateways.for_store(g.store) is not None,
                           online_minimum=gateways.ONLINE_MINIMUM), 400 if errors else 200


def init_app(app):
    @app.context_processor
    def cart_badge():
        # Only shoppers have a cart; one small query per page for the header.
        if g.get("user") is not None and can(SHOP):
            return {"cart_count": checkout.cart_count(db.session(), g.user)}
        return {"cart_count": 0}
