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
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, LargeBinary, String, Text, UniqueConstraint
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
PAYMENT_STATUS_LABELS = {PAYMENT_PENDING: "Started", PAYMENT_PAID: "Paid", PAYMENT_FAILED: "Didn't go through",
                         PAYMENT_EXPIRED: "Expired", PAYMENT_REFUNDED: "Refunded"}
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
    name: Mapped[str] = mapped_column(String(80))
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
    idempotency_key: Mapped[str] = mapped_column(String(64))
    # Goes up by 1 on every change (orders.move). A change made from an older copy is refused,
    # so two cashiers can't price the same order at once.
    version: Mapped[int] = mapped_column(default=1)
    placed_at: Mapped[datetime] = mapped_column(default=utcnow)
    priced_at: Mapped[datetime | None]
    priced_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)

    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order", order_by="OrderLine.id",
                                                    cascade="all, delete-orphan", passive_deletes=True)
    events: Mapped[list["OrderEvent"]] = relationship(back_populates="order", order_by="OrderEvent.id",
                                                      cascade="all, delete-orphan", passive_deletes=True)
    priced_by: Mapped[User | None] = relationship(foreign_keys=[priced_by_id])
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


class OrderLine(Base):
    """One line of an order. The name and BOTH prices are copied when the order is placed,
    so a later price change never changes an order that was already made."""
    __tablename__ = "order_lines"
    __table_args__ = (
        CheckConstraint("quantity >= 1 AND pieces >= 1", name="quantity_positive"),
        CheckConstraint(f"price_type IN ('{RETAIL}', '{WHOLESALE}')", name="price_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("product_units.id", ondelete="SET NULL"))
    product_name: Mapped[str] = mapped_column(String(80))
    unit_name: Mapped[str] = mapped_column(String(40))
    pieces_per_unit: Mapped[int]
    quantity: Mapped[int]
    pieces: Mapped[int]  # quantity x pieces_per_unit: what is held in stock
    retail_price: Mapped[int]
    wholesale_price: Mapped[int]
    price_type: Mapped[str] = mapped_column(String(10), default=RETAIL)  # the cashier's choice

    order: Mapped[Order] = relationship(back_populates="lines")

    @property
    def unit_price(self):
        return self.wholesale_price if self.price_type == WHOLESALE else self.retail_price

    @property
    def line_total(self):
        return self.quantity * self.unit_price


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
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime | None]
    paid_at: Mapped[datetime | None]

    order: Mapped[Order] = relationship(back_populates="payments")
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
