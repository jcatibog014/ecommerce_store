"""After pricing: packing takes stock, pickup and delivery take cash, riders hand it over; and the stock tools."""
import io

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dfjc import db, fulfil, stock
from dfjc.dates import utcnow
from dfjc.models import (
    DELIVERED, OUT_FOR_DELIVERY, PACKING, PICKED_UP, READY, Category, DeliveryArea, Order, Payment, Product, ProductUnit,
    Remittance, StockMove, Store, User,
)

from .conftest import add_product, get, log_in_as, make_user, post
from .test_orders import add, checkout_key, only_order, unit_id


def count(app, model, *where):
    with app.app_context():
        return db.session().scalar(select(func.count()).select_from(model).where(*where))


def order_now(app):
    with app.app_context():
        order = db.session().scalar(select(Order))
        _ = order.lines, order.payments, order.events
        return order


@pytest.fixture
def water(app, store_id):
    return add_product(app, store_id, "Bottled water 500 mL", units=(("Bottle", 1, 1500, 1300),), stock=100)


def place_and_price(app, shopper, cashier_id, water, store_id, delivery=False):
    add(shopper, unit_id(app, water, "Bottle"), 4)
    form = {"fulfilment": "pickup", "payment": "cash", "idempotency_key": checkout_key(shopper)}
    if delivery:
        with app.app_context():
            area = DeliveryArea(store_id=store_id, name="Poblacion", fee=3000)
            db.session().add(area)
            db.session().commit()
            form.update(fulfilment="delivery", address_id="new", area_id=str(area.id), street="12 Luna Street")
    post(shopper, "/checkout", form)
    order = only_order(app)
    with app.app_context():
        from dfjc import orders
        s = db.session()
        loaded = s.get(Order, order.id)
        orders.price_order(s, loaded, {}, s.get(User, cashier_id), utcnow(), loaded.version)
        s.commit()
    return order_now(app)


def step(client, order, action, **extra):
    current = order_now(client.application)
    return post(client, f"/manage/orders/{order.ref}/step", {"action": action, "version": current.version, **extra})


@pytest.fixture
def pickup(app, shopper, cashier_id, water, store_id):
    return place_and_price(app, shopper, cashier_id, water, store_id)


@pytest.fixture
def delivery(app, shopper, cashier_id, water, store_id):
    return place_and_price(app, shopper, cashier_id, water, store_id, delivery=True)


# ---------- Packing and pickup ----------

def test_packing_takes_the_held_pieces_off_the_shelf(app, client, cashier_id, pickup, water):
    assert (get(app, Product, water).stock_pieces, get(app, Product, water).reserved_pieces) == (100, 4)
    log_in_as(client, cashier_id)
    assert pickup.ref in client.get("/manage/packing").get_data(as_text=True)
    step(client, pickup, "pack")
    product = get(app, Product, water)
    assert (product.stock_pieces, product.reserved_pieces, order_now(app).status) == (96, 0, PACKING)
    with app.app_context():
        move = db.session().scalar(select(StockMove).where(StockMove.reason == "order"))
    assert (move.change, move.stock_after, move.order_id, move.note) == (-4, 96, pickup.id, pickup.ref)


def test_pickup_records_the_cash(app, client, cashier_id, pickup):
    log_in_as(client, cashier_id)
    step(client, pickup, "pack")
    step(client, pickup, "packed")
    assert order_now(app).status == READY
    response = step(client, pickup, "picked_up")
    assert response.status_code == 302
    order = order_now(app)
    assert order.status == PICKED_UP
    cash = order.payments[-1]
    assert (cash.gateway, cash.status, cash.amount, cash.collected_by_id) == ("cash", "paid", 6000, cashier_id)


def test_a_step_from_an_old_page_is_refused(app, client, cashier_id, pickup):
    log_in_as(client, cashier_id)
    old_version = order_now(app).version
    step(client, pickup, "pack")
    response = post(client, f"/manage/orders/{pickup.ref}/step", {"action": "packed", "version": old_version},
                    follow_redirects=True)
    assert "changed a moment ago" in response.get_data(as_text=True)
    assert order_now(app).status == PACKING


def test_a_pickup_order_cant_go_out_with_a_rider(app, client, cashier_id, rider_id, pickup):
    log_in_as(client, cashier_id)
    step(client, pickup, "pack")
    step(client, pickup, "packed")
    step(client, pickup, "send_out", rider_id=rider_id)
    assert order_now(app).status == READY


def test_only_staff_who_pack_can_open_packing(client, shopper_id, rider_id):
    for user_id in (shopper_id, rider_id):
        log_in_as(client, user_id)
        assert client.get("/manage/packing").status_code == 403


# ---------- Deliveries ----------

def out_with(client, app, order, rider_id):
    step(client, order, "pack")
    step(client, order, "packed")
    step(client, order, "send_out", rider_id=rider_id)
    assert order_now(app).status == OUT_FOR_DELIVERY


def test_a_rider_delivers_and_holds_the_cash(app, client, cashier_id, rider_id, delivery):
    log_in_as(client, cashier_id)
    out_with(client, app, delivery, rider_id)
    log_in_as(client, rider_id)
    page = client.get("/deliveries").get_data(as_text=True)
    assert "12 Luna Street" in page and "Collect ₱90.00" in page and "Cash with You: ₱0.00" in page
    post(client, f"/deliveries/{delivery.ref}/delivered", {"version": order_now(app).version})
    order = order_now(app)
    assert order.status == DELIVERED and order.payments[-1].collected_by_id == rider_id
    assert "Cash with You: ₱90.00" in client.get("/deliveries").get_data(as_text=True)


def test_another_rider_cant_finish_my_delivery(app, client, store_id, cashier_id, rider_id, delivery):
    other = make_user(app, store_id, "rider2@example.com", role="rider")
    log_in_as(client, cashier_id)
    out_with(client, app, delivery, rider_id)
    log_in_as(client, other)
    assert "delivery-card" not in client.get("/deliveries").get_data(as_text=True)
    response = post(client, f"/deliveries/{delivery.ref}/delivered", {"version": order_now(app).version},
                    follow_redirects=True)
    assert "with another rider" in response.get_data(as_text=True)
    assert order_now(app).status == OUT_FOR_DELIVERY


def test_nobody_home_brings_it_back(app, client, cashier_id, rider_id, delivery):
    log_in_as(client, cashier_id)
    out_with(client, app, delivery, rider_id)
    log_in_as(client, rider_id)
    post(client, f"/deliveries/{delivery.ref}/not-home", {"version": order_now(app).version, "note": "Gate locked"})
    order = order_now(app)
    assert (order.status, order.rider_id, order.events[-1].note) == (READY, None, "Gate locked")
    assert count(app, Payment, Payment.gateway == "cash") == 0


def test_only_riders_can_be_sent(app, client, cashier_id, shopper_id, delivery):
    log_in_as(client, cashier_id)
    step(client, delivery, "pack")
    step(client, delivery, "packed")
    response = step(client, delivery, "send_out", rider_id=shopper_id)
    assert response.status_code == 302 and order_now(app).status == READY


# ---------- The rider's cash ----------

def delivered_cash(app, client, cashier_id, rider_id, order):
    log_in_as(client, cashier_id)
    out_with(client, app, order, rider_id)
    log_in_as(client, rider_id)
    post(client, f"/deliveries/{order.ref}/delivered", {"version": order_now(app).version})
    log_in_as(client, cashier_id)
    return order_now(app).payments[-1]


def test_the_cashier_receives_the_riders_cash_once(app, client, cashier_id, rider_id, delivery):
    payment = delivered_cash(app, client, cashier_id, rider_id, delivery)
    page = client.get("/manage/rider-cash").get_data(as_text=True)
    assert "Jun Dizon: ₱90.00" in page
    post(client, f"/manage/rider-cash/{rider_id}", {"payment_id": str(payment.id)})
    with app.app_context():
        remittance = db.session().scalar(select(Remittance))
    assert (remittance.amount, remittance.rider_id, remittance.received_by_id) == (9000, rider_id, cashier_id)
    assert get(app, Payment, payment.id).remittance_id == remittance.id
    # Pressing Receive again (an old page) receives nothing twice.
    response = post(client, f"/manage/rider-cash/{rider_id}", {"payment_id": str(payment.id)}, follow_redirects=True)
    assert "already received" in response.get_data(as_text=True)
    assert count(app, Remittance) == 1
    assert "No Cash to Receive" in client.get("/manage/rider-cash").get_data(as_text=True)


def test_two_cashiers_receiving_at_once(app, store_id, cashier_id, owner_id, rider_id, client, delivery):
    payment = delivered_cash(app, client, cashier_id, rider_id, delivery)
    with app.app_context():
        engine = db.engine()
    one, two = Session(engine, expire_on_commit=False), Session(engine, expire_on_commit=False)
    store_one, store_two = one.get(Store, store_id), two.get(Store, store_id)
    fulfil.remit(one, store_one, one.get(User, rider_id), [payment.id], one.get(User, cashier_id), utcnow())
    one.commit()
    with pytest.raises(fulfil.FulfilProblem):
        fulfil.remit(two, store_two, two.get(User, rider_id), [payment.id], two.get(User, owner_id), utcnow())
    two.rollback()
    one.close()
    two.close()
    assert count(app, Remittance) == 1


def test_riders_cant_receive_cash(client, rider_id):
    log_in_as(client, rider_id)
    assert client.get("/manage/rider-cash").status_code == 403


# ---------- Restock and count ----------

def test_restock_and_count_keep_a_history(app, client, cashier_id, water):
    log_in_as(client, cashier_id)
    post(client, f"/manage/products/{water}/restock", {"pieces": "48", "note": "Supplier"})
    assert get(app, Product, water).stock_pieces == 148
    post(client, f"/manage/products/{water}/count", {"counted": "145", "seen": "148", "note": "Monthly count"})
    assert get(app, Product, water).stock_pieces == 145
    with app.app_context():
        moves = db.session().scalars(select(StockMove).order_by(StockMove.id)).all()
    assert [(move.reason, move.change, move.stock_after) for move in moves] == [("restock", 48, 148), ("count", -3, 145)]
    page = client.get("/manage/stock").get_data(as_text=True)
    assert "Monthly count" in page and "-3" in page and "+48" in page


def test_a_count_from_before_a_sale_is_refused(app, client, cashier_id, water):
    log_in_as(client, cashier_id)
    post(client, f"/manage/products/{water}/restock", {"pieces": "10"})  # stock is 110 now, the page said 100
    response = post(client, f"/manage/products/{water}/count", {"counted": "98", "seen": "100"}, follow_redirects=True)
    assert "changed while you were counting" in response.get_data(as_text=True)
    assert get(app, Product, water).stock_pieces == 110


def test_a_count_below_the_held_pieces_is_refused(app, client, cashier_id, pickup, water):
    log_in_as(client, cashier_id)
    response = post(client, f"/manage/products/{water}/count", {"counted": "2", "seen": "100"}, follow_redirects=True)
    assert "held for orders" in response.get_data(as_text=True)
    assert get(app, Product, water).stock_pieces == 100


def test_two_restocks_at_once_both_count(app, store_id, cashier_id, water):
    with app.app_context():
        engine = db.engine()
    one, two = Session(engine), Session(engine)
    product_one, product_two = one.get(Product, water), two.get(Product, water)  # both read 100
    stock.restock(one, product_one, 24, None, utcnow())
    one.commit()
    stock.restock(two, product_two, 12, None, utcnow())
    two.commit()
    one.close()
    two.close()
    assert get(app, Product, water).stock_pieces == 136  # never 124 or 112


def test_shoppers_cant_change_stock(client, shopper_id, water):
    log_in_as(client, shopper_id)
    assert post(client, f"/manage/products/{water}/restock", {"pieces": "5"}).status_code == 403


def test_a_new_product_starts_its_history(app, owner):
    post(owner, "/manage/products/new", {"title": "Vinegar", "category_id": "", "stock_pieces": "12", "low_stock_pieces": "0",
                                         "is_active": "1", "unit_name": "Bottle", "unit_pieces_per_unit": "1",
                                         "unit_retail_price": "40"})
    with app.app_context():
        move = db.session().scalar(select(StockMove))
    assert (move.reason, move.change, move.stock_after) == ("start", 12, 12)


# ---------- Importing products ----------

CSV = (b"name,category,unit,pieces,retail,wholesale,stock\n"
       b"Soy sauce 1 L,Condiments,Bottle,1,48,45,48\n"
       b"Soy sauce 1 L,Condiments,Case of 12,12,560,530,\n"
       b"Fish sauce 750 mL,Condiments,Bottle,1,38.50,,24\n")


def upload(client, data, follow=False):
    return post(client, "/manage/products/import", {"file": (io.BytesIO(data), "products.csv")},
                content_type="multipart/form-data", follow_redirects=follow)


def test_import_shows_a_preview_then_saves_everything(app, owner):
    preview = upload(owner, CSV)
    page = preview.get_data(as_text=True)
    assert "What Will Change" in page and "Import 2 products" in page and "Case of 12" in page
    assert count(app, Product) == 0  # nothing saved yet
    text = CSV.decode()
    response = post(owner, "/manage/products/import", {"text": text, "confirm": "1"})
    assert response.status_code == 302
    with app.app_context():
        s = db.session()
        soy = s.scalar(select(Product).where(Product.name == "Soy sauce 1 L"))
        fish = s.scalar(select(Product).where(Product.name == "Fish sauce 750 mL"))
        units = s.scalars(select(ProductUnit).where(ProductUnit.product_id == soy.id).order_by(ProductUnit.id)).all()
        assert [(u.name, u.pieces_per_unit, u.retail_price, u.wholesale_price) for u in units] == [
            ("Bottle", 1, 4800, 4500), ("Case of 12", 12, 56000, 53000)]
        assert (soy.stock_pieces, fish.stock_pieces) == (48, 24)
        assert fish.units[0].wholesale_price == 3850  # empty = the retail price
        assert s.scalar(select(Category)).name == "Condiments"
    assert count(app, StockMove, StockMove.reason == "import") == 2


def test_an_import_with_a_bad_row_saves_nothing(app, owner):
    bad = CSV + b"Rice,Grains,Kilo,0,abc,,\n"
    page = upload(owner, bad).get_data(as_text=True)
    assert "Row 5" in page and "Pieces must be a whole number" in page and "retail must be a price" in page
    response = post(owner, "/manage/products/import", {"text": bad.decode(), "confirm": "1"})
    assert response.status_code == 400 and count(app, Product) == 0


def test_importing_again_updates_prices_but_keeps_stock(app, owner, store_id):
    product_id = add_product(app, store_id, "Soy sauce 1 L", units=(("Bottle", 1, 4000, 3800),), stock=7)
    post(owner, "/manage/products/import", {"text": CSV.decode(), "confirm": "1"})
    product = get(app, Product, product_id)
    with app.app_context():
        bottle = db.session().scalar(select(ProductUnit).where(ProductUnit.product_id == product_id,
                                                               ProductUnit.name == "Bottle"))
    assert product.stock_pieces == 7 and bottle.retail_price == 4800
    assert count(app, Product, Product.name == "Soy sauce 1 L") == 1


def test_an_import_needs_the_columns(app, owner):
    response = upload(owner, b"product,price\nRice,52\n", follow=True)
    assert "Missing: name, unit, pieces, retail" in response.get_data(as_text=True)


def test_import_is_for_the_owner(client, cashier_id):
    log_in_as(client, cashier_id)
    assert client.get("/manage/products/import").status_code == 403


def test_the_example_file_downloads(owner):
    response = owner.get("/manage/products/import/template.csv")
    assert response.mimetype == "text/csv" and b"name,brand,kind,category,unit,pieces" in response.data


# ---------- The demo ----------

def demo_client(app, role):
    demo = app.test_client()
    with demo.session_transaction() as session:
        session["csrf"] = "x"
    post(demo, "/demo")
    post(demo, "/demo/view-as", {"role": role})
    return demo


def test_the_demo_fills_every_column_and_the_riders_pages(app):
    demo = demo_client(app, "cashier")
    page = demo.get("/manage/packing").get_data(as_text=True)
    for words in ("To Pack", "Nena Bautista", "Carlo Mendoza", "Lito Ramos", "Choose a rider", "With Jun"):
        assert words in page
    assert "Nothing here." not in page  # every column has an example
    cash = demo.get("/manage/rider-cash").get_data(as_text=True)
    assert "Jun Dizon:" in cash and "Received Before" in cash
    history = demo.get("/manage/stock").get_data(as_text=True)
    assert "Two bars were wet" in history and "Mabuhay Trading" in history and "Online order" in history
    assert "Done" in demo.get("/manage/orders?tab=done").get_data(as_text=True)
    post(demo, "/demo/view-as", {"role": "rider"})
    deliveries = demo.get("/deliveries").get_data(as_text=True)
    assert "Carlo Mendoza" in deliveries and "Cash with You: ₱" in deliveries and "Delivered Today" in deliveries
    post(demo, "/demo/view-as", {"role": "owner"})
    assert "Choose a File" in demo.get("/manage/products/import").get_data(as_text=True)
