"""Staff pages for the catalog: products, their units and prices, photos, and categories.

The owner adds and edits everything. A cashier sees the same pages with the
forms switched off (they need the stock and prices at the counter).
"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from .. import catalog, db, photos
from ..auth import not_for_demo
from ..dates import utcnow
from ..models import CATEGORY_ICONS, Category, Photo, Product, ProductUnit
from ..permissions import MANAGE_PRODUCTS, SEE_PRODUCTS, can, needs
from ..stores import get_in_store

bp = Blueprint("manage", __name__, url_prefix="/manage")

CONFLICT = ("Someone else saved this product while you were editing it, so your change wasn't saved. "
            "This is the newest version: check it and save again.")


def get_product(product_id):
    return get_in_store(db.session(), Product, product_id, g.store)


def get_unit(product, unit_id):
    unit = db.session().get(ProductUnit, unit_id)
    if unit is None or unit.product_id != product.id:
        abort(404)
    return unit


def to_product(product, anchor=None):
    return redirect(url_for("manage.product", product_id=product.id, _anchor=anchor))


# ---------- Products ----------

@bp.get("/products")
@needs(SEE_PRODUCTS)
def products():
    s = db.session()
    categories = catalog.categories(s, g.store)
    q = (request.args.get("q") or "").strip()[:80]
    raw = request.args.get("category", "")
    category = int(raw) if raw.isdigit() and int(raw) in {c.id for c in categories} else None
    show = request.args.get("show") if request.args.get("show") in catalog.STAFF_SHOWS else "all"
    found = catalog.staff_products(s, g.store, q, category, show)
    low_count = len(catalog.staff_products(s, g.store, show="low"))
    return render_template("manage/products.html", products=found, categories=categories, q=q, category=category,
                           show=show, shows=catalog.STAFF_SHOWS, low_count=low_count)


@bp.route("/products/new", methods=["GET", "POST"])
@needs(MANAGE_PRODUCTS, "Only the owner can add products.")
def new_product():
    s = db.session()
    categories = catalog.categories(s, g.store)
    values, unit, errors = {"is_active": True}, {"name": "Piece", "pieces_per_unit": 1}, {}
    if request.method == "POST":
        values, errors = catalog.read_product_form(request.form, categories, with_stock=True)
        unit, unit_errors = catalog.read_unit_form(request.form, prefix="unit_")
        errors.update({"unit_" + key: value for key, value in unit_errors.items()})
        if not errors:
            moment = utcnow()
            product = Product(store_id=g.store.id, created_at=moment, updated_at=moment, **values)
            product.units.append(ProductUnit(**unit))
            s.add(product)
            s.commit()
            flash(f"{product.name} is in the store now, sold by the {unit['name'].lower()}. Add more units below.")
            return to_product(product, "units")
    return render_template("manage/new_product.html", categories=categories, values=values, unit=unit,
                           errors=errors), 400 if errors else 200


@bp.route("/products/<int:product_id>", methods=["GET", "POST"])
@needs(SEE_PRODUCTS)
def product(product_id):
    s = db.session()
    item = get_product(product_id)
    categories = catalog.categories(s, g.store)
    errors = {}
    values = None
    if request.method == "POST":
        if not can(MANAGE_PRODUCTS):
            abort(403, "Only the owner can change products.")
        values, errors = catalog.read_product_form(request.form, categories, with_stock=True)
        if "stock_pieces" not in errors and values["stock_pieces"] < item.reserved_pieces:
            # The database would refuse it too (a CHECK): held pieces are on the shelf.
            errors["stock_pieces"] = (f"{item.reserved_pieces:,} pieces are held for orders, "
                                      f"so there are at least {item.reserved_pieces:,} on the shelf.")
        if request.form.get("version") != str(item.version):
            # The form was opened before someone else's save.
            flash(CONFLICT, "error")
            return to_product(item)
        if not errors:
            for name, value in values.items():
                setattr(item, name, value)
            item.updated_at = utcnow()
            try:
                s.commit()
            except StaleDataError:
                # Someone saved between our check and our save: the UPDATE found no row with our version.
                s.rollback()
                flash(CONFLICT, "error")
                return to_product(item)
            flash(f"{item.name} is saved.")
            return to_product(item)
    return render_template("manage/product.html", product=item, categories=categories,
                           values=values or item_values(item), errors=errors), 400 if errors else 200


def item_values(item):
    return {"name": item.name, "description": item.description, "category_id": item.category_id,
            "low_stock_pieces": item.low_stock_pieces, "is_active": item.is_active, "stock_pieces": item.stock_pieces}


@bp.post("/products/<int:product_id>/units")
@needs(MANAGE_PRODUCTS, "Only the owner can change prices.")
def add_unit(product_id):
    s = db.session()
    item = get_product(product_id)
    values, errors = catalog.read_unit_form(request.form, prefix="new_")
    if errors:
        flash("The new unit wasn't added: " + " ".join(errors.values()), "error")
        return to_product(item, "units")
    try:
        s.add(ProductUnit(product_id=item.id, **values))
        s.commit()
    except IntegrityError:
        s.rollback()
        flash(f"{item.name} already has a unit called \"{values['name']}\". Use another name.", "error")
        return to_product(item, "units")
    flash(f"Added \"{values['name']}\" to {item.name}.")
    return to_product(item, "units")


@bp.post("/products/<int:product_id>/units/<int:unit_id>")
@needs(MANAGE_PRODUCTS, "Only the owner can change prices.")
def save_unit(product_id, unit_id):
    s = db.session()
    item = get_product(product_id)
    unit = get_unit(item, unit_id)
    values, errors = catalog.read_unit_form(request.form, prefix=f"u{unit.id}_")
    if errors:
        flash(f"\"{unit.name}\" wasn't saved: " + " ".join(errors.values()), "error")
        return to_product(item, "units")
    for name, value in values.items():
        setattr(unit, name, value)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        flash(f"{item.name} already has a unit called \"{values['name']}\". Use another name.", "error")
        return to_product(item, "units")
    flash(f"\"{unit.name}\" is saved.")
    return to_product(item, "units")


@bp.post("/products/<int:product_id>/units/<int:unit_id>/delete")
@needs(MANAGE_PRODUCTS, "Only the owner can change prices.")
def delete_unit(product_id, unit_id):
    s = db.session()
    item = get_product(product_id)
    unit = get_unit(item, unit_id)
    s.delete(unit)
    s.commit()
    flash(f"\"{unit.name}\" was removed from {item.name}.")
    return to_product(item, "units")


@bp.post("/products/<int:product_id>/photo")
@needs(MANAGE_PRODUCTS, "Only the owner can change photos.")
def upload_photo(product_id):
    not_for_demo("The demo can't upload photos. In your own store, the owner adds them here.")
    s = db.session()
    item = get_product(product_id)
    upload = request.files.get("photo")
    try:
        photo = photos.make_photo(g.store.id, upload.read() if upload else b"")
    except photos.PhotoProblem as problem:
        flash(str(problem), "error")
        return to_product(item, "photo")
    old_photo_id = item.photo_id
    s.add(photo)
    s.flush()
    # A plain UPDATE: a new photo isn't a change to the product's details, so it
    # shouldn't make someone else's open product form out of date.
    s.execute(update(Product).where(Product.id == item.id).values(photo_id=photo.id))
    if old_photo_id:
        s.execute(Photo.__table__.delete().where(Photo.id == old_photo_id))  # the old picture isn't needed
    s.commit()
    flash(f"The photo of {item.name} is saved.")
    return to_product(item, "photo")


@bp.post("/products/<int:product_id>/photo/delete")
@needs(MANAGE_PRODUCTS, "Only the owner can change photos.")
def delete_photo(product_id):
    s = db.session()
    item = get_product(product_id)
    if item.photo_id:
        s.execute(Photo.__table__.delete().where(Photo.id == item.photo_id))
        s.commit()
        flash(f"The photo of {item.name} was removed.")
    return to_product(item, "photo")


# ---------- Categories ----------

@bp.route("/categories", methods=["GET", "POST"])
@needs(SEE_PRODUCTS)
def categories():
    s = db.session()
    errors = {}
    values = {"icon": "box", "sort_order": 0}
    if request.method == "POST":
        if not can(MANAGE_PRODUCTS):
            abort(403, "Only the owner can change categories.")
        values, errors = catalog.read_category_form(request.form)
        if not errors:
            try:
                s.add(Category(store_id=g.store.id, **values))
                s.commit()
            except IntegrityError:
                s.rollback()
                errors["name"] = "There is already a category with that name."
            else:
                flash(f"Added the category {values['name']}.")
                return redirect(url_for("manage.categories"))
    found = catalog.categories(s, g.store)
    counts = dict(s.execute(select(Product.category_id, func.count()).where(Product.store_id == g.store.id)
                            .group_by(Product.category_id)).all())
    return render_template("manage/categories.html", categories=found, counts=counts, values=values, errors=errors,
                           icons=CATEGORY_ICONS), 400 if errors else 200


@bp.post("/categories/<int:category_id>")
@needs(MANAGE_PRODUCTS, "Only the owner can change categories.")
def save_category(category_id):
    s = db.session()
    category = get_in_store(s, Category, category_id, g.store)
    values, errors = catalog.read_category_form(request.form)
    if errors:
        flash(f"{category.name} wasn't saved: " + " ".join(errors.values()), "error")
        return redirect(url_for("manage.categories"))
    for name, value in values.items():
        setattr(category, name, value)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        flash("There is already a category with that name.", "error")
        return redirect(url_for("manage.categories"))
    flash(f"{category.name} is saved.")
    return redirect(url_for("manage.categories"))


@bp.post("/categories/<int:category_id>/delete")
@needs(MANAGE_PRODUCTS, "Only the owner can change categories.")
def delete_category(category_id):
    s = db.session()
    category = get_in_store(s, Category, category_id, g.store)
    s.delete(category)  # its products stay, with no category (ON DELETE SET NULL)
    s.commit()
    flash(f"The category {category.name} was removed. Its products are still in the store, without a category.")
    return redirect(url_for("manage.categories"))
