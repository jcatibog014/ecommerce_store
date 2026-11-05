""""Not in the list? Ask for it": asked-for items go into the order, and the cashier says if the store has them."""
import re

import pytest
from sqlalchemy import func, select

from dfjc import db, orders
from dfjc.dates import utcnow
from dfjc.models import CartRequest, Order, Product, StockMove, User

from .conftest import add_product, get, log_in_as, post
from .test_orders import add, checkout_key, only_order, unit_id


def count(app, model, *where):
    with app.app_context():
        return db.session().scalar(select(func.count()).select_from(model).where(*where))


@pytest.fixture
def coffee(app, store_id):
    return add_product(app, store_id, "3-in-1 coffee sachet - Kape Juan Original", title="3-in-1 coffee sachet",
                       brand="Kape Juan", kind="Original", category="Drinks",
                       units=(("Sachet", 1, 1200, 1050), ("Pack of 10", 10, 11000, 10000)), stock=100)


def ask(client, product_id, wanted="Nescafe Classic", unit_name="Sachet", quantity=3):
    return post(client, "/cart/ask", {"product_id": product_id, "wanted": wanted, "unit_name": unit_name,
                                      "quantity": quantity})


def place(client):
    return post(client, "/checkout", {"fulfilment": "pickup", "payment": "cash", "idempotency_key": checkout_key(client)})


def price(client, order, **form):
    return post(client, f"/manage/orders/{order.ref}/price", {"version": str(order.version), **form},
                follow_redirects=True)


def test_the_product_page_offers_ask_for_it(client, store_id, coffee):
    page = client.get(f"/products/{coffee}").get_data(as_text=True)
    assert "Not in the list? Ask for it" in page and 'name="wanted"' in page
    assert '<option value="Pack of 10">' in page  # the sizes come from the product


def test_visitors_log_in_first_and_staff_cant_ask(app, client, coffee, cashier_id):
    response = ask(client, coffee)
    assert response.status_code == 302 and "/login" in response.location
    log_in_as(client, cashier_id)
    assert ask(client, coffee).status_code == 403


def test_asking_puts_it_in_the_cart_without_a_price(app, shopper, coffee):
    ask(shopper, coffee)
    with app.app_context():
        asked = db.session().scalar(select(CartRequest))
        assert asked.name == "3-in-1 coffee sachet - Nescafe Classic"
    assert (asked.wanted, asked.unit_name, asked.quantity) == ("Nescafe Classic", "Sachet", 3)
    page = shopper.get("/cart").get_data(as_text=True)
    assert "Asked for" in page and "Nescafe Classic" in page and "Price later" in page
    assert '<span class="badge">3' in shopper.get("/").get_data(as_text=True)


def test_asking_needs_words(app, shopper, coffee):
    response = ask(shopper, coffee, wanted="  ")
    assert response.status_code == 302 and count(app, CartRequest) == 0


def test_a_size_that_isnt_the_products_becomes_its_first_size(app, shopper, coffee):
    ask(shopper, coffee, unit_name="Truck")
    with app.app_context():
        assert db.session().scalar(select(CartRequest.unit_name)) in ("Sachet", "Pack of 10")


def test_an_order_of_only_asked_for_items_holds_no_stock(app, shopper, coffee):
    ask(shopper, coffee)
    assert place(shopper).status_code == 302
    order = only_order(app)
    line = order.lines[0]
    assert (line.is_request, line.request_status, line.pieces, line.line_total) == (True, "pending", 0, 0)
    assert line.product_name == "3-in-1 coffee sachet - Nescafe Classic"
    assert get(app, Product, coffee).reserved_pieces == 0 and count(app, CartRequest) == 0
    page = shopper.get(f"/orders/{order.ref}").get_data(as_text=True)
    assert "The cashier checks if we have it" in page


def test_the_cashier_must_answer_every_asked_for_item(app, shopper, cashier_id, coffee):
    add(shopper, unit_id(app, coffee, "Sachet"), 2)
    ask(shopper, coffee)
    place(shopper)
    order = only_order(app)
    log_in_as(shopper, cashier_id)
    page = shopper.get(f"/manage/orders/{order.ref}").get_data(as_text=True)
    assert "Do we have it?" in page and "We have it" in page and "Not available" in page
    assert "Has asked-for items" in shopper.get("/manage/orders").get_data(as_text=True)
    response = price(shopper, order)  # no answer for the asked-for line
    assert "Say if you have" in response.get_data(as_text=True)
    assert only_order(app).status == "placed"


def asked_line(order):
    return next(line for line in order.lines if line.is_request)


def test_we_have_it_adds_it_at_the_cashiers_price(app, shopper, shopper_id, cashier_id, coffee):
    add(shopper, unit_id(app, coffee, "Sachet"), 2)  # 24.00
    ask(shopper, coffee)
    place(shopper)
    order = only_order(app)
    log_in_as(shopper, cashier_id)
    line = asked_line(order)
    price(shopper, order, **{f"request_{line.id}": "available", f"price_{line.id}": "13.50"})
    order = only_order(app)
    line = asked_line(order)
    assert (line.request_status, line.unit_price, line.line_total) == ("available", 1350, 4050)
    assert (order.status, order.subtotal, order.total) == ("confirmed", 2400 + 4050, 6450)
    log_in_as(shopper, shopper_id)
    page = shopper.get(f"/orders/{order.ref}").get_data(as_text=True)
    assert "We have it" in page and "₱13.50" in page and "Priced: ₱64.50" in page


def test_not_available_costs_nothing_and_is_crossed_out(app, shopper, shopper_id, cashier_id, coffee):
    add(shopper, unit_id(app, coffee, "Sachet"), 2)
    ask(shopper, coffee)
    place(shopper)
    order = only_order(app)
    log_in_as(shopper, cashier_id)
    line = asked_line(order)
    price(shopper, order, **{f"request_{line.id}": "unavailable"})
    order = only_order(app)
    assert asked_line(order).request_status == "unavailable" and order.total == 2400
    log_in_as(shopper, shopper_id)
    page = shopper.get(f"/orders/{order.ref}").get_data(as_text=True)
    assert 'class="is-unavailable"' in page and "Not available" in page


def test_we_have_it_needs_a_price(app, shopper, cashier_id, coffee):
    ask(shopper, coffee)
    place(shopper)
    order = only_order(app)
    log_in_as(shopper, cashier_id)
    line = asked_line(order)
    response = price(shopper, order, **{f"request_{line.id}": "available", f"price_{line.id}": ""})
    assert "Type the price" in response.get_data(as_text=True) and only_order(app).status == "placed"


def test_nothing_available_means_cancel_instead(app, shopper, cashier_id, coffee):
    ask(shopper, coffee)
    place(shopper)
    order = only_order(app)
    log_in_as(shopper, cashier_id)
    line = asked_line(order)
    response = price(shopper, order, **{f"request_{line.id}": "unavailable"})
    assert "Cancel it instead" in response.get_data(as_text=True) and only_order(app).status == "placed"


def test_packing_takes_no_stock_for_asked_for_items(app, shopper, cashier_id, coffee):
    add(shopper, unit_id(app, coffee, "Sachet"), 2)
    ask(shopper, coffee)
    place(shopper)
    order = only_order(app)
    with app.app_context():
        s = db.session()
        loaded = s.get(Order, order.id)
        cashier = s.get(User, cashier_id)
        line = next(item for item in loaded.lines if item.is_request)
        orders.price_order(s, loaded, {}, cashier, utcnow(), loaded.version, requests={line.id: 1350})
        orders.move(s, loaded, "packing", cashier, utcnow())
        s.commit()
    assert get(app, Product, coffee).stock_pieces == 98  # only the 2 sachets in the system
    assert count(app, StockMove, StockMove.reason == "order") == 1


def test_removing_an_asked_for_item(app, shopper, coffee):
    ask(shopper, coffee)
    page = shopper.get("/cart").get_data(as_text=True)
    request_id = re.search(r'/cart/asks/(\d+)/remove', page).group(1)
    post(shopper, f"/cart/asks/{request_id}/remove")
    assert count(app, CartRequest) == 0


def test_someone_elses_asked_for_item_is_not_found(app, client, store_id, shopper, coffee):
    from .conftest import make_user
    ask(shopper, coffee)
    with app.app_context():
        request_id = db.session().scalar(select(CartRequest.id))
    log_in_as(client, make_user(app, store_id, "maria@example.com"))
    assert post(client, f"/cart/asks/{request_id}/remove").status_code == 404


def test_the_demo_has_asked_for_examples(app):
    demo = app.test_client()
    with demo.session_transaction() as session:
        session["csrf"] = "x"
    post(demo, "/demo")
    assert "Kape Juan Mocha" in demo.get("/cart").get_data(as_text=True)
    post(demo, "/demo/view-as", {"role": "cashier"})
    queue = demo.get("/manage/orders").get_data(as_text=True)
    assert "Has asked-for items" in queue
    with app.app_context():
        lines = db.session().execute(select(Order.ref, Order.status).join(Order.lines).where(
            Order.lines.any(is_request=True))).all()
    statuses = {status for _, status in lines}
    assert "placed" in statuses and "confirmed" in statuses
