"""The catalog: what shoppers can find, and the staff forms that fill it.

Shoppers only see products that are switched on and have at least one unit
switched on. They always see RETAIL prices; the wholesale price is for the
cashier to give.

Brands and kinds: every brand + kind is its own product, with its own units,
prices, stock and photo. Products with the same title in the same category
are ONE card in the shop (a Group); on the product page the shopper picks the
brand, then the kind. A product with no brand or kind is a group of one.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

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


def compose_name(title, brand="", kind=""):
    """The product's full name, used in orders and receipts: "3-in-1 coffee sachet - Kape Juan Creamy White"."""
    label = " ".join(part for part in (brand, kind) if part)
    return f"{title} - {label}" if label else title


def group_key(product):
    return (product.category_id, product.group_title.casefold())


@dataclass
class Group:
    """One card in the shop: every brand and kind of one product."""
    title: str
    variants: list = field(default_factory=list)  # products, sorted by brand then kind

    @property
    def first(self):
        """The kind the card opens: the cheapest one that can be bought (else the first)."""
        buyable = [product for product in self.variants if stock_word(product)[0] != "out"]
        return min(buyable or self.variants, key=lambda product: (cheapest(product).retail_price, product.id))

    @property
    def category(self):
        return self.first.category

    @property
    def category_id(self):
        return self.first.category_id

    @property
    def cheapest_unit(self):
        return min((cheapest(product) for product in self.variants), key=lambda unit: (unit.retail_price, unit.pieces_per_unit))

    @property
    def photo_product(self):
        return next((product for product in self.variants if product.photo_id), None)

    @property
    def brands(self):
        return sorted({product.brand for product in self.variants if product.brand}, key=str.casefold)

    @property
    def newest(self):
        return max((product.created_at for product in self.variants), default=datetime.min)

    @property
    def stock(self):
        """One kind: its own stock word. Several: in stock if any kind can be bought."""
        if len(self.variants) == 1:
            return stock_word(self.variants[0])
        if any(stock_word(product)[0] != "out" for product in self.variants):
            return "in", "In stock"
        return "out", "Out of stock"

    @property
    def size_count(self):
        """How many more sizes the card's cheapest kind has (Pack, Case...)."""
        return len(self.first.active_units) - 1


def sort_variants(products):
    return sorted(products, key=lambda product: (product.brand.casefold(), product.kind.casefold(), product.id))


def make_groups(products):
    found = defaultdict(list)
    for product in products:
        found[group_key(product)].append(product)
    return [Group(title=items[0].group_title, variants=sort_variants(items)) for items in found.values()]


def sort_groups(groups, sort):
    keys = {
        "name": lambda group: (group.title.casefold(),),
        "price-low": lambda group: (group.cheapest_unit.retail_price, group.title.casefold()),
        "price-high": lambda group: (-group.cheapest_unit.retail_price, group.title.casefold()),
        "new": lambda group: (-group.newest.timestamp(), group.title.casefold()),
    }
    return sorted(groups, key=keys[sort])


def shop_products(s, store, filters):
    """(groups on this page, how many groups match in all).

    Grouping happens here in Python, not in SQL: a store has hundreds of products, not millions,
    and one card per title is much easier to read this way."""
    where = list(shop_filter(store)) + search_condition(filters["q"])
    if filters["category"]:
        where.append(Product.category_id == filters["category"])
    products = s.scalars(select(Product).where(*where)
                         .options(selectinload(Product.units), selectinload(Product.category))).all()
    groups = sort_groups(make_groups(products), filters["sort"])
    start = (filters["page"] - 1) * PAGE_SIZE
    return groups[start:start + PAGE_SIZE], len(groups)


def siblings(s, store, product):
    """Every kind a shopper can buy in this product's group (itself included), sorted by brand then kind."""
    same_category = (Product.category_id.is_(None) if product.category_id is None
                     else Product.category_id == product.category_id)
    found = s.scalars(select(Product).where(*shop_filter(store), same_category)
                      .options(selectinload(Product.units))).all()
    key = product.group_title.casefold()
    return sort_variants([item for item in found if item.group_title.casefold() == key])


def choices(product, variants):
    """The Brand and Kind pickers for a product page:
    ([(brand, product to open, chosen?)], [(kind, product, chosen?, can be bought?)])."""
    brands = []
    for brand in sorted({item.brand for item in variants}, key=str.casefold):
        same = [item for item in variants if item.brand == brand]
        # The same kind in the other brand if it has one, else that brand's first kind.
        target = next((item for item in same if item.kind == product.kind), same[0])
        brands.append((brand, target, brand == product.brand))
    kinds = [(item.kind, item, item.id == product.id, stock_word(item)[0] != "out")
             for item in variants if item.brand == product.brand]
    return brands, kinds


def wrap(products):
    """Products as groups of one (Favorites shows the exact kinds that were saved)."""
    return [Group(title=product.name, variants=[product]) for product in products]


def categories(s, store):
    return s.scalars(select(Category).where(Category.store_id == store.id)
                     .order_by(Category.sort_order, func.lower(Category.name))).all()


def category_counts(s, store):
    """{category id: cards (groups) shoppers can see in it}."""
    titles = func.lower(func.coalesce(func.nullif(Product.title, ""), Product.name))
    rows = s.execute(select(Product.category_id, func.count(titles.distinct())).where(*shop_filter(store))
                     .group_by(Product.category_id)).all()
    return dict(rows)


def newest(s, store, limit=8):
    groups, _ = shop_products(s, store, {"q": "", "category": None, "sort": "new", "page": 1})
    return groups[:limit]


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
    values["title"], errors["title"] = forms.text(form, "title", 80, required=True, label="The product's name")
    values["brand"], errors["brand"] = forms.text(form, "brand", 40)
    values["kind"], errors["kind"] = forms.text(form, "kind", 40)
    values["name"] = compose_name(values["title"], values["brand"], values["kind"])
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
