"""Changing an order after placing it (edits.py): add, change and remove items until packing starts."""
from sqlalchemy import select

from dfjc import db, orders, payments
from dfjc.dates import utcnow
from dfjc.models import CartItem, Order, OrderEvent, Payment, Product, User

from .conftest import add_product, get, log_in_as, post
from .test_orders import add, checkout_key, count, only_order, unit_id, water  # noqa: F401 (water: a fixture)


def placed(app, shopper, water, bottles=2, payment="cash"):
    add(shopper, unit_id(app, water, "Bottle"), bottles)
    post(shopper, "/checkout", {"fulfilment": "pickup", "payment": payment, "idempotency_key": checkout_key(shopper)})
    return only_order(app)


def start(shopper, order):
    return post(shopper, f"/orders/{order.ref}/edit")


def change(shopper, order, line, **form):
    return post(shopper, f"/orders/{order.ref}/lines/{line.id}", form, follow_redirects=True)


def price(app, cashier_id):
    with app.app_context():
        s = db.session()
        order = s.scalar(select(Order))
        orders.price_order(s, order, {}, s.get(User, cashier_id), utcnow(), order.version)
        s.commit()
    return only_order(app)


def reserved(app, product_id):
    return get(app, Product, product_id).reserved_pieces


# ---------- Adding from the shop ----------

def test_while_editing_add_to_cart_adds_to_the_order(app, shopper, water):
    order = placed(app, shopper, water)
    page = shopper.get(f"/orders/{order.ref}").get_data(as_text=True)
    assert "Change This Order" in page and "Edit order" in page
    start(shopper, order)
    assert f"Editing order {order.ref}" in shopper.get("/products").get_data(as_text=True)
    response = add(shopper, unit_id(app, water, "Case of 24"), 1)
    assert response.status_code == 302
    order = only_order(app)
    assert [(line.unit_name, line.quantity) for line in order.lines] == [("Bottle", 2), ("Case of 24", 1)]
    assert (order.subtotal, order.total, order.status) == (3000 + 30000, 33000, "placed")
    assert reserved(app, water) == 2 + 24 and count(app, CartItem) == 0  # the cart stays empty
    assert order.events[-1].note == "Added 1 x Bottled water 500 mL (Case of 24)"
    assert "Changed by the shopper" in shopper.get(f"/orders/{order.ref}").get_data(as_text=True)
    # The same size again joins its line.
    add(shopper, unit_id(app, water, "Bottle"), 3)
    assert [line.quantity for line in only_order(app).lines] == [5, 1]
    # Done editing: Add to cart is the cart again.
    post(shopper, f"/orders/{order.ref}/done")
    add(shopper, unit_id(app, water, "Bottle"), 1)
    assert count(app, CartItem) == 1 and len(only_order(app).lines) == 2


def test_asking_for_an_item_while_editing(app, shopper, water):
    order = placed(app, shopper, water)
    start(shopper, order)
    post(shopper, "/cart/ask", {"product_id": water, "wanted": "Sparkling", "unit_name": "Bottle", "quantity": 2})
    line = only_order(app).lines[-1]
    assert (line.is_request, line.request_status, line.pieces, line.quantity) == (True, "pending", 0, 2)
    assert reserved(app, water) == 2


def test_adding_more_than_is_left_changes_nothing(app, shopper, store_id, water):
    order = placed(app, shopper, water)
    soap = add_product(app, store_id, "Bath soap", units=(("Bar", 1, 3800, 3500),), stock=3)
    start(shopper, order)
    response = post(shopper, "/cart/add", {"unit_id": unit_id(app, soap, "Bar"), "quantity": 5},
                    follow_redirects=True)
    assert "only 3 pieces of Bath soap can still be added" in response.get_data(as_text=True)
    assert len(only_order(app).lines) == 1 and reserved(app, soap) == 0


# ---------- Changing amounts and removing ----------

def test_change_amounts_and_remove_items(app, shopper, water):
    order = placed(app, shopper, water, bottles=4)
    start(shopper, order)
    add(shopper, unit_id(app, water, "Case of 24"), 1)
    order = only_order(app)
    bottles, case = order.lines
    page = shopper.get(f"/orders/{order.ref}").get_data(as_text=True)
    assert "Editing This Order" in page and "Done editing" in page and 'name="quantity" value="4"' in page
    change(shopper, order, bottles, quantity="1")
    assert reserved(app, water) == 1 + 24 and only_order(app).subtotal == 1500 + 30000
    change(shopper, order, case, action="remove")
    order = only_order(app)
    assert [line.unit_name for line in order.lines] == ["Bottle"] and reserved(app, water) == 1
    assert order.total == 1500
    # The last item stays: stopping the order is Cancel.
    response = change(shopper, order, order.lines[0], action="remove")
    assert "cancel it instead" in response.get_data(as_text=True) and len(only_order(app).lines) == 1


def test_someone_else_cant_change_my_order(app, client, store_id, shopper, water):
    from .conftest import make_user
    order = placed(app, shopper, water)
    log_in_as(client, make_user(app, store_id, "maria@example.com"))
    assert post(client, f"/orders/{order.ref}/edit").status_code == 404
    assert post(client, f"/orders/{order.ref}/lines/{order.lines[0].id}", {"quantity": "9"}).status_code == 404


# ---------- After the cashier priced it ----------

def test_a_priced_order_goes_back_to_the_cashier(app, client, shopper, cashier_id, water):
    placed(app, shopper, water)
    order = price(app, cashier_id)
    assert order.status == "confirmed"
    start(shopper, order)
    add(shopper, unit_id(app, water, "Bottle"), 1)
    order = only_order(app)
    assert (order.status, order.priced_at, order.priced_by_id, order.subtotal) == ("placed", None, None, 4500)
    event = order.events[-1]
    assert (event.from_status, event.to_status) == ("confirmed", "placed")
    assert event.note == "Added 1 x Bottled water 500 mL (Bottle). The cashier prices it again."
    log_in_as(client, cashier_id)
    assert "Changed by the shopper" in client.get("/manage/orders").get_data(as_text=True)


def test_the_cashier_cant_price_the_old_version(app, client, shopper, cashier_id, water):
    order = placed(app, shopper, water)
    old_version = order.version
    start(shopper, order)
    add(shopper, unit_id(app, water, "Bottle"), 1)
    with app.app_context():
        s = db.session()
        loaded = s.scalar(select(Order))
        try:
            orders.price_order(s, loaded, {}, s.get(User, cashier_id), utcnow(), old_version)
            raised = False
        except orders.OrderChanged:
            raised = True
    assert raised and only_order(app).status == "placed"


def test_an_open_payment_page_is_closed_and_a_late_payment_is_flagged(app, shopper, cashier_id, water):
    placed(app, shopper, water, payment="online")
    order = price(app, cashier_id)
    assert order.status == "awaiting_payment"
    sandbox = post(shopper, f"/orders/{order.ref}/pay").location
    session_id = sandbox.rsplit("/", 1)[1]
    start(shopper, order)
    add(shopper, unit_id(app, water, "Bottle"), 1)
    with app.app_context():
        assert db.session().scalar(select(Payment)).status == "expired"
        # The old page was still open in another tab and paid anyway: it is kept for a refund.
        result = payments.handle_event(db.session(), "evt_late", payments.PAID_EVENT, session_id, "pay_late",
                                       "gcash", utcnow())
        db.session().commit()
        payment = db.session().scalar(select(Payment))
    assert result == "needs_refund" and payment.needs_refund
    assert only_order(app).status == "placed"


def test_no_edits_once_paid_or_packing(app, shopper, cashier_id, water):
    placed(app, shopper, water)
    order = price(app, cashier_id)
    with app.app_context():
        s = db.session()
        loaded = s.scalar(select(Order))
        orders.move(s, loaded, "packing", s.get(User, cashier_id), utcnow())
        s.commit()
    page = shopper.get(f"/orders/{order.ref}").get_data(as_text=True)
    assert "Change This Order" not in page
    response = post(shopper, f"/orders/{order.ref}/edit", follow_redirects=True)
    assert "can&#39;t be changed any more" in response.get_data(as_text=True)
    add(shopper, unit_id(app, water, "Bottle"), 1)
    assert count(app, CartItem) == 1  # the cart, not the order
    assert count(app, OrderEvent, OrderEvent.from_status == OrderEvent.to_status) == 0


def test_a_voucher_keeps_its_least_spend(app, shopper, store_id, water):
    from .test_extras import make_voucher
    make_voucher(app, store_id, min_spend=30000)
    add(shopper, unit_id(app, water, "Case of 24"), 1)
    add(shopper, unit_id(app, water, "Bottle"), 2)
    post(shopper, "/checkout", {"fulfilment": "pickup", "payment": "cash", "voucher": "SUKI50",
                                "idempotency_key": checkout_key(shopper)})
    order = only_order(app)
    assert order.voucher_code == "SUKI50"
    start(shopper, order)
    case = next(line for line in order.lines if line.unit_name == "Case of 24")
    response = change(shopper, order, case, action="remove")
    assert "is for orders of 300.00 pesos or more" in response.get_data(as_text=True)
    assert len(only_order(app).lines) == 2


def test_the_demo_has_a_changed_order(app):
    demo = app.test_client()
    with demo.session_transaction() as session:
        session["csrf"] = "x"
    post(demo, "/demo")
    orders_page = demo.get("/orders").get_data(as_text=True)
    assert "Waiting for the cashier" in orders_page
    post(demo, "/demo/view-as", {"role": "cashier"})
    assert "Changed by the shopper" in demo.get("/manage/orders").get_data(as_text=True)
