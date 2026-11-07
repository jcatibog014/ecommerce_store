"""The data model.

    stores ─┬─< users (owner, cashier, rider, shopper)
            ├─< invites (a link that makes a cashier or rider)
            ├─< categories
            ├─< photos
            ├─< products ──< product_units (piece, pack, case: each with a retail AND a wholesale price)
            ├─< delivery_areas (barangay -> delivery fee)
            └─< orders ─┬─< order_lines (a copy of the name and BOTH prices when ordered)
                        ├─< order_events (the timeline: every status change, who and when)
                        └─< payments ──< refunds
    payment_events (every webhook PayMongo sent us, each event id once)
    stores ─┬─< stock_moves (every change to a product's stock: why, how many, who)
            ├─< remittances (cash a rider hands to the cashier) ──< payments (cash on delivery)
            ├─< cash_shifts (a cashier's drawer) ──< walkin_sales (the counter) ──< walkin_lines
            ├─< vouchers (promo codes) ──< voucher_uses (one per shopper)
            └─< notifications (the email outbox)
    users ──< favorites ──> products
    users ──< cart_requests ──> products ("Not in the list? Ask for it")

    users ─┬─< cart_items ──> product_units
           ├─< addresses ──> delivery_areas
           └─< orders

Everything belongs to one store. The real store is DFJC; every "Try Demo"
makes its own small demo store, so a visitor can never touch the real
products. Deleting a store deletes everything in it (ON DELETE CASCADE).

Stock is counted in PIECES. A unit says how many pieces it takes: selling one
"Case of 24" takes 24 pieces. Money is whole centavos (money.py).
Moments are naive UTC (dates.utcnow()).
"""
from datetime import date, datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, LargeBinary, String, Text, UniqueConstraint, false, text, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .dates import utcnow
from .db import Base

# ---------- Roles (permissions.py says who may do what) ----------

OWNER = "owner"
CASHIER = "cashier"
RIDER = "rider"
SHOPPER = "shopper"
ROLES = (OWNER, CASHIER, RIDER, SHOPPER)
STAFF_ROLES = (OWNER, CASHIER, RIDER)
ROLE_LABELS = {OWNER: "Owner", CASHIER: "Cashier", RIDER: "Rider", SHOPPER: "Shopper"}

# ---------- Orders (orders.py says which moves between statuses are allowed) ----------

PLACED = "placed"                      # the shopper ordered; the cashier hasn't priced it yet
AWAITING_PAYMENT = "awaiting_payment"  # priced; waiting for an online payment (stage 3)
CONFIRMED = "confirmed"                # priced; paid in cash at pickup or on delivery
PAID = "paid"                          # paid online (stage 3)
PACKING = "packing"
READY = "ready"                        # ready for pickup, or ready to go out
OUT_FOR_DELIVERY = "out_for_delivery"
PICKED_UP = "picked_up"
DELIVERED = "delivered"
CANCELLED = "cancelled"
REFUNDED = "refunded"                  # paid online, then cancelled (stage 3)
ORDER_STATUSES = (PLACED, AWAITING_PAYMENT, CONFIRMED, PAID, PACKING, READY, OUT_FOR_DELIVERY, PICKED_UP,
                  DELIVERED, CANCELLED, REFUNDED)
OPEN_STATUSES = (PLACED, AWAITING_PAYMENT, CONFIRMED, PAID, PACKING, READY, OUT_FOR_DELIVERY)
DONE_STATUSES = (PICKED_UP, DELIVERED, CANCELLED, REFUNDED)

PICKUP = "pickup"
DELIVERY = "delivery"
FULFILMENTS = {PICKUP: "Pick up at the store", DELIVERY: "Delivery"}

CASH_PICKUP = "cash_pickup"
COD = "cod"
ONLINE = "online"
PAYMENT_METHODS = {CASH_PICKUP: "Cash at pickup", COD: "Cash on delivery", ONLINE: "Online (GCash, Maya or card)"}

# One payment attempt (payments.py). An order can have several: a session that expired, then one that was paid.
PAYMENT_PENDING = "pending"    # the shopper is on the payment page (or left it)
PAYMENT_PAID = "paid"
PAYMENT_FAILED = "failed"
PAYMENT_EXPIRED = "expired"
PAYMENT_REFUNDED = "refunded"
PAYMENT_STATUSES = (PAYMENT_PENDING, PAYMENT_PAID, PAYMENT_FAILED, PAYMENT_EXPIRED, PAYMENT_REFUNDED)
PAYMENT_STATUS_LABELS = {PAYMENT_PENDING: "Not finished", PAYMENT_PAID: "Paid", PAYMENT_FAILED: "Not paid",
                         PAYMENT_EXPIRED: "Expired", PAYMENT_REFUNDED: "Refunded"}
# What a status means, under the payment on the staff order page. The gateway doesn't tell us why a
# payment failed, so these say what can cause it, and that no money was taken.
PAYMENT_STATUS_NOTES = {
    PAYMENT_PENDING: "The shopper opened the payment page but hasn't finished paying yet.",
    PAYMENT_FAILED: "GCash, Maya or the card turned it down (like not enough balance), or the shopper cancelled. "
                    "No money was taken. They can try again or pay in cash.",
    PAYMENT_EXPIRED: "The shopper didn't finish paying in time. No money was taken.",
}
GATEWAY_PAYMONGO = "paymongo"
GATEWAY_SANDBOX = "sandbox"  # the built-in practice gateway (demo, tests, your own computer)
GATEWAY_CASH = "cash"

RETAIL = "retail"
WHOLESALE = "wholesale"
MIXED = "mixed"
PRICE_TYPES = {RETAIL: "Retail", WHOLESALE: "Wholesale", MIXED: "Retail and wholesale"}

# The small picture on a product without a photo, chosen per category (_icons.html draws them).
CATEGORY_ICONS = {
    "bottle": "Drinks", "snack": "Snacks", "can": "Canned goods", "noodles": "Noodles",
    "rice": "Rice and grains", "condiment": "Condiments", "care": "Personal care", "home": "Household",
    "frozen": "Frozen", "bread": "Bread and baking", "candy": "Sweets", "box": "Other",
}


def sql_list(values):
    return ", ".join(f"'{value}'" for value in values)


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    tagline: Mapped[str] = mapped_column(String(160), default="")
    address: Mapped[str] = mapped_column(String(300), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    hours: Mapped[str] = mapped_column(String(300), default="")  # "Mon to Sat, 6 AM to 9 PM"
    pickup_note: Mapped[str] = mapped_column(String(500), default="")
    is_demo: Mapped[bool] = mapped_column(default=False)
    # Every "Try Demo" makes its own store, deleted after this moment.
    demo_expires_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # One account per email in each store (the demo stores have their own made-up people).
        UniqueConstraint("store_id", "email"),
        CheckConstraint(f"role IN ({sql_list(ROLES)})", name="role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10), default=SHOPPER)
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(254))  # always lowercase (auth.clean_email)
    mobile: Mapped[str] = mapped_column(String(20), default="")  # 09171234567
    password_hash: Mapped[str] = mapped_column(String(255))
    failed_logins: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None]
    # A copy sits in the login cookie. Adding 1 here ends every session at once
    # (after a password change), and it also makes old reset links stop working.
    session_version: Mapped[int] = mapped_column(default=1)
    reset_sent_at: Mapped[datetime | None]  # the last reset email, so nobody can flood an inbox
    order_emails: Mapped[bool] = mapped_column(default=True, server_default=true())  # Settings
    is_active: Mapped[bool] = mapped_column(default=True)  # the owner can switch staff off
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    store: Mapped[Store] = relationship(lazy="joined")

    @property
    def is_demo(self):
        return self.store.is_demo

    @property
    def is_staff(self):
        return self.role != SHOPPER

    @property
    def first_name(self):
        return self.name.split()[0] if self.name.strip() else self.name


class Invite(Base):
    """A one-time link that makes a staff account (cashier or rider)."""
    __tablename__ = "invites"
    __table_args__ = (CheckConstraint(f"role IN ('{CASHIER}', '{RIDER}')", name="role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(80), default="")  # who it is for, so the owner remembers
    # Only a hash: someone who reads the database can't use the link.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    used_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    used_by: Mapped[User | None] = relationship(foreign_keys=[used_by_id])


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("store_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    icon: Mapped[str] = mapped_column(String(20), default="box")
    sort_order: Mapped[int] = mapped_column(default=0)


class Photo(Base):
    """A product photo, kept in the database because Render forgets files on every restart.
    photos.py turns every upload into two WebP files: a big one and a small one for lists."""
    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    # deferred: a product list never loads the picture bytes by accident.
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    thumb: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    width: Mapped[int]
    height: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        # The database itself refuses impossible stock, even if the code has a bug.
        CheckConstraint("stock_pieces >= 0", name="stock_not_negative"),
        CheckConstraint("reserved_pieces >= 0 AND reserved_pieces <= stock_pieces", name="reserved_in_stock"),
        CheckConstraint("low_stock_pieces >= 0", name="low_stock_not_negative"),
        Index("ix_products_store_name", "store_id", "name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), index=True)
    # Brands and kinds: every brand + kind is its own product (its own units, prices, stock
    # and photo). Products with the same title in the same category are one card in the shop,
    # where the shopper picks the brand, then the kind (catalog.py).
    #   title "3-in-1 coffee sachet", brand "Kape Juan", kind "Creamy White"
    # name is all three together ("3-in-1 coffee sachet - Kape Juan Creamy White"), made by
    # catalog.compose_name; orders, receipts and the stock history copy it.
    title: Mapped[str] = mapped_column(String(80), default="", server_default="")
    brand: Mapped[str] = mapped_column(String(40), default="", server_default="")
    kind: Mapped[str] = mapped_column(String(40), default="", server_default="")
    name: Mapped[str] = mapped_column(String(170))
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(default=True)  # hidden from shoppers when off
    stock_pieces: Mapped[int] = mapped_column(default=0)
    # Pieces held for orders that aren't packed yet (from stage 2). Shoppers can buy
    # stock_pieces - reserved_pieces.
    reserved_pieces: Mapped[int] = mapped_column(default=0)
    low_stock_pieces: Mapped[int] = mapped_column(default=0)  # warn at or below this (0 = never)
    photo_id: Mapped[int | None] = mapped_column(ForeignKey("photos.id", ondelete="SET NULL"))
    # Goes up by 1 on every staff save. A save made from an older copy of the
    # form is refused, so two staff can't overwrite each other's change.
    # SQLAlchemy checks it too: every ORM update says "... WHERE version = <the one I read>"
    # and adds 1, so a change made in between is never silently overwritten (StaleDataError).
    # Stock changes for orders use plain UPDATE statements instead, so they don't bump it.
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)

    __mapper_args__ = {"version_id_col": version}

    category: Mapped[Category | None] = relationship()
    units: Mapped[list["ProductUnit"]] = relationship(
        back_populates="product", order_by="[ProductUnit.pieces_per_unit, ProductUnit.id]",
        cascade="all, delete-orphan", passive_deletes=True)

    @property
    def available_pieces(self):
        return self.stock_pieces - self.reserved_pieces

    @property
    def group_title(self):
        """The shop card's title: the title, or the whole name for a product without brands or kinds."""
        return self.title or self.name

    @property
    def variant_label(self):
        """"Kape Juan Creamy White", or "" for a product with no brand or kind."""
        return " ".join(part for part in (self.brand, self.kind) if part)

    @property
    def active_units(self):
        return [unit for unit in self.units if unit.is_active]

    @property
    def is_low(self):
        return self.low_stock_pieces > 0 and self.available_pieces <= self.low_stock_pieces

    @property
    def icon(self):
        return self.category.icon if self.category else "box"


class ProductUnit(Base):
    """One way to buy a product: "Piece", "Pack of 6", "Case of 24".

    Each has a RETAIL and a WHOLESALE price. Shoppers always see retail; the
    cashier decides when an order gets the wholesale price (from stage 2)."""
    __tablename__ = "product_units"
    __table_args__ = (
        UniqueConstraint("product_id", "name"),
        CheckConstraint("pieces_per_unit >= 1", name="pieces_at_least_one"),
        CheckConstraint("retail_price >= 0 AND wholesale_price >= 0", name="prices_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    pieces_per_unit: Mapped[int] = mapped_column(default=1)
    retail_price: Mapped[int]  # centavos
    wholesale_price: Mapped[int]  # centavos
    barcode: Mapped[str] = mapped_column(String(40), default="")
    is_active: Mapped[bool] = mapped_column(default=True)

    product: Mapped[Product] = relationship(back_populates="units")

    @property
    def can_buy(self):
        """Enough pieces left for at least one of this unit."""
        return self.product.available_pieces >= self.pieces_per_unit

    @property
    def wholesale_saving(self):
        return self.retail_price - self.wholesale_price


# ---------- Delivery, the cart and addresses ----------

class DeliveryArea(Base):
    """A place the rider goes (a barangay) and what delivery there costs."""
    __tablename__ = "delivery_areas"
    __table_args__ = (UniqueConstraint("store_id", "name"), CheckConstraint("fee >= 0", name="fee_not_negative"))

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    fee: Mapped[int]  # centavos
    is_active: Mapped[bool] = mapped_column(default=True)
    sort_order: Mapped[int] = mapped_column(default=0)


class CartItem(Base):
    """One line in a shopper's cart. Nothing is held in stock until the order is placed."""
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("user_id", "unit_id"),
                      CheckConstraint("quantity >= 1", name="quantity_positive"))

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("product_units.id", ondelete="CASCADE"))
    quantity: Mapped[int]
    added_at: Mapped[datetime] = mapped_column(default=utcnow)

    unit: Mapped[ProductUnit] = relationship()

    @property
    def pieces(self):
        return self.quantity * self.unit.pieces_per_unit

    @property
    def line_total(self):
        return self.quantity * self.unit.retail_price


class Address(Base):
    __tablename__ = "addresses"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(40), default="Home")
    area_id: Mapped[int | None] = mapped_column(ForeignKey("delivery_areas.id", ondelete="SET NULL"))
    street: Mapped[str] = mapped_column(String(200))  # house number, street, purok
    landmark: Mapped[str] = mapped_column(String(200), default="")  # "beside the chapel"
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    area: Mapped[DeliveryArea | None] = relationship()

    @property
    def one_line(self):
        line = ", ".join(part for part in (self.street, self.area.name if self.area else "") if part)
        return f"{line} (near {self.landmark})" if self.landmark else line


# ---------- Orders ----------

class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        # A double-tap on "Place order" sends the same key twice; the second one finds the first order.
        UniqueConstraint("user_id", "idempotency_key"),
        CheckConstraint(f"status IN ({sql_list(ORDER_STATUSES)})", name="status"),
        CheckConstraint(f"fulfilment IN ('{PICKUP}', '{DELIVERY}')", name="fulfilment"),
        CheckConstraint("subtotal >= 0 AND delivery_fee >= 0 AND discount >= 0 AND total >= 0",
                        name="money_not_negative"),
        Index("ix_orders_store_status", "store_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"))
    # SET NULL: the store keeps its sales records even if a shopper's account goes.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    ref: Mapped[str] = mapped_column(String(12), unique=True)  # DF-7K3QM: easy to say on the phone
    status: Mapped[str] = mapped_column(String(20), default=PLACED)
    fulfilment: Mapped[str] = mapped_column(String(10))
    payment_method: Mapped[str] = mapped_column(String(20))
    price_type: Mapped[str] = mapped_column(String(10), default=RETAIL)
    # Copies, so an order still says who and where after the shopper edits their details.
    customer_name: Mapped[str] = mapped_column(String(80))
    customer_mobile: Mapped[str] = mapped_column(String(20), default="")
    area_name: Mapped[str] = mapped_column(String(80), default="")
    address: Mapped[str] = mapped_column(String(500), default="")
    note: Mapped[str] = mapped_column(String(300), default="")  # from the shopper
    cashier_note: Mapped[str] = mapped_column(String(300), default="")  # shown to the shopper
    subtotal: Mapped[int] = mapped_column(default=0)
    delivery_fee: Mapped[int] = mapped_column(default=0)
    discount: Mapped[int] = mapped_column(default=0)
    total: Mapped[int] = mapped_column(default=0)
    # A copy of the voucher when the order was placed, so the discount can be worked out again
    # after the cashier's price (vouchers.py) even if the owner changes the voucher later.
    voucher_id: Mapped[int | None] = mapped_column(ForeignKey("vouchers.id", ondelete="SET NULL"))
    voucher_code: Mapped[str] = mapped_column(String(30), default="", server_default="")
    voucher_kind: Mapped[str] = mapped_column(String(10), default="", server_default="")
    voucher_value: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    voucher_free_delivery: Mapped[bool] = mapped_column(default=False, server_default=false())
    idempotency_key: Mapped[str] = mapped_column(String(64))
    # Goes up by 1 on every change (orders.move). A change made from an older copy is refused,
    # so two cashiers can't price the same order at once.
    version: Mapped[int] = mapped_column(default=1)
    placed_at: Mapped[datetime] = mapped_column(default=utcnow)
    priced_at: Mapped[datetime | None]
    priced_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Delivery orders: the rider taking it (stage 4).
    rider_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)

    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order", order_by="OrderLine.id",
                                                    cascade="all, delete-orphan", passive_deletes=True)
    events: Mapped[list["OrderEvent"]] = relationship(back_populates="order", order_by="OrderEvent.id",
                                                      cascade="all, delete-orphan", passive_deletes=True)
    priced_by: Mapped[User | None] = relationship(foreign_keys=[priced_by_id])
    rider: Mapped[User | None] = relationship(foreign_keys=[rider_id])
    payments: Mapped[list["Payment"]] = relationship(back_populates="order", order_by="Payment.id",
                                                     cascade="all, delete-orphan", passive_deletes=True)

    @property
    def is_priced(self):
        return self.priced_at is not None

    @property
    def item_count(self):
        return sum(line.quantity for line in self.lines)

    @property
    def is_online(self):
        return self.payment_method == ONLINE

    @property
    def paid_payment(self):
        return next((payment for payment in self.payments if payment.status == PAYMENT_PAID), None)

    @property
    def latest_payment(self):
        return self.payments[-1] if self.payments else None


# An asked-for line: a brand or kind the shopper wants that isn't in the system (yet).
REQUEST_PENDING = "pending"          # the cashier hasn't checked yet
REQUEST_AVAILABLE = "available"      # the cashier has it, at the price they typed
REQUEST_UNAVAILABLE = "unavailable"  # not in the store: it costs nothing and isn't packed


class CartRequest(Base):
    """"Not in the list? Ask for it": something the shopper wants, asked under a product
    (for example "Nescafe Classic" under 3-in-1 coffee). It has no price and holds no stock:
    the cashier says if the store has it, and its price, when pricing the order."""
    __tablename__ = "cart_requests"
    __table_args__ = (CheckConstraint("quantity >= 1", name="quantity_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    wanted: Mapped[str] = mapped_column(String(80))  # what they typed: "Nescafe Classic"
    unit_name: Mapped[str] = mapped_column(String(40))  # one of the product's sizes, or "Piece"
    quantity: Mapped[int]
    added_at: Mapped[datetime] = mapped_column(default=utcnow)

    product: Mapped[Product] = relationship()

    @property
    def name(self):
        return f"{self.product.group_title} - {self.wanted}"


class OrderLine(Base):
    """One line of an order. The name and BOTH prices are copied when the order is placed,
    so a later price change never changes an order that was already made.

    An asked-for line (is_request) has no stock and no price until the cashier checks it:
    request_status says pending, available (then retail_price is the cashier's price) or unavailable."""
    __tablename__ = "order_lines"
    __table_args__ = (
        CheckConstraint("quantity >= 1 AND (pieces >= 1 OR is_request)", name="quantity_positive"),
        CheckConstraint(f"price_type IN ('{RETAIL}', '{WHOLESALE}')", name="price_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("product_units.id", ondelete="SET NULL"))
    product_name: Mapped[str] = mapped_column(String(170))
    unit_name: Mapped[str] = mapped_column(String(40))
    pieces_per_unit: Mapped[int]
    quantity: Mapped[int]
    pieces: Mapped[int]  # quantity x pieces_per_unit: what is held in stock
    retail_price: Mapped[int]
    wholesale_price: Mapped[int]
    price_type: Mapped[str] = mapped_column(String(10), default=RETAIL)  # the cashier's choice
    is_request: Mapped[bool] = mapped_column(default=False, server_default=false())
    request_status: Mapped[str] = mapped_column(String(12), default="", server_default="")

    order: Mapped[Order] = relationship(back_populates="lines")

    @property
    def unit_price(self):
        return self.wholesale_price if self.price_type == WHOLESALE else self.retail_price

    @property
    def counts(self):
        """Does this line cost money? An asked-for line only once the cashier has it."""
        return not self.is_request or self.request_status == REQUEST_AVAILABLE

    @property
    def line_total(self):
        return self.quantity * self.unit_price if self.counts else 0


class OrderEvent(Base):
    """One step in an order's timeline."""
    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    order: Mapped[Order] = relationship(back_populates="events")
    by: Mapped[User | None] = relationship()


# ---------- Payments ----------

class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(f"status IN ({sql_list(PAYMENT_STATUSES)})", name="status"),
        CheckConstraint("amount > 0 AND refunded_amount >= 0 AND refunded_amount <= amount", name="amounts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    gateway: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default=PAYMENT_PENDING)
    amount: Mapped[int]  # centavos: the order's total when the payment started
    # PayMongo's checkout session (cs_...) and, once paid, its payment (pay_...).
    session_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    checkout_url: Mapped[str] = mapped_column(String(500), default="")
    gateway_payment_id: Mapped[str] = mapped_column(String(80), default="")
    paid_with: Mapped[str] = mapped_column(String(40), default="")  # gcash, paymaya, card, cash
    refunded_amount: Mapped[int] = mapped_column(default=0)
    # Paid for an order that was cancelled in the meantime (or paid twice): staff refund it.
    needs_refund: Mapped[bool] = mapped_column(default=False)
    # Cash: who took it (the cashier at pickup, the rider on delivery), and the hand-over
    # that brought a rider's cash to the counter.
    collected_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    remittance_id: Mapped[int | None] = mapped_column(ForeignKey("remittances.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime | None]
    paid_at: Mapped[datetime | None]

    order: Mapped[Order] = relationship(back_populates="payments")
    collected_by: Mapped[User | None] = relationship(foreign_keys=[collected_by_id])
    refunds: Mapped[list["Refund"]] = relationship(back_populates="payment", order_by="Refund.id",
                                                   cascade="all, delete-orphan", passive_deletes=True)


class Refund(Base):
    """Money given back. It is written BEFORE asking PayMongo (status "pending"), because a call
    to another company can't be rolled back: if anything breaks, the row says what was tried."""
    __tablename__ = "refunds"
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), index=True)
    amount: Mapped[int]
    reason: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, done, failed
    gateway_refund_id: Mapped[str] = mapped_column(String(80), default="")
    error: Mapped[str] = mapped_column(String(300), default="")
    by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    payment: Mapped[Payment] = relationship(back_populates="refunds")


class PaymentEvent(Base):
    """Every webhook event we accepted. The UNIQUE event id is what makes a webhook safe to
    send twice: the second copy fails to insert, so it changes nothing."""
    __tablename__ = "payment_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(80), unique=True)
    event_type: Mapped[str] = mapped_column(String(80))
    session_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    result: Mapped[str] = mapped_column(String(40), default="")  # what we did with it
    received_at: Mapped[datetime] = mapped_column(default=utcnow)


# ---------- Stage 4: stock history and cash from riders ----------

# Why stock changed (stock.py).
MOVE_ORDER = "order"          # packed for an online order
MOVE_RESTOCK = "restock"      # a delivery from the supplier
MOVE_COUNT = "count"          # counted on the shelf; the difference
MOVE_IMPORT = "import"        # the starting stock from a CSV file
MOVE_START = "start"          # the starting stock of a new product
MOVE_WALKIN = "walkin"        # sold at the counter (stage 5)
MOVE_REASONS = {MOVE_ORDER: "Online order", MOVE_RESTOCK: "Restock", MOVE_COUNT: "Stock count",
                MOVE_IMPORT: "CSV import", MOVE_START: "Starting stock", MOVE_WALKIN: "Counter sale"}


class StockMove(Base):
    __tablename__ = "stock_moves"
    __table_args__ = (Index("ix_stock_moves_store_created", "store_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    change: Mapped[int]  # pieces: + more on the shelf, - fewer
    stock_after: Mapped[int]
    reason: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(String(200), default="")
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    product: Mapped[Product | None] = relationship()
    order: Mapped[Order | None] = relationship()
    by: Mapped[User | None] = relationship()


class Remittance(Base):
    """A rider hands their cash-on-delivery money to the cashier."""
    __tablename__ = "remittances"
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    rider_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    received_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    amount: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    rider: Mapped[User | None] = relationship(foreign_keys=[rider_id])
    received_by: Mapped[User | None] = relationship(foreign_keys=[received_by_id])


# ---------- Stage 5: the counter ----------

class CashShift(Base):
    """A cashier's turn at the drawer: the cash at the start, and the count at the end."""
    __tablename__ = "cash_shifts"
    __table_args__ = (
        # At most one OPEN shift per cashier: a second "Open shift" at the same moment fails here.
        Index("uq_cash_shifts_one_open", "store_id", "cashier_id", unique=True,
              sqlite_where=text("closed_at IS NULL"), postgresql_where=text("closed_at IS NULL")),
        CheckConstraint("opening_cash >= 0", name="opening_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"))
    cashier_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    opening_cash: Mapped[int]
    opened_at: Mapped[datetime] = mapped_column(default=utcnow)
    closed_at: Mapped[datetime | None]
    expected_cash: Mapped[int | None]  # worked out when it closes
    counted_cash: Mapped[int | None]
    note: Mapped[str] = mapped_column(String(300), default="")

    cashier: Mapped[User | None] = relationship()
    sales: Mapped[list["WalkinSale"]] = relationship(back_populates="shift", order_by="WalkinSale.id")

    @property
    def difference(self):
        if self.counted_cash is None or self.expected_cash is None:
            return None
        return self.counted_cash - self.expected_cash


class WalkinSale(Base):
    __tablename__ = "walkin_sales"
    __table_args__ = (
        CheckConstraint("total >= 0 AND cash_given >= total AND change = cash_given - total", name="cash_adds_up"),
        CheckConstraint("voided_amount >= 0 AND voided_amount <= total", name="voided_in_range"),
        Index("ix_walkin_sales_store_created", "store_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"))
    shift_id: Mapped[int] = mapped_column(ForeignKey("cash_shifts.id", ondelete="CASCADE"), index=True)
    cashier_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    price_type: Mapped[str] = mapped_column(String(10), default=RETAIL)
    total: Mapped[int]
    cash_given: Mapped[int]
    change: Mapped[int]
    # The idempotency key of the ticket: pressing Pay twice records one sale.
    ticket_key: Mapped[str] = mapped_column(String(64), unique=True)
    # Voiding is per item: the money given back so far. voided_at is set once EVERY item is voided.
    voided_amount: Mapped[int] = mapped_column(default=0, server_default="0")
    voided_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    shift: Mapped[CashShift] = relationship(back_populates="sales")
    cashier: Mapped[User | None] = relationship()
    lines: Mapped[list["WalkinLine"]] = relationship(back_populates="sale", order_by="WalkinLine.id",
                                                     cascade="all, delete-orphan", passive_deletes=True)
    voids: Mapped[list["WalkinVoid"]] = relationship(back_populates="sale", order_by="WalkinVoid.id",
                                                     cascade="all, delete-orphan", passive_deletes=True)

    @property
    def receipt_no(self):
        return f"R-{self.id:06d}"

    @property
    def kept_total(self):
        """What the sale is worth now: the total less the items voided."""
        return self.total - self.voided_amount

    @property
    def can_void(self):
        return self.voided_at is None and self.shift.closed_at is None


class WalkinLine(Base):
    __tablename__ = "walkin_lines"
    __table_args__ = (
        CheckConstraint("quantity >= 1", name="quantity_positive"),
        CheckConstraint("voided_quantity >= 0 AND voided_quantity <= quantity", name="voided_in_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("walkin_sales.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("product_units.id", ondelete="SET NULL"))
    product_name: Mapped[str] = mapped_column(String(170))
    unit_name: Mapped[str] = mapped_column(String(40))
    pieces_per_unit: Mapped[int]
    quantity: Mapped[int]
    pieces: Mapped[int]
    price_type: Mapped[str] = mapped_column(String(10))
    unit_price: Mapped[int]
    line_total: Mapped[int]
    voided_quantity: Mapped[int] = mapped_column(default=0, server_default="0")  # of quantity, given back

    sale: Mapped[WalkinSale] = relationship(back_populates="lines")

    @property
    def kept_quantity(self):
        return self.quantity - self.voided_quantity

    @property
    def kept_pieces(self):
        return self.kept_quantity * self.pieces_per_unit

    @property
    def kept_total(self):
        return self.kept_quantity * self.unit_price


class WalkinVoid(Base):
    """Items taken off a counter sale: which, how much was given back, why and by whom."""
    __tablename__ = "walkin_voids"
    __table_args__ = (CheckConstraint("amount >= 0", name="amount_not_negative"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("walkin_sales.id", ondelete="CASCADE"), index=True)
    items: Mapped[str] = mapped_column(String(500))  # "2 x Cola 1.5 L (Bottle)", as they were voided
    amount: Mapped[int]  # centavos given back
    reason: Mapped[str] = mapped_column(String(200), default="")
    by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    sale: Mapped[WalkinSale] = relationship(back_populates="voids")
    by: Mapped[User | None] = relationship()


# ---------- Stage 6: favorites, promos and emails ----------

class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "product_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    product: Mapped[Product] = relationship()


VOUCHER_AMOUNT = "amount"    # value is centavos off
VOUCHER_PERCENT = "percent"  # value is a percentage of the items
VOUCHER_DELIVERY = "delivery"  # free delivery only
VOUCHER_KINDS = {VOUCHER_AMOUNT: "Pesos off", VOUCHER_PERCENT: "Percent off", VOUCHER_DELIVERY: "Free delivery"}


class Voucher(Base):
    __tablename__ = "vouchers"
    __table_args__ = (
        UniqueConstraint("store_id", "code"),
        CheckConstraint(f"kind IN ({sql_list(VOUCHER_KINDS)})", name="kind"),
        # The database refuses more uses than allowed, even if two shoppers order at once.
        CheckConstraint("max_uses IS NULL OR used_count <= max_uses", name="uses_within_limit"),
        CheckConstraint("value >= 0 AND min_spend >= 0 AND used_count >= 0", name="numbers_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(30))  # always UPPER CASE
    kind: Mapped[str] = mapped_column(String(10))
    value: Mapped[int] = mapped_column(default=0)
    min_spend: Mapped[int] = mapped_column(default=0)  # centavos, on the items at retail price
    max_uses: Mapped[int | None]  # empty = no limit
    used_count: Mapped[int] = mapped_column(default=0)
    starts_on: Mapped[date | None]
    ends_on: Mapped[date | None]
    description: Mapped[str] = mapped_column(String(160), default="")
    show_on_home: Mapped[bool] = mapped_column(default=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    @property
    def uses_left(self):
        return None if self.max_uses is None else max(0, self.max_uses - self.used_count)


class VoucherUse(Base):
    """One shopper used one voucher on one order. UNIQUE: each account can use a code once."""
    __tablename__ = "voucher_uses"
    __table_args__ = (UniqueConstraint("voucher_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    voucher_id: Mapped[int] = mapped_column(ForeignKey("vouchers.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Notification(Base):
    """The email OUTBOX. A row is written in the same transaction as the change it tells about,
    so an email is never sent for a change that was rolled back. notices.deliver() sends them later."""
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_status", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    to_email: Mapped[str] = mapped_column(String(254))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending, sent, failed, skipped
    tries: Mapped[int] = mapped_column(default=0)
    error: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    sent_at: Mapped[datetime | None]
