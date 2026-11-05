"""Adding many products at once from a CSV file (Excel: File > Save As > CSV UTF-8).

One row per unit. Rows with the same product name are one product:

    name,category,unit,pieces,retail,wholesale,stock,barcode,description
    Soy sauce 1 L,Condiments,Bottle,1,48,45,48,,For adobo
    Soy sauce 1 L,Condiments,Case of 12,12,560,530,,,

read() checks every row and builds a plan; nothing is saved. The owner sees
a preview, then apply() saves the whole plan in ONE transaction: every row,
or (if anything goes wrong) none of them. The file is read again on confirm,
never taken from the preview.

New products get the stock from their first row. Existing products keep
their stock (use Restock or Count for that); their units' prices are updated.
"""
import csv
import io

from sqlalchemy import func, select

from . import stock
from .models import MOVE_IMPORT, Category, Product, ProductUnit
from .money import parse_peso

MAX_BYTES = 500_000
MAX_ROWS = 2000
COLUMNS = ("name", "category", "unit", "pieces", "retail", "wholesale", "stock", "barcode", "description")
REQUIRED = ("name", "unit", "pieces", "retail")
TEMPLATE = ("name,category,unit,pieces,retail,wholesale,stock,barcode,description\n"
            "Soy sauce 1 L,Condiments,Bottle,1,48,45,48,,For adobo\n"
            "Soy sauce 1 L,Condiments,Case of 12,12,560,530,,,\n")


class ImportProblem(ValueError):
    pass


def decode(data):
    if len(data) > MAX_BYTES:
        raise ImportProblem("That file is too big. Split it into files under 500 KB.")
    for encoding in ("utf-8-sig", "cp1252"):  # Excel saves one or the other
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportProblem("We couldn't read that file. Save it as CSV UTF-8 and try again.")


def whole(text):
    text = (text or "").replace(",", "").strip()
    return int(text) if text.isdigit() else None


def read(s, store, text):
    """Returns (plan, errors). plan: [{name, category, description, stock, existing, units: [...]}]."""
    reader = csv.DictReader(io.StringIO(text))
    headers = [(name or "").strip().lower() for name in (reader.fieldnames or [])]
    missing = [name for name in REQUIRED if name not in headers]
    if missing:
        raise ImportProblem("The first row must name the columns. Missing: " + ", ".join(missing) + ".")
    reader.fieldnames = headers
    errors, products = [], {}
    existing = {product.name.lower(): product for product in s.scalars(
        select(Product).where(Product.store_id == store.id))}
    for number, row in enumerate(reader, start=2):
        if number - 1 > MAX_ROWS:
            raise ImportProblem(f"Use at most {MAX_ROWS:,} rows per file.")
        row = {key: (value or "").strip() for key, value in row.items() if key}
        if not any(row.values()):
            continue
        problems = []
        name, unit = row.get("name", ""), row.get("unit", "")
        if not name or len(name) > 80:
            problems.append("a name (up to 80 letters) is needed")
        if not unit or len(unit) > 40:
            problems.append("a unit (up to 40 letters) is needed")
        pieces = whole(row.get("pieces"))
        if pieces is None or not 1 <= pieces <= 10_000:
            problems.append("pieces must be a whole number from 1 to 10,000")
        retail = parse_peso(row.get("retail"))
        if retail is None:
            problems.append("retail must be a price like 48 or 48.50")
        wholesale = parse_peso(row.get("wholesale")) if row.get("wholesale") else retail
        if row.get("wholesale") and wholesale is None:
            problems.append("wholesale must be a price like 45 or 45.50")
        elif retail is not None and wholesale is not None and wholesale > retail:
            problems.append("wholesale can't be more than retail")
        start = whole(row.get("stock")) if row.get("stock") else 0
        if start is None or start > stock.MAX_PIECES:
            problems.append("stock must be a whole number of pieces")
        if problems:
            errors.append((number, name or "(no name)", "; ".join(problems).capitalize() + "."))
            continue
        key = name.lower()
        product = products.setdefault(key, {
            "name": name, "category": row.get("category", "")[:60], "description": row.get("description", "")[:1000],
            "stock": start, "existing": existing.get(key), "units": [], "unit_names": set()})
        if unit.lower() in product["unit_names"]:
            errors.append((number, name, f'"{unit}" appears twice for this product.'))
            continue
        product["unit_names"].add(unit.lower())
        product["units"].append({"name": unit, "pieces_per_unit": pieces, "retail_price": retail,
                                 "wholesale_price": wholesale, "barcode": row.get("barcode", "")[:40]})
    if not products and not errors:
        raise ImportProblem("The file has no products in it.")
    return list(products.values()), errors


def apply(s, store, plan, by, moment):
    """Save the plan. The caller commits (or rolls back everything)."""
    categories = {category.name.lower(): category for category in s.scalars(
        select(Category).where(Category.store_id == store.id))}
    made = updated = 0
    for item in plan:
        category = None
        if item["category"]:
            category = categories.get(item["category"].lower())
            if category is None:
                order = s.scalar(select(func.coalesce(func.max(Category.sort_order), -1) + 1)
                                 .where(Category.store_id == store.id))
                category = Category(store_id=store.id, name=item["category"], sort_order=order)
                s.add(category)
                s.flush()
                categories[item["category"].lower()] = category
        product = item["existing"]
        if product is None:
            product = Product(store_id=store.id, name=item["name"], description=item["description"],
                              category_id=category.id if category else None, stock_pieces=item["stock"],
                              created_at=moment, updated_at=moment)
            s.add(product)
            s.flush()
            stock.starting_stock(s, product, by, moment, MOVE_IMPORT, "From a CSV file")
            made += 1
        else:
            updated += 1
        units = {unit.name.lower(): unit for unit in s.scalars(
            select(ProductUnit).where(ProductUnit.product_id == product.id))}
        for values in item["units"]:
            unit = units.get(values["name"].lower())
            if unit is None:
                s.add(ProductUnit(product_id=product.id, **values))
            else:
                for name, value in values.items():
                    setattr(unit, name, value)
    s.flush()
    return made, updated
