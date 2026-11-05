"""The data model.

    stores ─┬─< users (owner, cashier, rider, shopper)
            ├─< invites (a link that makes a cashier or rider)
            ├─< categories
            ├─< photos
            └─< products ──< product_units (piece, pack, case: each with a retail AND a wholesale price)

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
