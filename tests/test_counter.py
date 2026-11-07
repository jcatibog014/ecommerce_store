"""The counter: shifts, walk-in sales (one transaction, the same stock guard as online), void, the drawer."""
import re

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dfjc import counter, db
from dfjc.dates import utcnow
from dfjc.models import CashShift, Product, StockMove, Store, User, WalkinSale

from .conftest import add_product, get, log_in_as, post
from .test_orders import add, checkout_key, unit_id


def count(app, model, *where):
    with app.app_context():
        return db.session().scalar(select(func.count()).select_from(model).where(*where))


@pytest.fixture
def water(app, store_id):
    return add_product(app, store_id, "Bottled water 500 mL",
                       units=(("Bottle", 1, 1500, 1300), ("Case of 24", 24, 30000, 27600)), stock=100)


@pytest.fixture
def at_counter(client, cashier_id):
    log_in_as(client, cashier_id)
    post(client, "/counter/open", {"opening_cash": "1000"})
    return client


def ticket_key(client):
    page = client.get("/counter").get_data(as_text=True)
    return re.search(r'name="ticket_key" value="([^"]+)"', page).group(1)


def ring(client, unit, quantity=1, price_type="retail"):
    return post(client, "/counter/add", {"unit_id": unit, "quantity": quantity, "price_type": price_type})


def last_sale(app):
    with app.app_context():
        sale = db.session().scalar(select(WalkinSale).order_by(WalkinSale.id.desc()))
        _ = sale.lines, sale.shift
        return sale


# ---------- Shifts ----------

def test_the_counter_starts_with_opening_a_shift(app, client, cashier_id):
    log_in_as(client, cashier_id)
    assert "Open My Shift" in client.get("/counter").get_data(as_text=True)
    post(client, "/counter/open", {"opening_cash": "1,000"})
    with app.app_context():
        shift = db.session().scalar(select(CashShift))
    assert (shift.opening_cash, shift.cashier_id, shift.closed_at) == (100000, cashier_id, None)
    assert "Type a name or scan a barcode" in client.get("/counter").get_data(as_text=True)


def test_one_open_shift_per_cashier(app, at_counter, cashier_id, store_id):
    post(at_counter, "/counter/open", {"opening_cash": "500"})  # pressed again: still one
    assert count(app, CashShift) == 1
    with app.app_context():
        with Session(db.engine()) as s:  # the database itself refuses a second open shift
            store, cashier = s.get(Store, store_id), s.get(User, cashier_id)
            with pytest.raises(IntegrityError):
                counter.open_shift(s, store, cashier, 0, utcnow())


def test_shoppers_and_riders_cant_use_the_counter(client, shopper_id, rider_id):
    for user_id in (shopper_id, rider_id):
        log_in_as(client, user_id)
        assert client.get("/counter").status_code == 403


# ---------- Selling ----------

def test_a_walkin_sale_takes_stock_and_gives_change(app, at_counter, cashier_id, water):
    ring(at_counter, unit_id(app, water, "Bottle"), 3)
    ring(at_counter, unit_id(app, water, "Case of 24"), 1, "wholesale")
    page = at_counter.get("/counter").get_data(as_text=True)
    assert "₱321.00" in page  # 3 x 15 + 276
    response = post(at_counter, "/counter/pay", {"cash_given": "500", "ticket_key": ticket_key(at_counter)})
    sale = last_sale(app)
    assert response.location == f"/counter/sales/{sale.id}"
    assert (sale.total, sale.cash_given, sale.change, sale.price_type) == (32100, 50000, 17900, "mixed")
    assert get(app, Product, water).stock_pieces == 100 - 27
    with app.app_context():
        move = db.session().scalar(select(StockMove).where(StockMove.reason == "walkin"))
    assert (move.change, move.note) == (-27, sale.receipt_no)
    receipt = at_counter.get(response.location).get_data(as_text=True)
    assert "Change: ₱179.00" in receipt and sale.receipt_no in receipt and "W = wholesale price" in receipt
    assert "This Sale" in at_counter.get("/counter").get_data(as_text=True) and "Nothing yet" in \
        at_counter.get("/counter").get_data(as_text=True)  # the ticket is empty again


def test_not_enough_cash_sells_nothing(app, at_counter, water):
    ring(at_counter, unit_id(app, water, "Bottle"), 2)
    response = post(at_counter, "/counter/pay", {"cash_given": "20", "ticket_key": ticket_key(at_counter)},
                    follow_redirects=True)
    assert "at least the total" in response.get_data(as_text=True)
    assert count(app, WalkinSale) == 0 and get(app, Product, water).stock_pieces == 100


def test_pay_twice_records_one_sale(app, at_counter, water):
    ring(at_counter, unit_id(app, water, "Bottle"), 2)
    key = ticket_key(at_counter)
    first = post(at_counter, "/counter/pay", {"cash_given": "50", "ticket_key": key})
    ring(at_counter, unit_id(app, water, "Bottle"), 2)  # even with something on the ticket again
    second = post(at_counter, "/counter/pay", {"cash_given": "50", "ticket_key": key})
    assert first.location == second.location
    assert count(app, WalkinSale) == 1 and get(app, Product, water).stock_pieces == 98


def test_the_counter_cant_sell_pieces_held_for_online_orders(app, client, shopper_id, cashier_id, store_id):
    rice = add_product(app, store_id, "Rice", units=(("Kilo", 1, 5200, 5000),), stock=10)
    log_in_as(client, shopper_id)
    add(client, unit_id(app, rice, "Kilo"), 8)
    post(client, "/checkout", {"fulfilment": "pickup", "payment": "cash", "idempotency_key": checkout_key(client)})
    assert get(app, Product, rice).reserved_pieces == 8
    log_in_as(client, cashier_id)
    post(client, "/counter/open", {"opening_cash": "0"})
    ring(client, unit_id(app, rice, "Kilo"), 3)
    response = post(client, "/counter/pay", {"cash_given": "500", "ticket_key": ticket_key(client)},
                    follow_redirects=True)
    assert "Only 2 pieces of Rice can be sold" in response.get_data(as_text=True)
    assert get(app, Product, rice).stock_pieces == 10 and count(app, WalkinSale) == 0


def test_the_last_pieces_go_to_one_buyer_counter_or_online(app, store_id, cashier_id, shopper_id):
    """A counter sale and an online order race for the same last 3 pieces."""
    from dfjc import checkout
    from dfjc.models import CartItem
    coffee = add_product(app, store_id, "Coffee", units=(("Sachet", 1, 1200, 1050),), stock=3)
    sachet = unit_id(app, coffee, "Sachet")
    with app.app_context():
        engine = db.engine()
        with Session(engine) as s:
            s.add(CartItem(user_id=shopper_id, unit_id=sachet, quantity=3))
            shift = counter.open_shift(s, s.get(Store, store_id), s.get(User, cashier_id), 0, utcnow())
            s.commit()
            shift_id = shift.id
    online, desk = Session(engine), Session(engine)
    store_one = online.get(Store, store_id)
    checkout.place_order(online, online.get(User, shopper_id), store_one,
                         {"fulfilment": "pickup", "payment_method": "cash_pickup"}, "k1", utcnow())
    online.commit()
    with pytest.raises(counter.CounterProblem):
        counter.sell(desk, desk.get(Store, store_id), desk.get(CashShift, shift_id), desk.get(User, cashier_id),
                     [[sachet, 1, "retail"]], 10000, "k2", utcnow())
    desk.rollback()
    online.close()
    desk.close()
    product = get(app, Product, coffee)
    assert (product.stock_pieces, product.reserved_pieces) == (3, 3)


def test_a_scanned_barcode_goes_straight_onto_the_ticket(app, at_counter, store_id):
    product_id = add_product(app, store_id, "Soap", units=(("Bar", 1, 3800, 3500),), stock=10)
    with app.app_context():
        from dfjc.models import ProductUnit
        unit = db.session().get(ProductUnit, unit_id(app, product_id, "Bar"))
        unit.barcode = "4800000000017"
        db.session().commit()
    at_counter.get("/counter?q=4800000000017")
    assert "₱38.00" in at_counter.get("/counter").get_data(as_text=True)


def test_change_a_line_to_wholesale_and_remove_lines(app, at_counter, water):
    ring(at_counter, unit_id(app, water, "Case of 24"), 2)
    post(at_counter, "/counter/line/0", {"action": "wholesale"})
    assert "₱552.00" in at_counter.get("/counter").get_data(as_text=True)
    post(at_counter, "/counter/all", {"price_type": "retail"})
    assert "₱600.00" in at_counter.get("/counter").get_data(as_text=True)
    post(at_counter, "/counter/line/0", {"action": "remove"})
    assert "Nothing yet" in at_counter.get("/counter").get_data(as_text=True)


# ---------- Void ----------

def void(client, sale, reason="Wrong item", **counts):
    """counts: {line index: how many}; e.g. void(client, sale, l0=2) voids 2 of the first line."""
    form = {"reason": reason}
    for key, count in counts.items():
        form[f"void_{sale.lines[int(key[1:])].id}"] = str(count)
    return post(client, f"/counter/sales/{sale.id}/void", form, follow_redirects=True)


@pytest.fixture
def two_line_sale(app, at_counter, water):
    """4 bottles (15.00 each) and 1 case of 24 (300.00): 360.00, paid with 500."""
    ring(at_counter, unit_id(app, water, "Bottle"), 4)
    ring(at_counter, unit_id(app, water, "Case of 24"), 1)
    post(at_counter, "/counter/pay", {"cash_given": "500", "ticket_key": ticket_key(at_counter)})
    assert get(app, Product, water).stock_pieces == 100 - 4 - 24
    return last_sale(app)


def test_void_only_the_chosen_items(app, at_counter, water, two_line_sale):
    page = at_counter.get(f"/counter/sales/{two_line_sale.id}").get_data(as_text=True)
    assert "Void how many" in page and "Void chosen items" in page
    response = void(at_counter, two_line_sale, "Rang up a case instead of a bottle", l1=1)
    assert "give back 300.00 pesos" in response.get_data(as_text=True)
    assert "The rest of the sale stays: 60.00 pesos" in response.get_data(as_text=True)
    sale = last_sale(app)
    assert (sale.voided_amount, sale.kept_total, sale.voided_at) == (30000, 6000, None)
    assert [line.voided_quantity for line in sale.lines] == [0, 1]
    assert get(app, Product, water).stock_pieces == 100 - 4  # the case is back on the shelf, the bottles aren't
    receipt = at_counter.get(f"/counter/sales/{sale.id}").get_data(as_text=True)
    assert "1 x Bottled water 500 mL (Case of 24)" in receipt and "Rang up a case instead of a bottle" in receipt
    assert "₱300.00 given back</strong> for voided items. The sale is now ₱60.00" in receipt
    assert "VOIDED" not in receipt and "Void chosen items" in receipt  # the bottles can still be voided


def test_void_part_of_a_line_then_the_rest(app, at_counter, water, two_line_sale):
    void(at_counter, two_line_sale, "Two were broken", l0=2)
    sale = last_sale(app)
    assert (sale.lines[0].voided_quantity, sale.voided_amount) == (2, 3000)
    assert get(app, Product, water).stock_pieces == 100 - 2 - 24
    assert "Voided 2" in at_counter.get(f"/counter/sales/{sale.id}").get_data(as_text=True)
    void(at_counter, sale, "Customer changed their mind", l0=2, l1=1)
    sale = last_sale(app)
    assert sale.voided_at is not None and sale.voided_amount == sale.total and sale.kept_total == 0
    assert get(app, Product, water).stock_pieces == 100
    receipt = at_counter.get(f"/counter/sales/{sale.id}").get_data(as_text=True)
    assert "VOIDED" in receipt and "Void chosen items" not in receipt


def test_void_all_takes_back_everything_left(app, at_counter, water, two_line_sale):
    page = at_counter.get(f"/counter/sales/{two_line_sale.id}").get_data(as_text=True)
    assert 'name="all" value="1"' in page and "you give back ₱360.00" in page
    void(at_counter, two_line_sale, "Two were broken", l0=2)  # first a part
    page = at_counter.get(f"/counter/sales/{two_line_sale.id}").get_data(as_text=True)
    assert "you give back ₱330.00" in page  # Void all now means what is left
    response = post(at_counter, f"/counter/sales/{two_line_sale.id}/void",
                    {"reason": "Customer changed their mind", "all": "1", f"void_{two_line_sale.lines[0].id}": "0"},
                    follow_redirects=True)
    text = response.get_data(as_text=True)
    assert "give back 330.00 pesos" in text and "Nothing is left on it" in text
    sale = last_sale(app)
    assert sale.voided_at is not None and sale.voided_amount == sale.total
    assert get(app, Product, water).stock_pieces == 100
    assert "Void all" not in at_counter.get(f"/counter/sales/{sale.id}").get_data(as_text=True)


def test_void_all_still_needs_a_reason(app, at_counter, water, two_line_sale):
    response = post(at_counter, f"/counter/sales/{two_line_sale.id}/void", {"reason": "", "all": "1"},
                    follow_redirects=True)
    assert "Say why" in response.get_data(as_text=True) and last_sale(app).voided_amount == 0


def test_never_more_than_was_sold(app, at_counter, water, two_line_sale):
    response = void(at_counter, two_line_sale, l0=5)
    assert "Only 4 of Bottled water 500 mL (Bottle)" in response.get_data(as_text=True)
    void(at_counter, two_line_sale, l0=4)
    again = void(at_counter, two_line_sale, l0=1)  # pressed twice, or an old page
    assert "Only 0 of" in again.get_data(as_text=True)
    assert get(app, Product, water).stock_pieces == 100 - 24  # the 4 bottles went back once
    assert last_sale(app).voided_amount == 6000


def test_void_needs_items_and_a_reason(app, at_counter, water, two_line_sale):
    assert "Choose the items to void" in void(at_counter, two_line_sale).get_data(as_text=True)
    assert "Say why" in void(at_counter, two_line_sale, reason="", l0=1).get_data(as_text=True)
    assert last_sale(app).voided_amount == 0 and get(app, Product, water).stock_pieces == 72


def test_two_voids_at_once_never_give_back_too_much(app, at_counter, water, two_line_sale):
    """Two cashiers void the same 3 of 4 bottles at the same moment: only one void goes through."""
    from sqlalchemy.exc import OperationalError
    with app.app_context():
        engine = db.engine()
        first, second = Session(engine), Session(engine)
        try:
            cashier = first.get(User, two_line_sale.cashier_id)
            sale_a, sale_b = first.get(WalkinSale, two_line_sale.id), second.get(WalkinSale, two_line_sale.id)
            line = sale_a.lines[0].id
            counter.void(first, sale_a, cashier, utcnow(), "a", {line: 3})
            first.commit()
            with pytest.raises((counter.CounterProblem, OperationalError)):
                counter.void(second, sale_b, cashier, utcnow(), "b", {line: 3})
                second.commit()
            second.rollback()
        finally:
            first.close()
            second.close()
    assert last_sale(app).lines[0].voided_quantity == 3 and get(app, Product, water).stock_pieces == 75


# ---------- Closing the drawer ----------

def test_closing_a_shift_counts_the_drawer(app, at_counter, water):
    ring(at_counter, unit_id(app, water, "Bottle"), 2)
    post(at_counter, "/counter/pay", {"cash_given": "50", "ticket_key": ticket_key(at_counter)})
    page = at_counter.get("/counter/shift").get_data(as_text=True)
    assert "₱1,030.00" in page  # 1,000 start + 30 sales
    response = post(at_counter, "/counter/shift", {"counted_cash": "1010", "note": "Gave too much change"},
                    follow_redirects=True)
    assert "20.00 pesos less than expected" in response.get_data(as_text=True)
    with app.app_context():
        shift = db.session().scalar(select(CashShift))
    assert (shift.expected_cash, shift.counted_cash, shift.difference) == (103000, 101000, -2000)
    assert "Open My Shift" in at_counter.get("/counter").get_data(as_text=True)


def test_a_voided_sale_doesnt_count_in_the_drawer(app, at_counter, water):
    ring(at_counter, unit_id(app, water, "Bottle"), 2)
    post(at_counter, "/counter/pay", {"cash_given": "50", "ticket_key": ticket_key(at_counter)})
    void(at_counter, last_sale(app), "Oops", l0=2)
    with app.app_context():
        s = db.session()
        assert counter.summary(s, s.scalar(select(CashShift)), utcnow())["expected"] == 100000


def test_a_part_voided_sale_counts_what_was_kept(app, at_counter, water, two_line_sale):
    void(at_counter, two_line_sale, l1=1)
    with app.app_context():
        s = db.session()
        summary = counter.summary(s, s.scalar(select(CashShift)), utcnow())
    assert (summary["sales"], summary["sale_count"], summary["expected"]) == (6000, 1, 106000)
    page = at_counter.get("/counter").get_data(as_text=True)
    assert "Part voided" in page and "₱300.00 voided" in page


def test_another_stores_receipt_is_not_found(app, at_counter, water, store_id):
    ring(at_counter, unit_id(app, water, "Bottle"), 1)
    post(at_counter, "/counter/pay", {"cash_given": "20", "ticket_key": ticket_key(at_counter)})
    sale_id = last_sale(app).id
    with app.app_context():
        s = db.session()
        other = Store(name="Other", is_demo=True)
        s.add(other)
        s.commit()
        other_id = other.id
    from .conftest import make_user
    log_in_as(at_counter, make_user(app, other_id, "o@other.example", role="owner"))
    assert at_counter.get(f"/counter/sales/{sale_id}").status_code == 404


# ---------- The demo ----------

def test_the_demo_counter_has_examples(app):
    demo = app.test_client()
    with demo.session_transaction() as session:
        session["csrf"] = "x"
    post(demo, "/demo")
    post(demo, "/demo/view-as", {"role": "cashier"})
    page = demo.get("/counter").get_data(as_text=True)
    assert "This Shift" in page and "Part voided" in page and "Wholesale" in page
    shifts = demo.get("/counter/shifts").get_data(as_text=True)
    assert "Open now" in shifts
    post(demo, "/demo/view-as", {"role": "owner"})
    everyone = demo.get("/counter/shifts").get_data(as_text=True)
    assert "Lorna Cruz" in everyone and "Short 20 pesos" in everyone and "Matches" in everyone
    assert "What Should Be in the Drawer" in demo.get("/counter/shift").get_data(as_text=True)
