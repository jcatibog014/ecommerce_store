"""Staff pages for the catalog: products, their units and prices, photos, and categories.

The owner adds and edits everything. A cashier sees the same pages with the
forms switched off (they need the stock and prices at the counter).
"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from .. import catalog, db, forms, importer, photos, stock
from ..auth import not_for_demo
from ..dates import utcnow
from ..models import CATEGORY_ICONS, MOVE_REASONS, Category, Photo, Product, ProductUnit
from ..permissions import IMPORT_PRODUCTS, MANAGE_PRODUCTS, MANAGE_STOCK, SEE_PRODUCTS, can, needs
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
    like = request.args.get("like", type=int)
    if like and request.method == "GET":
        # "Add another kind": start from that product (its name, brand, category and first unit).
        source = get_product(like)
        values = {**item_values(source), "kind": ""}
        first = source.units[0] if source.units else None
        if first:
            unit = {"name": first.name, "pieces_per_unit": first.pieces_per_unit, "retail_price": first.retail_price,
                    "wholesale_price": first.wholesale_price}
    if request.method == "POST":
        values, errors = catalog.read_product_form(request.form, categories, with_stock=True)
        unit, unit_errors = catalog.read_unit_form(request.form, prefix="unit_")
        errors.update({"unit_" + key: value for key, value in unit_errors.items()})
        if not errors:
            moment = utcnow()
            product = Product(store_id=g.store.id, created_at=moment, updated_at=moment, **values)
            product.units.append(ProductUnit(**unit))
            like_units = []
            source_id = request.form.get("like", type=int)
            if source_id:
                # The other sizes (Pack, Case) of the product this one was copied from.
                source = get_product(source_id)
                like_units = [item for item in source.units if item.name.casefold() != unit["name"].casefold()]
            for item in like_units:
                product.units.append(ProductUnit(name=item.name, pieces_per_unit=item.pieces_per_unit,
                                                 retail_price=item.retail_price, wholesale_price=item.wholesale_price))
            s.add(product)
            s.flush()
            stock.starting_stock(s, product, g.user, moment)  # the first line of its stock history
            s.commit()
            flash(f"{product.name} is in the store now, sold by the {unit['name'].lower()}. Add more units below.")
            return to_product(product, "units")
    return render_template("manage/new_product.html", categories=categories, values=values, unit=unit,
                           errors=errors, like=like or request.form.get("like", type=int)), 400 if errors else 200


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
        # Stock isn't on this form: it changes with Restock and Count, which keep its history.
        values, errors = catalog.read_product_form(request.form, categories, with_stock=False)
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
                           values=values or item_values(item), errors=errors,
                           moves=stock.history(s, g.store, product_id=item.id, limit=8)), 400 if errors else 200


def item_values(item):
    return {"title": item.group_title, "brand": item.brand, "kind": item.kind, "description": item.description,
            "category_id": item.category_id, "low_stock_pieces": item.low_stock_pieces, "is_active": item.is_active}


# ---------- Stock ----------

@bp.post("/products/<int:product_id>/restock")
@needs(MANAGE_STOCK, "Only a cashier or the owner can change stock.")
def restock(product_id):
    s = db.session()
    item = get_product(product_id)
    pieces, problem = forms.whole_number(request.form, "pieces", 1, stock.MAX_PIECES)
    note, _ = forms.text(request.form, "note", 200)
    if problem:
        flash(problem, "error")
        return to_product(item, "stock")
    move = stock.restock(s, item, pieces, g.user, utcnow(), note)
    s.commit()
    flash(f"Added {pieces:,} pieces of {item.name}. There are {move.stock_after:,} now.")
    return to_product(item, "stock")


@bp.post("/products/<int:product_id>/count")
@needs(MANAGE_STOCK, "Only a cashier or the owner can change stock.")
def count(product_id):
    s = db.session()
    item = get_product(product_id)
    counted, problem = forms.whole_number(request.form, "counted", 0, stock.MAX_PIECES)
    seen = request.form.get("seen", type=int)
    note, _ = forms.text(request.form, "note", 200)
    if problem or seen is None:
        flash(problem or "Please count again.", "error")
        return to_product(item, "stock")
    try:
        move = stock.count(s, item, counted, seen, g.user, utcnow(), note)
        s.commit()
    except stock.StockProblem as trouble:
        s.rollback()
        flash(str(trouble), "error")
        return to_product(item, "stock")
    if move is None:
        flash(f"The count matches: {counted:,} pieces of {item.name}.")
    else:
        flash(f"{item.name} is now {counted:,} pieces ({move.change:+,} from the count).")
    return to_product(item, "stock")


@bp.get("/stock")
@needs(SEE_PRODUCTS)
def stock_history():
    s = db.session()
    reason = request.args.get("reason") if request.args.get("reason") in MOVE_REASONS else None
    return render_template("manage/stock.html", moves=stock.history(s, g.store, reason=reason), reason=reason,
                           reasons=MOVE_REASONS)


# ---------- Adding many products from a CSV file ----------

@bp.route("/products/import", methods=["GET", "POST"])
@needs(IMPORT_PRODUCTS, "Only the owner can import products.")
def import_products():
    s = db.session()
    if request.method == "GET":
        return render_template("manage/import.html", plan=None, errors=[], text="")
    upload = request.files.get("file")
    try:
        text = importer.decode(upload.read()) if upload and upload.filename else request.form.get("text", "")
        if len(text) > importer.MAX_BYTES:
            raise importer.ImportProblem("That file is too big. Split it into files under 500 KB.")
        plan, errors = importer.read(s, g.store, text)
    except importer.ImportProblem as problem:
        flash(str(problem), "error")
        return redirect(url_for("manage.import_products"))
    if request.form.get("confirm") == "1":
        if errors:
            flash("Fix the rows marked in red first, then upload the file again.", "error")
        else:
            not_for_demo("The demo can't import files. Everything else on this page works.")
            made, updated = importer.apply(s, g.store, plan, g.user, utcnow())
            s.commit()
            flash(f"Imported: {made} new products and {updated} updated.")
            return redirect(url_for("manage.products"))
    return render_template("manage/import.html", plan=plan, errors=errors, text=text), 400 if errors else 200


@bp.get("/products/import/template.csv")
@needs(IMPORT_PRODUCTS)
def import_template():
    return importer.TEMPLATE, 200, {"Content-Type": "text/csv; charset=utf-8",
                                    "Content-Disposition": "attachment; filename=dfjc-products.csv"}


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
