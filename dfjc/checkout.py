"""The cart and checkout: turning a cart into an order, in ONE transaction.

place_order() does all of this, or none of it:
  1. read the cart again (prices and stock may have changed since the page was shown);
  2. HOLD the pieces for every product with a guarded UPDATE:
         UPDATE products SET reserved_pieces = reserved_pieces + 24
         WHERE id = 7 AND stock_pieces - reserved_pieces >= 24
     Two shoppers buying the last case at the same moment can't both win: the
     database runs the two UPDATEs one after the other, and the second finds
     no row any more (OutOfStock). On PostgreSQL the product rows are also
     locked first (SELECT ... FOR UPDATE), always in the same order (by id),
     so two orders never wait for each other forever;
  3. make the order and its lines, with copies of the names and BOTH prices;
  4. empty the cart.
The caller commits. If anything raises, the caller rolls back and nothing
is held, ordered or emptied.

The order's idempotency key (a hidden field on the checkout page) makes a
double-tap safe: the second "Place order" finds the first order.
"""
import secrets
from collections import defaultdict

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import selectinload

from . import forms
from .models import (
    CASH_PICKUP, COD, DELIVERY, ONLINE, PICKUP, PLACED, RETAIL, Address, CartItem, DeliveryArea, Order, OrderEvent, OrderLine,
    Product, ProductUnit,
)

MAX_QUANTITY = 999
REF_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I/L: easy to read out on the phone
NOTE_MAX = 300


class CheckoutProblem(ValueError):
    pass


class OutOfStock(CheckoutProblem):
    def __init__(self, name, available_pieces):
        self.name = name
        self.available_pieces = available_pieces
        super().__init__(f"Sorry, {name} just ran low: only {available_pieces:,} "
                         f"{'piece is' if available_pieces == 1 else 'pieces are'} left. "
                         "Your cart is unchanged, so lower the amount and try again.")


# ---------- The cart ----------

def cart_items(s, user):
    """The user's cart, with each unit and product loaded, oldest first."""
    return s.scalars(select(CartItem).where(CartItem.user_id == user.id).order_by(CartItem.added_at, CartItem.id)
                     .options(selectinload(CartItem.unit).selectinload(ProductUnit.product)
                              .selectinload(Product.category))).all()


def cart_count(s, user):
    return s.scalar(select(func.coalesce(func.sum(CartItem.quantity), 0)).where(CartItem.user_id == user.id))


def add_to_cart(s, user, unit, quantity, moment):
    """Add quantity of unit, on top of what is already in the cart (at most MAX_QUANTITY)."""
    item = s.scalar(select(CartItem).where(CartItem.user_id == user.id, CartItem.unit_id == unit.id))
    if item is None:
        item = CartItem(user_id=user.id, unit_id=unit.id, quantity=0, added_at=moment)
        s.add(item)
    item.quantity = min(MAX_QUANTITY, item.quantity + quantity)
    return item


def item_problem(item):
    """Why this cart line can't be ordered as it is, or None."""
    unit = item.unit
    if not unit.is_active or not unit.product.is_active:
        return "Not sold any more. Please remove it."
    if unit.product.available_pieces < item.pieces:
        left = unit.product.available_pieces // unit.pieces_per_unit
        return f"Only {left:,} left." if left else "Out of stock right now."
    return None


def cart_summary(items):
    problems = {item.id: item_problem(item) for item in items}
    # Two lines of the same product (bottles and a case) share its stock.
    by_product = defaultdict(int)
    for item in items:
        by_product[item.unit.product_id] += item.pieces
    for item in items:
        product = item.unit.product
        if not problems[item.id] and by_product[product.id] > product.available_pieces:
            problems[item.id] = f"Together with your other {product.name} lines, that is more than we have."
    return {"subtotal": sum(item.line_total for item in items), "problems": problems,
            "can_check_out": bool(items) and not any(problems.values())}


# ---------- Reading the checkout form ----------

def read_checkout(form, user, areas, addresses, online_ok=False):
    """Returns (choice, errors). choice has fulfilment, payment_method, area, address (text),
    new_address (an Address to save, or None) and note. online_ok: this store takes online payment."""
    errors = {}
    choice = {"fulfilment": form.get("fulfilment"), "area": None, "address": "", "new_address": None}
    if choice["fulfilment"] not in (PICKUP, DELIVERY):
        errors["fulfilment"] = "Choose pickup or delivery."
        choice["fulfilment"] = PICKUP
    pay = form.get("payment", "cash")
    if pay == "online" and not online_ok:
        errors["payment"] = "Online payment is off for now. Please choose cash."
    choice["payment_method"] = ONLINE if pay == "online" and online_ok else (
        CASH_PICKUP if choice["fulfilment"] == PICKUP else COD)
    choice["note"], errors["note"] = forms.text(form, "note", NOTE_MAX)
    if choice["fulfilment"] == DELIVERY:
        raw = form.get("address_id", "new")
        saved = {str(address.id): address for address in addresses}
        if raw in saved:
            address = saved[raw]
            if address.area is None or not address.area.is_active:
                errors["address_id"] = "We don't deliver to that address's area any more. Choose another."
            else:
                choice["area"], choice["address"] = address.area, address.one_line
        else:
            active = {str(area.id): area for area in areas if area.is_active}
            area = active.get(form.get("area_id", ""))
            if area is None:
                errors["area_id"] = "Choose your barangay from the list."
            street, errors["street"] = forms.text(form, "street", 200, required=True, label="Your street address")
            landmark, errors["landmark"] = forms.text(form, "landmark", 200)
            label, errors["label"] = forms.text(form, "label", 40)
            if area is not None and street and not errors["street"]:
                new = Address(user_id=user.id, area_id=area.id, street=street, landmark=landmark, label=label or "Home")
                new.area = area
                choice["area"], choice["address"] = area, new.one_line
                if forms.checkbox(form, "save_address"):
                    choice["new_address"] = new
    return choice, {key: value for key, value in errors.items() if value}


# ---------- Placing the order ----------

def new_ref(s):
    while True:
        ref = "DF-" + "".join(secrets.choice(REF_ALPHABET) for _ in range(6))
        if s.scalar(select(Order.id).where(Order.ref == ref)) is None:
            return ref


def find_by_key(s, user, key):
    return s.scalar(select(Order).where(Order.user_id == user.id, Order.idempotency_key == key))


def hold_stock(s, store, pieces_by_product, names):
    """Reserve the pieces for every product, or raise OutOfStock (the caller rolls back)."""
    ids = sorted(pieces_by_product)
    # PostgreSQL: lock the rows first, always lowest id first. SQLite ignores FOR UPDATE: it
    # lets only one writer in at a time anyway.
    s.execute(select(Product.id).where(Product.id.in_(ids)).order_by(Product.id).with_for_update())
    for product_id in ids:
        pieces = pieces_by_product[product_id]
        held = s.execute(
            update(Product)
            .where(Product.id == product_id, Product.store_id == store.id, Product.is_active.is_(True),
                   Product.stock_pieces - Product.reserved_pieces >= pieces)
            .values(reserved_pieces=Product.reserved_pieces + pieces)
            .execution_options(synchronize_session=False)).rowcount
        if held != 1:
            available = s.scalar(select(Product.stock_pieces - Product.reserved_pieces).where(Product.id == product_id))
            raise OutOfStock(names[product_id], max(0, available or 0))


def place_order(s, user, store, choice, key, moment):
    """Turn the cart into an order. Returns (order, made_now). made_now is False when this
    key already made an order (a double-tap). The caller commits, or rolls back on any error."""
    if not key or len(key) > 64:
        raise CheckoutProblem("This checkout page is too old. Please open it again.")
    existing = find_by_key(s, user, key)
    if existing is not None:
        return existing, False
    items = cart_items(s, user)
    if not items:
        raise CheckoutProblem("Your cart is empty.")
    pieces, names = defaultdict(int), {}
    for item in items:
        unit = item.unit
        if unit.product.store_id != store.id or not unit.is_active or not unit.product.is_active:
            raise CheckoutProblem(f"{unit.product.name} ({unit.name}) isn't sold any more. Remove it from your cart.")
        pieces[unit.product_id] += item.pieces
        names[unit.product_id] = unit.product.name
    hold_stock(s, store, pieces, names)

    area = choice.get("area")
    order = Order(
        store_id=store.id, user_id=user.id, ref=new_ref(s), status=PLACED, fulfilment=choice["fulfilment"],
        payment_method=choice["payment_method"], price_type=RETAIL, customer_name=user.name,
        customer_mobile=user.mobile, area_name=area.name if area else "", address=choice.get("address", ""),
        note=choice.get("note", ""), delivery_fee=area.fee if area and choice["fulfilment"] == DELIVERY else 0,
        idempotency_key=key, placed_at=moment, updated_at=moment)
    for item in items:
        unit = item.unit
        # Copies of the name and BOTH prices: the cashier chooses later, from these.
        order.lines.append(OrderLine(
            product_id=unit.product_id, unit_id=unit.id, product_name=unit.product.name, unit_name=unit.name,
            pieces_per_unit=unit.pieces_per_unit, quantity=item.quantity, pieces=item.pieces,
            retail_price=unit.retail_price, wholesale_price=unit.wholesale_price, price_type=RETAIL))
    order.subtotal = sum(line.line_total for line in order.lines)
    order.total = order.subtotal + order.delivery_fee
    s.add(order)
    if choice.get("new_address") is not None:
        s.add(choice["new_address"])
    s.flush()  # a second request with the same key fails here (UNIQUE), before anything is kept
    s.add(OrderEvent(order_id=order.id, from_status=None, to_status=PLACED, by_id=user.id, created_at=moment,
                     note=choice.get("note", "")))
    s.execute(delete(CartItem).where(CartItem.user_id == user.id))
    return order, True


def new_checkout_key():
    return secrets.token_urlsafe(24)


def active_areas(s, store):
    return s.scalars(select(DeliveryArea).where(DeliveryArea.store_id == store.id, DeliveryArea.is_active.is_(True))
                     .order_by(DeliveryArea.sort_order, DeliveryArea.name)).all()


def saved_addresses(s, user):
    return s.scalars(select(Address).where(Address.user_id == user.id).order_by(Address.created_at.desc())
                     .options(selectinload(Address.area))).all()

