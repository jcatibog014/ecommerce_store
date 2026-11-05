"""The demo: a fresh sample store per visitor, View As for every role, and an example on every page."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from dfjc import db
from dfjc.dates import utcnow
from dfjc.models import Category, Invite, Product, Store, User

from .conftest import add_product, post


@pytest.fixture
def demo(app):
    """A browser that pressed Try Demo."""
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = "x"
    response = post(client, "/demo")
    assert response.status_code == 302 and response.location == "/"
    return client


def demo_store(app):
    with app.app_context():
        return db.session().scalar(select(Store).where(Store.is_demo.is_(True)).order_by(Store.id.desc()))


def count(app, model, *where):
    with app.app_context():
        return db.session().scalar(select(func.count()).select_from(model).where(*where))


def view_as(client, role):
    response = post(client, "/demo/view-as", {"role": role})
    assert response.status_code == 302
    return response.location


def test_try_demo_makes_a_sample_store(app, demo):
    store = demo_store(app)
    assert store.is_demo and store.demo_expires_at > utcnow() + timedelta(hours=1, minutes=55)
    assert count(app, Product, Product.store_id == store.id) >= 30
    assert count(app, Category, Category.store_id == store.id) >= 8
    roles = {role for (role,) in db_rows(app, select(User.role).where(User.store_id == store.id))}
    assert roles == {"owner", "cashier", "rider", "shopper"}
    page = demo.get("/").get_data(as_text=True)
    assert "Demo store." in page and "Welcome to the demo" in page and 'id="view-as"' in page


def db_rows(app, query):
    with app.app_context():
        return db.session().execute(query).all()


def test_the_demo_works_before_the_real_store_exists(app, demo):
    assert demo.get("/").status_code == 200


def test_each_visitor_gets_their_own_store(app, demo):
    other = app.test_client()
    with other.session_transaction() as session:
        session["csrf"] = "x"
    post(other, "/demo")
    assert count(app, Store, Store.is_demo.is_(True)) == 2


def test_the_demo_never_shows_the_real_store(app, demo, store_id):
    add_product(app, store_id, "Real store only")
    assert "Real store only" not in demo.get("/products").get_data(as_text=True)


def test_old_demos_are_deleted_with_everything_in_them(app, demo):
    old = demo_store(app)
    with app.app_context():
        db.session().get(Store, old.id).demo_expires_at = utcnow() - timedelta(minutes=1)
        db.session().commit()
    newcomer = app.test_client()
    with newcomer.session_transaction() as session:
        session["csrf"] = "x"
    post(newcomer, "/demo")
    # Only the newcomer's store is left (SQLite may give it the old id again, so count everything).
    new = demo_store(app)
    assert count(app, Store) == 1 and new.demo_expires_at > utcnow()
    assert count(app, Product) == count(app, Product, Product.store_id == new.id)
    assert count(app, User) == count(app, User, User.store_id == new.id)


def test_view_as_plays_every_role(app, demo):
    assert view_as(demo, "cashier") == "/manage/orders"
    assert demo.get("/manage/products").status_code == 200
    assert demo.get("/manage/products/new").status_code == 403  # a cashier can't add products
    assert view_as(demo, "owner") == "/manage/orders"
    assert demo.get("/manage/products/new").status_code == 200
    assert demo.get("/manage/staff").status_code == 200
    assert view_as(demo, "rider") == "/"
    assert demo.get("/manage/products").status_code == 403
    assert view_as(demo, "shopper") == "/"
    assert demo.get("/manage/products").status_code == 403


def test_view_as_is_only_for_the_demo(client, shopper):
    assert post(shopper, "/demo/view-as", {"role": "owner"}).status_code == 403
    assert shopper.get("/manage/products").status_code == 403


def test_view_as_refuses_unknown_roles(demo):
    assert post(demo, "/demo/view-as", {"role": "admin"}).status_code == 400


def test_the_demo_cant_change_its_account_or_upload(app, demo):
    assert post(demo, "/settings/", {"action": "details", "name": "Someone", "mobile": "09170000000"}).status_code == 403
    view_as(demo, "owner")
    assert post(demo, "/manage/staff/invite", {"role": "cashier"}).status_code == 403
    store = demo_store(app)
    with app.app_context():
        product_id = db.session().scalar(select(Product.id).where(Product.store_id == store.id).limit(1))
    assert post(demo, f"/manage/products/{product_id}/photo", {}).status_code == 403


def test_the_owner_can_try_changes_in_the_demo_store(app, demo):
    view_as(demo, "owner")
    store = demo_store(app)
    with app.app_context():
        product = db.session().scalar(select(Product).where(Product.store_id == store.id).limit(1))
    response = post(demo, f"/manage/products/{product.id}", {
        "name": "Renamed in the demo", "category_id": "", "stock_pieces": "5000", "low_stock_pieces": "0",
        "is_active": "1", "version": str(product.version)})
    assert response.status_code == 302
    assert "Renamed in the demo" in demo.get("/products").get_data(as_text=True)


# ---------- Every page the demo can open has an example ----------

SHOPPER_PAGES = {
    "/": ["Shop by Category", "New in the Store", "product-card"],
    "/products": ["product-card", "Page 1 of 2"],
    "/products?sort=price-low": ["product-card"],
    "/settings/": ["Your Details", "Ana Reyes"],
    "/cart": ["Bottled water 500 mL", "Instant pancit canton 60 g", "Check out"],
    "/checkout": ["45 Mabini Street", "San Isidro", "Place order"],
    "/orders": ["In Progress", "Past Orders", "Waiting for the cashier", "Confirmed", "Cancelled"],
}
STAFF_PAGES = {
    "cashier": {
        "/manage/products": ["Bottled water 500 mL", "Case of 24"],
        "/manage/products?show=low": ["Powdered juice, mango"],
        "/manage/products?show=off": ["Ice candy, buko"],
        "/manage/categories": ["Canned Goods", "Personal Care"],
        "/manage/orders": ["Marites Santos", "Ramon Garcia", "Ana Reyes", "Has bulk items"],
        "/manage/orders?tab=confirmed": ["Marites Santos", "Ana Reyes"],
        "/manage/orders?tab=cancelled": ["Ramon Garcia", "Ana Reyes"],
        "/manage/orders?tab=all": ["Waiting for the cashier", "Confirmed", "Cancelled"],
    },
    "owner": {
        "/manage/products/new": ["First Unit"],
        "/manage/staff": ["Lorna Cruz", "Jun Dizon", "Used by Jun Dizon", "New cashier for the night shift"],
        "/manage/store": ["123 Rizal Street"],
        "/manage/delivery-areas": ["San Isidro", "Malinis"],
    },
}


def test_every_shopper_page_has_an_example(demo):
    for url, signs in SHOPPER_PAGES.items():
        page = demo.get(url).get_data(as_text=True)
        missing = [sign for sign in signs if sign not in page]
        assert missing == [], url


@pytest.mark.parametrize("role", ["cashier", "owner"])
def test_every_staff_page_has_an_example(demo, role):
    view_as(demo, role)
    for url, signs in STAFF_PAGES[role].items():
        page = demo.get(url).get_data(as_text=True)
        missing = [sign for sign in signs if sign not in page]
        assert missing == [], url


def test_every_kind_of_stock_has_an_example(app, demo):
    page = demo.get("/products?page=1").get_data(as_text=True) + demo.get("/products?page=2").get_data(as_text=True)
    assert "In stock" in page and "Only 7 left" in page and "Out of stock" in page


def test_a_product_page_and_a_category_page(app, demo):
    store = demo_store(app)
    with app.app_context():
        s = db.session()
        water = s.scalar(select(Product).where(Product.store_id == store.id, Product.name == "Bottled water 500 mL"))
        drinks = water.category_id
    page = demo.get(f"/products/{water.id}").get_data(as_text=True)
    assert "Case of 24" in page and "₱300.00" in page
    page = demo.get(f"/products?category={drinks}").get_data(as_text=True)
    assert "<h1>Drinks</h1>" in page and "Cola 1.5 L" in page


def test_the_demo_has_an_open_and_a_used_invite(app, demo):
    store = demo_store(app)
    assert count(app, Invite, Invite.store_id == store.id, Invite.used_at.is_(None)) == 1
    assert count(app, Invite, Invite.store_id == store.id, Invite.used_at.is_not(None)) == 1


def test_every_demo_order_page_has_its_example(app, demo):
    """The shopper's three orders, and every order as the cashier sees it."""
    from dfjc.models import Order
    store = demo_store(app)
    with app.app_context():
        found = db.session().scalars(select(Order).where(Order.store_id == store.id)).all()
        visitor = db.session().scalar(select(User).where(User.store_id == store.id, User.name == "Ana Reyes"))
        mine = {order.ref: order.status for order in found if order.user_id == visitor.id}
        every = {order.ref: order.status for order in found}
    assert sorted(mine.values()) == ["awaiting_payment", "cancelled", "confirmed", "paid", "placed"]
    words = {"placed": "Waiting for the Cashier", "confirmed": "Priced:", "cancelled": "I ordered the wrong size.",
             "awaiting_payment": "last payment didn", "paid": "You paid with GCash"}
    for ref, status in mine.items():
        assert words[status] in demo.get(f"/orders/{ref}").get_data(as_text=True), ref
    view_as(demo, "cashier")
    for ref, status in every.items():
        page = demo.get(f"/manage/orders/{ref}").get_data(as_text=True)
        assert ("Price This Order" in page) == (status == "placed"), ref


def test_the_demo_holds_stock_for_its_open_orders(app, demo):
    """Open orders hold their pieces; cancelled ones gave them back."""
    from dfjc import orders as order_rules
    from dfjc.models import Order
    store = demo_store(app)
    with app.app_context():
        s = db.session()
        held = {}
        for order in s.scalars(select(Order).where(Order.store_id == store.id)):
            if order.status in order_rules.HOLDS_STOCK:
                for product_id, pieces in order_rules.pieces_by_product(order).items():
                    held[product_id] = held.get(product_id, 0) + pieces
        reserved = {product.id: product.reserved_pieces
                    for product in s.scalars(select(Product).where(Product.store_id == store.id))
                    if product.reserved_pieces}
    assert reserved == held
