"""The catalog: what shoppers can find, and the staff forms that fill it.

Shoppers only see products that are switched on and have at least one unit
switched on. They always see RETAIL prices; the wholesale price is for the
cashier to give (from stage 2).
"""
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import selectinload

from . import forms
from .models import CATEGORY_ICONS, Category, Product, ProductUnit

PAGE_SIZE = 24
SORTS = {
    "name": "Name, A to Z",
    "price-low": "Price, low to high",
    "price-high": "Price, high to low",
    "new": "Newest",
}
MAX_STOCK = 1_000_000
MAX_PIECES_PER_UNIT = 10_000


# ---------- What shoppers see ----------

def has_active_unit():
    return exists().where(ProductUnit.product_id == Product.id, ProductUnit.is_active.is_(True))


def cheapest_unit_price():
    """The lowest retail price among a product's units (usually the single piece)."""
    return (select(func.min(ProductUnit.retail_price))
            .where(ProductUnit.product_id == Product.id, ProductUnit.is_active.is_(True))
            .correlate(Product).scalar_subquery())


def shop_filter(store):
    return (Product.store_id == store.id, Product.is_active.is_(True), has_active_unit())


def parse_shop_filters(args, categories):
    """From the address (?q=coffee&category=3&sort=price-low&page=2). Bad values are dropped, never errors."""
    q = (args.get("q") or "").strip()[:80]
    category_ids = {category.id for category in categories}
    category = args.get("category", "")
    category = int(category) if category.isdigit() and int(category) in category_ids else None
    sort = args.get("sort") if args.get("sort") in SORTS else "name"
    page = args.get("page", "1")
    page = max(1, min(int(page), 1000)) if page.isdigit() else 1
    return {"q": q, "category": category, "sort": sort, "page": page}


def to_args(filters, **changes):
    """The filters for an address again, leaving out the defaults (for links and pages)."""
    values = {**filters, **changes}
    args = {}
    if values.get("q"):
        args["q"] = values["q"]
    if values.get("category"):
        args["category"] = values["category"]
    if values.get("sort", "name") != "name":
        args["sort"] = values["sort"]
    if values.get("page", 1) != 1:
        args["page"] = values["page"]
    return args


def search_condition(q):
    """Every word must appear in the name or the description. % and _ are plain letters."""
    return [or_(Product.name.icontains(word, autoescape=True), Product.description.icontains(word, autoescape=True))
            for word in q.split()[:6]]


def shop_products(s, store, filters):
    """(products on this page, how many match in all)."""
    where = list(shop_filter(store)) + search_condition(filters["q"])
    if filters["category"]:
        where.append(Product.category_id == filters["category"])
    total = s.scalar(select(func.count()).select_from(Product).where(*where))
    order = {
        "name": (func.lower(Product.name), Product.id),
        "price-low": (cheapest_unit_price(), func.lower(Product.name)),
        "price-high": (cheapest_unit_price().desc(), func.lower(Product.name)),
        "new": (Product.created_at.desc(), Product.id.desc()),
    }[filters["sort"]]
    products = s.scalars(
        select(Product).where(*where).order_by(*order)
        .offset((filters["page"] - 1) * PAGE_SIZE).limit(PAGE_SIZE)
        .options(selectinload(Product.units), selectinload(Product.category))).all()
    return products, total


def categories(s, store):
    return s.scalars(select(Category).where(Category.store_id == store.id)
                     .order_by(Category.sort_order, func.lower(Category.name))).all()


def category_counts(s, store):
    """{category id: products shoppers can see in it}."""
    rows = s.execute(select(Product.category_id, func.count()).where(*shop_filter(store))
                     .group_by(Product.category_id)).all()
    return dict(rows)


def newest(s, store, limit=8):
    return s.scalars(select(Product).where(*shop_filter(store)).order_by(Product.created_at.desc(), Product.id.desc())
                     .limit(limit).options(selectinload(Product.units), selectinload(Product.category))).all()


def shop_product(s, store, product_id):
    """A product a shopper may open, or None."""
    return s.scalar(select(Product).where(Product.id == product_id, *shop_filter(store))
                    .options(selectinload(Product.units), selectinload(Product.category)))


def cheapest(product):
    """The unit shown on a product card: the lowest retail price."""
    units = product.active_units
    return min(units, key=lambda unit: (unit.retail_price, unit.pieces_per_unit)) if units else None


def stock_word(product):
    """In stock / Only 6 left / Out of stock, counted in the smallest unit."""
    units = product.active_units
    if not units:
        return "out", "Out of stock"
    smallest = min(unit.pieces_per_unit for unit in units)
    left = product.available_pieces // smallest
    if left == 0:
        return "out", "Out of stock"
    if product.is_low:
        return "low", f"Only {left:,} left"
    return "in", "In stock"


# ---------- What staff see ----------

STAFF_SHOWS = {"all": "All products", "low": "Low stock", "off": "Switched off"}


def staff_products(s, store, q="", category=None, show="all"):
    where = [Product.store_id == store.id] + search_condition(q)
    if category:
        where.append(Product.category_id == category)
    if show == "off":
        where.append(Product.is_active.is_(False))
    if show == "low":
        where += [Product.low_stock_pieces > 0,
                  Product.stock_pieces - Product.reserved_pieces <= Product.low_stock_pieces]
    return s.scalars(select(Product).where(*where).order_by(func.lower(Product.name), Product.id)
                     .options(selectinload(Product.units), selectinload(Product.category))).all()


def read_product_form(form, store_categories, with_stock):
    """Returns (values, errors) for the product form."""
    errors = {}
    values = {}
    values["name"], errors["name"] = forms.text(form, "name", 80, required=True, label="A name")
    values["description"], errors["description"] = forms.text(form, "description", 1000)
    raw_category = form.get("category_id", "")
    ids = {category.id for category in store_categories}
    values["category_id"] = int(raw_category) if raw_category.isdigit() and int(raw_category) in ids else None
    if raw_category and values["category_id"] is None:
        errors["category_id"] = "Choose a category from the list."
    values["low_stock_pieces"], errors["low_stock_pieces"] = forms.whole_number(
        form, "low_stock_pieces", 0, MAX_STOCK, default=0)
    values["is_active"] = forms.checkbox(form, "is_active")
    if with_stock:
        values["stock_pieces"], errors["stock_pieces"] = forms.whole_number(
            form, "stock_pieces", 0, MAX_STOCK, default=0)
    return values, {key: value for key, value in errors.items() if value}


def read_unit_form(form, prefix=""):
    """Returns (values, errors) for one unit: "Case of 24", 24 pieces, ₱440 retail, ₱420 wholesale."""
    errors = {}
    values = {}
    values["name"], errors["name"] = forms.text(form, prefix + "name", 40, required=True, label="A unit name")
    values["pieces_per_unit"], errors["pieces_per_unit"] = forms.whole_number(
        form, prefix + "pieces_per_unit", 1, MAX_PIECES_PER_UNIT)
    values["retail_price"], errors["retail_price"] = forms.price(form, prefix + "retail_price")
    wholesale, errors["wholesale_price"] = forms.price(form, prefix + "wholesale_price", required=False)
    # No wholesale price typed: it is the same as retail until the owner sets one.
    values["wholesale_price"] = wholesale if wholesale is not None else values["retail_price"]
    if values["retail_price"] is not None and wholesale is not None and wholesale > values["retail_price"]:
        errors["wholesale_price"] = "The wholesale price can't be more than the retail price."
    values["barcode"], errors["barcode"] = forms.text(form, prefix + "barcode", 40)
    values["is_active"] = forms.checkbox(form, prefix + "is_active") if (prefix + "is_active_sent") in form else True
    return values, {key: value for key, value in errors.items() if value}


def read_category_form(form):
    errors = {}
    values = {}
    values["name"], errors["name"] = forms.text(form, "name", 60, required=True, label="A name")
    values["icon"] = form.get("icon") if form.get("icon") in CATEGORY_ICONS else "box"
    values["sort_order"], errors["sort_order"] = forms.whole_number(form, "sort_order", 0, 999, default=0)
    return values, {key: value for key, value in errors.items() if value}
