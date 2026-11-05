"""Cart, checkout (one transaction, no overselling, no double orders), the cashier's price, and cancelling."""
import re
import threading

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dfjc import checkout, db, orders
from dfjc.dates import utcnow
from dfjc.models import (
    CANCELLED, CONFIRMED, PACKING, PLACED, Address, CartItem, DeliveryArea, Order, OrderEvent, Product, ProductUnit,
    Store, User,
)

from .conftest import add_product, get, log_in_as, make_user, post

WATER = (("Bottle", 1, 1500, 1300), ("Case of 24", 24, 30000, 27600))


def unit_id(app, product_id, name):
    with app.app_context():
        return db.session().scalar(select(ProductUnit.id).where(ProductUnit.product_id == product_id,
                                                                ProductUnit.name == name))


def add(client, unit, quantity=1):
    return post(client, "/cart/add", {"unit_id": unit, "quantity": quantity})


def count(app, model, *where):
    with app.app_context():
        return db.session().scalar(select(func.count()).select_from(model).where(*where))


def only_order(app):
    with app.app_context():
        order = db.session().scalar(select(Order))
        _ = order.lines, order.events  # load them before the session closes
        return order


def checkout_key(client):
    page = client.get("/checkout").get_data(as_text=True)
    return re.search(r'name="idempotency_key" value="([^"]+)"', page).group(1)


def place(client, follow=False, **form):
    key = form.pop("key", None) or checkout_key(client)
    return post(client, "/checkout", {"fulfilment": "pickup", "idempotency_key": key, **form}, follow_redirects=follow)


def add_area(app, store_id, name="San Isidro", fee=4000, active=True):
    with app.app_context():
        area = DeliveryArea(store_id=store_id, name=name, fee=fee, is_active=active)
        db.session().add(area)
        db.session().commit()
        return area.id


@pytest.fixture
def water(app, store_id):
    return add_product(app, store_id, "Bottled water 500 mL", units=WATER, stock=100)


# ---------- The cart ----------

def test_visitors_are_asked_to_log_in_to_order(client, store_id, water):
    response = add(client, unit_id(client.application, water, "Bottle"))
    assert response.status_code == 302
    assert response.location == f"/login?next=/products/{water}"


def test_staff_dont_have_a_cart(client, cashier_id, water):
    log_in_as(client, cashier_id)
    assert add(client, unit_id(client.application, water, "Bottle")).status_code == 403
    assert client.get("/cart").status_code == 403
    assert "Ordering is for shopper accounts" in client.get(f"/products/{water}").get_data(as_text=True)


def test_add_update_and_remove(app, shopper, water):
    bottle = unit_id(app, water, "Bottle")
    add(shopper, bottle, 2)
    add(shopper, bottle, 3)  # added on top
    with app.app_context():
        item = db.session().scalar(select(CartItem))
    assert item.quantity == 5
    page = shopper.get("/cart").get_data(as_text=True)
    assert "Bottled water 500 mL" in page and "₱75.00" in page
    assert '<span class="badge">5' in shopper.get("/").get_data(as_text=True)  # the header's cart count
    post(shopper, f"/cart/{item.id}", {"quantity": "2"})
    assert get(app, CartItem, item.id).quantity == 2
    post(shopper, f"/cart/{item.id}", {"quantity": "0"})
    assert get(app, CartItem, item.id) is None
    add(shopper, bottle)
    with app.app_context():
        item = db.session().scalar(select(CartItem))
    post(shopper, f"/cart/{item.id}/remove")
    assert count(app, CartItem) == 0


def test_someone_elses_cart_line_is_not_found(app, client, store_id, shopper_id, water):
    other = make_user(app, store_id, "maria@example.com")
    log_in_as(client, other)
    add(client, unit_id(app, water, "Bottle"))
    with app.app_context():
        item_id = db.session().scalar(select(CartItem.id))
    log_in_as(client, shopper_id)
    assert post(client, f"/cart/{item_id}", {"quantity": "9"}).status_code == 404
    assert post(client, f"/cart/{item_id}/remove").status_code == 404


def test_the_cart_warns_about_stock_and_blocks_checkout(app, shopper, store_id):
    rice = add_product(app, store_id, "Rice", units=(("Kilo", 1, 5200, 5000), ("Sack of 25 kg", 25, 125000, 118000)),
                       stock=30)
    add(shopper, unit_id(app, rice, "Sack of 25 kg"), 1)
    add(shopper, unit_id(app, rice, "Kilo"), 10)  # 25 + 10 is more than 30
    page = shopper.get("/cart").get_data(as_text=True)
    assert "more than we have" in page and "Check out</a>" not in page
    assert shopper.get("/checkout").status_code == 302


def test_the_empty_cart_says_so(shopper):
    assert "Your Cart Is Empty" in shopper.get("/cart").get_data(as_text=True)


# ---------- Placing an order ----------

def test_placing_an_order_holds_stock_copies_prices_and_empties_the_cart(app, shopper, shopper_id, water):
    add(shopper, unit_id(app, water, "Case of 24"), 2)
    add(shopper, unit_id(app, water, "Bottle"), 5)
    response = place(shopper, note="After 5 PM")
    order = only_order(app)
    assert response.location == f"/orders/{order.ref}"
    assert re.fullmatch(r"DF-[A-HJ-NP-Z2-9]{6}", order.ref)
    assert (order.status, order.fulfilment, order.payment_method) == (PLACED, "pickup", "cash_pickup")
    assert (order.subtotal, order.delivery_fee, order.total) == (2 * 30000 + 5 * 1500, 0, 67500)
    assert (order.customer_name, order.note) == ("Juan dela Cruz", "After 5 PM")
    case = next(line for line in order.lines if line.unit_name == "Case of 24")
    assert (case.pieces, case.retail_price, case.wholesale_price, case.price_type) == (48, 30000, 27600, "retail")
    product = get(app, Product, water)
    assert (product.stock_pieces, product.reserved_pieces, product.available_pieces) == (100, 53, 47)
    assert count(app, CartItem) == 0
    assert [event.to_status for event in order.events] == [PLACED]
    page = shopper.get(f"/orders/{order.ref}").get_data(as_text=True)
    assert "Waiting for the Cashier" in page and "Estimated total" in page and "₱675.00" in page


def test_a_later_price_change_never_changes_an_order(app, shopper, water):
    add(shopper, unit_id(app, water, "Bottle"), 2)
    place(shopper)
    with app.app_context():
        db.session().get(ProductUnit, unit_id(app, water, "Bottle")).retail_price = 9999
        db.session().commit()
    assert only_order(app).lines[0].retail_price == 1500


def test_delivery_saves_a_new_address_and_adds_the_fee(app, shopper, store_id, water):
    area = add_area(app, store_id, fee=4000)
    add(shopper, unit_id(app, water, "Bottle"), 2)
    place(shopper, fulfilment="delivery", address_id="new", area_id=str(area), street="45 Mabini Street",
          landmark="the chapel", label="Home", save_address="1")
    order = only_order(app)
    assert (order.fulfilment, order.payment_method, order.area_name) == ("delivery", "cod", "San Isidro")
    assert (order.delivery_fee, order.total) == (4000, 7000)
    assert order.address == "45 Mabini Street, San Isidro (near the chapel)"
    with app.app_context():
        saved = db.session().scalar(select(Address))
    assert saved.street == "45 Mabini Street"
    # Next time it's offered.
    add(shopper, unit_id(app, water, "Bottle"))
    page = shopper.get("/checkout").get_data(as_text=True)
    assert f'value="{saved.id}" checked' in page


def test_delivery_needs_an_area_we_go_to_and_a_street(app, shopper, store_id, water):
    closed = add_area(app, store_id, "Malinis", active=False)
    add_area(app, store_id)
    add(shopper, unit_id(app, water, "Bottle"))
    response = place(shopper, fulfilment="delivery", address_id="new", area_id=str(closed), street="")
    page = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "Choose your barangay from the list." in page and "Your street address is needed." in page
    assert count(app, Order) == 0


def test_a_double_tap_makes_one_order(app, shopper, water):
    add(shopper, unit_id(app, water, "Bottle"), 2)
    key = checkout_key(shopper)
    first = place(shopper, key=key)
    add(shopper, unit_id(app, water, "Bottle"), 2)  # even if the cart has something again
    second = place(shopper, key=key)
    assert first.location == second.location
    assert count(app, Order) == 1
    assert get(app, Product, water).reserved_pieces == 2


def test_running_out_at_checkout_keeps_the_cart_and_holds_nothing(app, shopper, water, store_id):
    soap = add_product(app, store_id, "Soap", units=(("Bar", 1, 3800, 3500),), stock=10)
    add(shopper, unit_id(app, water, "Bottle"), 5)
    add(shopper, unit_id(app, soap, "Bar"), 4)
    key = checkout_key(shopper)
    with app.app_context():
        db.session().get(Product, soap).stock_pieces = 3  # someone bought soap at the counter
        db.session().commit()
    response = place(shopper, key=key, follow=True)
    page = response.get_data(as_text=True)
    assert "Please fix the lines marked in your cart first." in page and "Only 3 left." in page
    assert count(app, Order) == 0 and count(app, CartItem) == 2
    # The water was held first, then rolled back with everything else.
    assert get(app, Product, water).reserved_pieces == 0


def test_a_product_switched_off_after_adding_cant_be_ordered(app, shopper, water):
    add(shopper, unit_id(app, water, "Bottle"))
    key = checkout_key(shopper)
    with app.app_context():
        db.session().get(Product, water).is_active = False
        db.session().commit()
    place(shopper, key=key)
    assert count(app, Order) == 0


def test_ten_shoppers_race_for_the_last_three_pieces(app, store_id):
    """The guarded UPDATE lets exactly three of them win; nobody gets a piece that isn't there."""
    product_id = add_product(app, store_id, "Last of the coffee", units=(("Sachet", 1, 1200, 1050),), stock=3)
    bottle = unit_id(app, product_id, "Sachet")
    users = [make_user(app, store_id, f"racer{number}@example.com") for number in range(10)]
    with app.app_context():
        engine = db.engine()
        with Session(engine) as s:
            for user_id in users:
                s.add(CartItem(user_id=user_id, unit_id=bottle, quantity=1))
            s.commit()
    start = threading.Barrier(len(users))
    results = []

    def shop(user_id):
        with Session(engine, expire_on_commit=False) as s:
            user, store = s.get(User, user_id), s.get(Store, store_id)
            start.wait()
            try:
                checkout.place_order(s, user, store, {"fulfilment": "pickup", "payment_method": "cash_pickup"},
                                     f"key-{user_id}", utcnow())
                s.commit()
                results.append("won")
            except checkout.OutOfStock:
                s.rollback()
                results.append("out")

    threads = [threading.Thread(target=shop, args=(user_id,)) for user_id in users]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["out"] * 7 + ["won"] * 3
    assert get(app, Product, product_id).reserved_pieces == 3
    assert count(app, Order) == 3


# ---------- The cashier's price ----------

@pytest.fixture
def bulk_order(app, shopper, water):
    add(shopper, unit_id(app, water, "Case of 24"), 2)
    add(shopper, unit_id(app, water, "Bottle"), 4)
    place(shopper)
    return only_order(app)


def price_form(order, **choices):
    form = {"version": str(order.version), "cashier_note": "Wholesale for the cases"}
    for line in order.lines:
        form[f"line_{line.id}"] = choices.get(line.unit_name, "retail")
    return form


def test_the_cashier_prices_wholesale_and_the_shopper_sees_the_total(app, client, cashier_id, shopper_id, bulk_order):
    log_in_as(client, cashier_id)
    page = client.get("/manage/orders").get_data(as_text=True)
    assert bulk_order.ref in page and "Has bulk items" in page
    assert "Price This Order" in client.get(f"/manage/orders/{bulk_order.ref}").get_data(as_text=True)
    response = post(client, f"/manage/orders/{bulk_order.ref}/price", price_form(bulk_order, **{"Case of 24": "wholesale"}))
    assert response.status_code == 302
    order = only_order(app)
    assert (order.status, order.price_type, order.priced_by_id) == (CONFIRMED, "mixed", cashier_id)
    assert (order.subtotal, order.total) == (2 * 27600 + 4 * 1500, 61200)
    assert order.cashier_note == "Wholesale for the cases"
    assert [event.to_status for event in order.events] == [PLACED, CONFIRMED]
    log_in_as(client, shopper_id)
    page = client.get(f"/orders/{order.ref}").get_data(as_text=True)
    assert "Priced: ₱612.00" in page and "Wholesale</span>" in page and "wholesale price on some items" in page
    assert "Estimated total" not in page


def test_an_order_is_priced_once(app, client, cashier_id, owner_id, bulk_order):
    log_in_as(client, cashier_id)
    post(client, f"/manage/orders/{bulk_order.ref}/price", price_form(bulk_order))
    log_in_as(client, owner_id)
    response = post(client, f"/manage/orders/{bulk_order.ref}/price", price_form(bulk_order, **{"Case of 24": "wholesale"}),
                    follow_redirects=True)
    assert "already priced" in response.get_data(as_text=True)
    assert only_order(app).price_type == "retail"


def test_a_stale_pricing_form_changes_nothing(app, client, cashier_id, bulk_order):
    log_in_as(client, cashier_id)
    stale = {**price_form(bulk_order, **{"Case of 24": "wholesale"}), "version": "0"}
    response = post(client, f"/manage/orders/{bulk_order.ref}/price", stale, follow_redirects=True)
    assert "changed by someone else" in response.get_data(as_text=True)
    order = only_order(app)
    assert order.status == PLACED and all(line.price_type == "retail" for line in order.lines)


def test_two_cashiers_pricing_at_the_same_moment(app, store_id, cashier_id, owner_id, bulk_order):
    """Both read version 1. The conditional UPDATE lets only the first one through."""
    with app.app_context():
        engine = db.engine()
    one, two = Session(engine, expire_on_commit=False), Session(engine, expire_on_commit=False)
    first, second = one.get(Order, bulk_order.id), two.get(Order, bulk_order.id)
    _ = first.lines, second.lines
    orders.price_order(one, first, {}, one.get(User, cashier_id), utcnow(), 1)
    one.commit()
    with pytest.raises(orders.OrderChanged):
        orders.price_order(two, second, {line.id: "wholesale" for line in second.lines}, two.get(User, owner_id),
                           utcnow(), 1)
    two.rollback()
    one.close()
    two.close()
    order = only_order(app)
    assert order.price_type == "retail" and len(order.events) == 2


def test_a_rider_or_shopper_cant_price(app, client, rider_id, shopper_id, bulk_order):
    for user_id in (rider_id, shopper_id):
        log_in_as(client, user_id)
        assert client.get("/manage/orders").status_code == 403
        assert post(client, f"/manage/orders/{bulk_order.ref}/price", price_form(bulk_order)).status_code == 403


# ---------- Cancelling ----------

def test_the_shopper_cancels_and_the_stock_comes_back(app, shopper, bulk_order, water):
    assert get(app, Product, water).reserved_pieces == 52
    post(shopper, f"/orders/{bulk_order.ref}/cancel", {"reason": "Wrong size"})
    order = only_order(app)
    assert order.status == CANCELLED and order.events[-1].note == "Wrong size"
    assert get(app, Product, water).reserved_pieces == 0
    page = shopper.get(f"/orders/{order.ref}").get_data(as_text=True)
    assert "Wrong size" in page and "Cancel This Order" not in page
    # Cancelled once only.
    post(shopper, f"/orders/{bulk_order.ref}/cancel")
    assert count(app, OrderEvent, OrderEvent.to_status == CANCELLED) == 1


def test_the_shopper_cant_cancel_once_packing_starts(app, shopper, bulk_order, cashier_id):
    with app.app_context():
        s = db.session()
        order = s.get(Order, bulk_order.id)
        cashier = s.get(User, cashier_id)
        orders.price_order(s, order, {}, cashier, utcnow(), order.version)
        orders.move(s, order, PACKING, cashier, utcnow())
        s.commit()
    response = post(shopper, f"/orders/{bulk_order.ref}/cancel", follow_redirects=True)
    assert "can&#39;t be cancelled any more" in response.get_data(as_text=True)
    assert only_order(app).status == PACKING


def test_staff_cancel_with_a_reason(app, client, cashier_id, bulk_order, water):
    log_in_as(client, cashier_id)
    response = post(client, f"/manage/orders/{bulk_order.ref}/cancel", {"reason": ""}, follow_redirects=True)
    assert "Say why" in response.get_data(as_text=True)
    post(client, f"/manage/orders/{bulk_order.ref}/cancel", {"reason": "Sorry, we ran out."})
    assert only_order(app).status == CANCELLED
    assert get(app, Product, water).reserved_pieces == 0


# ---------- The rules of moving ----------

def test_the_moves_follow_the_rules(app, bulk_order, cashier_id):
    with app.app_context():
        s = db.session()
        order = s.get(Order, bulk_order.id)
        cashier = s.get(User, cashier_id)
        with pytest.raises(orders.MoveNotAllowed):
            orders.move(s, order, "delivered", cashier, utcnow())  # can't skip ahead
        orders.price_order(s, order, {}, cashier, utcnow(), order.version)
        orders.move(s, order, PACKING, cashier, utcnow())
        orders.move(s, order, "ready", cashier, utcnow())
        assert not orders.can_move(order, "out_for_delivery")  # a pickup order is picked up, not delivered
        orders.move(s, order, "picked_up", cashier, utcnow())
        assert orders.TRANSITIONS[order.status] == ()
        s.commit()
        assert [event.to_status for event in order.events] == [PLACED, CONFIRMED, PACKING, "ready", "picked_up"]


def test_every_status_has_a_label_and_a_place_in_the_rules():
    from dfjc.models import ORDER_STATUSES
    assert set(orders.TRANSITIONS) == set(ORDER_STATUSES) == set(orders.STATUS_LABELS) == set(orders.EVENT_WORDS)
    for targets in orders.TRANSITIONS.values():
        assert set(targets) <= set(ORDER_STATUSES)


def test_the_steps_show_how_far_an_order_got():
    class Fake:
        fulfilment = "delivery"
        status = "confirmed"
        payment_method = "cod"
    assert orders.steps(Fake()) == [("Placed", "done"), ("Priced", "now"), ("Packing", "next"),
                                    ("Ready to go", "next"), ("On the way", "next"), ("Delivered", "next")]
    Fake.status = "cancelled"
    assert [state for _, state in orders.steps(Fake())] == ["done"] + ["next"] * 5
    Fake.payment_method, Fake.status = "online", "paid"  # online orders have a Paid step
    assert [label for label, state in orders.steps(Fake()) if state == "now"] == ["Paid"]


# ---------- Privacy ----------

def test_shoppers_see_only_their_own_orders(app, client, store_id, bulk_order):
    other = make_user(app, store_id, "maria@example.com")
    log_in_as(client, other)
    assert client.get(f"/orders/{bulk_order.ref}").status_code == 404
    assert post(client, f"/orders/{bulk_order.ref}/cancel").status_code == 404
    assert bulk_order.ref not in client.get("/orders").get_data(as_text=True)


def test_another_stores_staff_cant_see_the_order(app, client, bulk_order):
    with app.app_context():
        s = db.session()
        demo = Store(name="Demo", is_demo=True)
        s.add(demo)
        s.commit()
        demo_id = demo.id
    stranger = make_user(app, demo_id, "owner@demo.example", role="owner")
    log_in_as(client, stranger)
    assert client.get(f"/manage/orders/{bulk_order.ref}").status_code == 404
    assert bulk_order.ref not in client.get("/manage/orders?tab=all").get_data(as_text=True)


def test_order_history_splits_in_progress_and_past(app, shopper, bulk_order, water):
    add(shopper, unit_id(app, water, "Bottle"))
    place(shopper)
    post(shopper, f"/orders/{bulk_order.ref}/cancel", follow_redirects=True)
    page = shopper.get("/orders").get_data(as_text=True)
    assert page.index("In Progress") < page.index("Past Orders") < page.index(bulk_order.ref)


# ---------- Delivery areas ----------

def test_delivery_areas(app, owner, store_id):
    assert post(owner, "/manage/delivery-areas", {"name": "Poblacion", "fee": "30", "sort_order": "0"}).status_code == 302
    assert post(owner, "/manage/delivery-areas", {"name": "Poblacion", "fee": "35"}).status_code == 400
    with app.app_context():
        area = db.session().scalar(select(DeliveryArea))
    assert area.fee == 3000 and area.is_active
    post(owner, f"/manage/delivery-areas/{area.id}", {"name": "Poblacion", "fee": "35", "sort_order": "1",
                                                     "is_active_sent": "1"})
    area = get(app, DeliveryArea, area.id)
    assert (area.fee, area.is_active) == (3500, False)


def test_only_the_owner_changes_delivery_areas(client, cashier_id):
    log_in_as(client, cashier_id)
    assert client.get("/manage/delivery-areas").status_code == 403
