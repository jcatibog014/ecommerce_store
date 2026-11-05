"""The shop: the home page, the catalog with search and filters, product pages, and photos.

Anyone may browse (the real store's products). Ordering needs an account
(from stage 2). A demo visitor sees their own demo store.
"""
from flask import Blueprint, abort, g, make_response, render_template, request, url_for
from sqlalchemy.orm import undefer

from .. import catalog, db
from ..models import Photo

bp = Blueprint("shop", __name__)


@bp.get("/")
def home():
    if g.store is None:
        return render_template("not_ready.html"), 503
    s = db.session()
    categories = catalog.categories(s, g.store)
    counts = catalog.category_counts(s, g.store)
    from .. import vouchers
    from ..dates import local_today
    return render_template("shop/home.html", categories=[c for c in categories if counts.get(c.id)],
                           counts=counts, newest=catalog.newest(s, g.store),
                           promos=vouchers.home_promos(s, g.store, local_today()))


@bp.get("/products")
def products():
    if g.store is None:
        abort(404)
    s = db.session()
    categories = catalog.categories(s, g.store)
    filters = catalog.parse_shop_filters(request.args, categories)
    found, total = catalog.shop_products(s, g.store, filters)
    pages = max(1, -(-total // catalog.PAGE_SIZE))
    chosen = next((c for c in categories if c.id == filters["category"]), None)
    counts = catalog.category_counts(s, g.store)
    return render_template("shop/products.html", products=found, total=total, filters=filters, pages=pages,
                           categories=[c for c in categories if counts.get(c.id)], counts=counts,
                           chosen=chosen, sorts=catalog.SORTS, to_args=catalog.to_args)


@bp.get("/products/<int:product_id>")
def product(product_id):
    if g.store is None:
        abort(404)
    item = catalog.shop_product(db.session(), g.store, product_id)
    if item is None:
        abort(404)
    from ..permissions import SHOP, can
    from .extras import favorite_ids
    s = db.session()
    saved = g.user is not None and can(SHOP) and item.id in favorite_ids(s, g.user)
    # The other brands and kinds with the same title: the shopper picks one, which opens its page.
    variants = catalog.siblings(s, g.store, item)
    brands, kinds = catalog.choices(item, variants)
    return render_template("shop/product.html", product=item, saved=saved, variants=variants, brands=brands,
                           kinds=kinds)


def photo_response(photo_id, field):
    photo = db.session().get(Photo, photo_id, options=[undefer(getattr(Photo, field))])
    if photo is None:
        abort(404)
    response = make_response(getattr(photo, field))
    response.headers["Content-Type"] = "image/webp"
    # A photo never changes: a new upload is a new photo with a new address. So browsers may keep it.
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@bp.get("/photos/<int:photo_id>.webp")
def photo(photo_id):
    return photo_response(photo_id, "data")


@bp.get("/photos/<int:photo_id>/small.webp")
def photo_small(photo_id):
    return photo_response(photo_id, "thumb")


def photo_url(product, small=True):
    if not product.photo_id:
        return None
    return url_for("shop.photo_small" if small else "shop.photo", photo_id=product.photo_id)


def init_app(app):
    # Globals, not a context processor: macros imported from _macros.html see globals only.
    app.jinja_env.globals.update(photo_url=photo_url, cheapest=catalog.cheapest, stock_word=catalog.stock_word)
