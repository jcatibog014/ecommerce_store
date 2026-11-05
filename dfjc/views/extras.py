"""Shopper extras (favorites, Buy again, saved addresses) and the owner's Promos page."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .. import catalog, checkout, db, forms, vouchers
from ..dates import local_today, utcnow
from ..models import (
    VOUCHER_AMOUNT, VOUCHER_DELIVERY, VOUCHER_KINDS, VOUCHER_PERCENT, Address, Favorite, Order, Product, ProductUnit,
    Voucher,
)
from ..permissions import MANAGE_STORE, SHOP, needs
from ..stores import get_in_store
from .cart import STAFF_CANT_SHOP

bp = Blueprint("extras", __name__)


# ---------- Favorites ----------

@bp.post("/favorites/<int:product_id>")
@needs(SHOP, STAFF_CANT_SHOP)
def toggle_favorite(product_id):
    s = db.session()
    product = get_in_store(s, Product, product_id, g.store)
    found = s.scalar(select(Favorite).where(Favorite.user_id == g.user.id, Favorite.product_id == product.id))
    if found:
        s.delete(found)
        flash(f"Removed {product.name} from your favorites.")
    else:
        s.add(Favorite(user_id=g.user.id, product_id=product.id, created_at=utcnow()))
        flash(f"Saved {product.name} to your favorites.")
    try:
        s.commit()
    except IntegrityError:
        s.rollback()  # pressed twice: it is saved already
    back = request.form.get("next", "")
    return redirect(back if back.startswith("/") and not back.startswith("//")
                    else url_for("shop.product", product_id=product.id))


@bp.get("/favorites")
@needs(SHOP, STAFF_CANT_SHOP)
def favorites():
    found = db.session().scalars(select(Product).join(Favorite, Favorite.product_id == Product.id)
                                 .where(Favorite.user_id == g.user.id, Product.is_active.is_(True))
                                 .order_by(Favorite.created_at.desc())
                                 .options(selectinload(Product.units), selectinload(Product.category))).all()
    return render_template("shop/favorites.html", groups=catalog.wrap([p for p in found if p.active_units]))


def favorite_ids(s, user):
    return set(s.scalars(select(Favorite.product_id).where(Favorite.user_id == user.id)))


# ---------- Buy again ----------

@bp.post("/orders/<ref>/buy-again")
@needs(SHOP, STAFF_CANT_SHOP)
def buy_again(ref):
    s = db.session()
    order = s.scalar(select(Order).where(Order.ref == ref, Order.user_id == g.user.id)
                     .options(selectinload(Order.lines)))
    if order is None:
        abort(404)
    added, missing = 0, []
    for line in order.lines:
        if line.is_request:
            continue  # asked-for items aren't in the system; ask for them again on the product page
        unit = s.get(ProductUnit, line.unit_id) if line.unit_id else None
        if unit is None or not unit.is_active or not unit.product.is_active:
            missing.append(line.product_name)
            continue
        checkout.add_to_cart(s, g.user, unit, line.quantity, utcnow())
        added += 1
    s.commit()
    if added:
        flash(f"Added {added} {'item' if added == 1 else 'items'} from {order.ref} to your cart. "
              "Prices are today's, and the cashier checks them again.")
    if missing:
        flash("Not sold any more: " + ", ".join(missing) + ".", "error")
    return redirect(url_for("cart.index"))


# ---------- Saved addresses ----------

def read_address(form, areas):
    errors = {}
    active = {str(area.id): area for area in areas}
    area = active.get(form.get("area_id", ""))
    if area is None:
        errors["area_id"] = "Choose your barangay from the list."
    street, errors["street"] = forms.text(form, "street", 200, required=True, label="Your street address")
    landmark, errors["landmark"] = forms.text(form, "landmark", 200)
    label, errors["label"] = forms.text(form, "label", 40)
    return ({"area_id": area.id if area else None, "street": street, "landmark": landmark, "label": label or "Home"},
            {key: value for key, value in errors.items() if value})


def my_address(address_id):
    address = db.session().get(Address, address_id)
    if address is None or address.user_id != g.user.id:
        abort(404)
    return address


@bp.route("/addresses", methods=["GET", "POST"])
@needs(SHOP, STAFF_CANT_SHOP)
def addresses():
    s = db.session()
    areas = checkout.active_areas(s, g.store)
    errors, values = {}, {}
    if request.method == "POST":
        values, errors = read_address(request.form, areas)
        if not errors:
            s.add(Address(user_id=g.user.id, created_at=utcnow(), **values))
            s.commit()
            flash("Address saved. It's offered when you check out.")
            return redirect(url_for("extras.addresses"))
    return render_template("shop/addresses.html", addresses=checkout.saved_addresses(s, g.user), areas=areas,
                           errors=errors, values=values), 400 if errors else 200


@bp.post("/addresses/<int:address_id>")
@needs(SHOP, STAFF_CANT_SHOP)
def save_address(address_id):
    s = db.session()
    address = my_address(address_id)
    values, errors = read_address(request.form, checkout.active_areas(s, g.store))
    if errors:
        flash("Not saved: " + " ".join(errors.values()), "error")
        return redirect(url_for("extras.addresses"))
    for name, value in values.items():
        setattr(address, name, value)
    s.commit()
    flash(f"{address.label} is saved.")
    return redirect(url_for("extras.addresses"))


@bp.post("/addresses/<int:address_id>/delete")
@needs(SHOP, STAFF_CANT_SHOP)
def delete_address(address_id):
    s = db.session()
    address = my_address(address_id)
    s.delete(address)  # past orders keep their own copy of the address
    s.commit()
    flash(f"{address.label} was removed.")
    return redirect(url_for("extras.addresses"))


# ---------- Promos (the owner's vouchers) ----------

def read_voucher(form):
    errors, values = {}, {}
    values["code"] = vouchers.clean_code(form.get("code"))
    if not vouchers.CODE_PATTERN.fullmatch(values["code"]):
        errors["code"] = "Use 3 to 30 letters, numbers or dashes, like SUKI50."
    values["kind"] = form.get("kind") if form.get("kind") in VOUCHER_KINDS else VOUCHER_AMOUNT
    if values["kind"] == VOUCHER_AMOUNT:
        values["value"], errors["value"] = forms.price(form, "value")
    elif values["kind"] == VOUCHER_PERCENT:
        values["value"], errors["value"] = forms.whole_number(form, "value", 1, 100)
    else:
        values["value"] = 0
    values["min_spend"], errors["min_spend"] = forms.price(form, "min_spend", required=False)
    values["min_spend"] = values["min_spend"] or 0
    values["max_uses"], errors["max_uses"] = forms.whole_number(form, "max_uses", 1, 100_000, default=None)
    if not (form.get("max_uses") or "").strip():
        errors["max_uses"] = None
    from ..dates import parse_day
    for name in ("starts_on", "ends_on"):
        raw = (form.get(name) or "").strip()
        values[name] = parse_day(raw) if raw else None
        if raw and values[name] is None:
            errors[name] = "Use a date like 2026-12-01."
    if values["starts_on"] and values["ends_on"] and values["ends_on"] < values["starts_on"]:
        errors["ends_on"] = "The end can't be before the start."
    values["description"], errors["description"] = forms.text(form, "description", 160)
    values["show_on_home"] = forms.checkbox(form, "show_on_home")
    return values, {key: value for key, value in errors.items() if value}


@bp.route("/manage/promos", methods=["GET", "POST"])
@needs(MANAGE_STORE, "Only the owner can change promos.")
def promos():
    s = db.session()
    errors, values = {}, {"kind": VOUCHER_AMOUNT, "show_on_home": True}
    if request.method == "POST":
        values, errors = read_voucher(request.form)
        if not errors:
            try:
                s.add(Voucher(store_id=g.store.id, created_at=utcnow(), **values))
                s.commit()
            except IntegrityError:
                s.rollback()
                errors["code"] = "There is already a voucher with that code."
            else:
                flash(f"{values['code']} is ready. Shoppers type it at checkout.")
                return redirect(url_for("extras.promos"))
    found = s.scalars(select(Voucher).where(Voucher.store_id == g.store.id).order_by(Voucher.created_at.desc())).all()
    discounts = dict(s.execute(select(Order.voucher_id, func.sum(Order.discount)).where(
        Order.store_id == g.store.id, Order.voucher_id.is_not(None), Order.status.not_in(("cancelled", "refunded")))
        .group_by(Order.voucher_id)).all())
    return render_template("manage/promos.html", vouchers=found, values=values, errors=errors, kinds=VOUCHER_KINDS,
                           describe=vouchers.describe, discounts=discounts, today=local_today(),
                           VOUCHER_DELIVERY=VOUCHER_DELIVERY), 400 if errors else 200


@bp.post("/manage/promos/<int:voucher_id>/active")
@needs(MANAGE_STORE, "Only the owner can change promos.")
def voucher_active(voucher_id):
    s = db.session()
    voucher = get_in_store(s, Voucher, voucher_id, g.store)
    voucher.is_active = request.form.get("active") == "1"
    s.commit()
    flash(f"{voucher.code} works again." if voucher.is_active else f"{voucher.code} is switched off.")
    return redirect(url_for("extras.promos"))


def init_app(app):
    app.jinja_env.globals.update(describe_voucher=vouchers.describe)
