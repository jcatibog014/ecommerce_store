"""Shopper extras (favorites, Buy again, addresses), voucher codes, and order emails through the outbox."""
import threading
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dfjc import checkout, db, mailer, notices
from dfjc.dates import local_today, utcnow
from dfjc.models import (
    Address, CartItem, DeliveryArea, Favorite, Notification, Order, Product, Store, User, Voucher, VoucherUse,
)

from .conftest import add_product, get, log_in_as, make_user, post
from .test_orders import add, checkout_key, only_order, unit_id


def count(app, model, *where):
    with app.app_context():
        return db.session().scalar(select(func.count()).select_from(model).where(*where))


@pytest.fixture
def water(app, store_id):
    return add_product(app, store_id, "Bottled water 500 mL",
                       units=(("Bottle", 1, 1500, 1300), ("Case of 24", 24, 30000, 27600)), stock=500)


def make_voucher(app, store_id, code="SUKI50", kind="amount", value=5000, **fields):
    with app.app_context():
        voucher = Voucher(store_id=store_id, code=code, kind=kind, value=value, **fields)
        db.session().add(voucher)
        db.session().commit()
        return voucher.id


def place(client, **form):
    data = {"fulfilment": "pickup", "payment": "cash", "idempotency_key": checkout_key(client), **form}
    return post(client, "/checkout", data)


# ---------- Favorites ----------

def test_save_and_remove_a_favorite(app, shopper, water):
    post(shopper, f"/favorites/{water}")
    assert count(app, Favorite) == 1
    assert "Saved to favorites" in shopper.get(f"/products/{water}").get_data(as_text=True)
    assert "Bottled water 500 mL" in shopper.get("/favorites").get_data(as_text=True)
    post(shopper, f"/favorites/{water}")
    assert count(app, Favorite) == 0
    assert "No Favorites Yet" in shopper.get("/favorites").get_data(as_text=True)


def test_another_stores_product_cant_be_a_favorite(app, shopper):
    with app.app_context():
        s = db.session()
        other = Store(name="Other", is_demo=True)
        s.add(other)
        s.commit()
        other_id = other.id
    product_id = add_product(app, other_id, "Not here")
    assert post(shopper, f"/favorites/{product_id}").status_code == 404


# ---------- Buy again ----------

def test_buy_again_fills_the_cart_with_what_is_still_sold(app, shopper, water, store_id):
    soap = add_product(app, store_id, "Soap", units=(("Bar", 1, 3800, 3500),), stock=10)
    add(shopper, unit_id(app, water, "Bottle"), 3)
    add(shopper, unit_id(app, soap, "Bar"), 2)
    place(shopper)
    order = only_order(app)
    post(shopper, f"/orders/{order.ref}/cancel")
    with app.app_context():
        db.session().get(Product, soap).is_active = False
        db.session().commit()
    response = post(shopper, f"/orders/{order.ref}/buy-again", follow_redirects=True)
    page = response.get_data(as_text=True)
    assert "Added 1 item" in page and "Not sold any more: Soap" in page
    with app.app_context():
        assert db.session().scalar(select(CartItem.quantity)) == 3


def test_buy_again_is_only_for_my_orders(app, client, store_id, shopper, water):
    add(shopper, unit_id(app, water, "Bottle"))
    place(shopper)
    log_in_as(client, make_user(app, store_id, "maria@example.com"))
    assert post(client, f"/orders/{only_order(app).ref}/buy-again").status_code == 404


# ---------- Addresses ----------

def test_addresses(app, shopper, store_id):
    with app.app_context():
        area = DeliveryArea(store_id=store_id, name="Poblacion", fee=3000)
        db.session().add(area)
        db.session().commit()
        area_id = area.id
    post(shopper, "/addresses", {"label": "Home", "area_id": str(area_id), "street": "12 Luna Street"})
    with app.app_context():
        address = db.session().scalar(select(Address))
        assert address.one_line == "12 Luna Street, Poblacion"
    post(shopper, f"/addresses/{address.id}", {"label": "Work", "area_id": str(area_id), "street": "1 Rizal Ave"})
    assert get(app, Address, address.id).label == "Work"
    assert post(shopper, "/addresses", {"area_id": "", "street": ""}).status_code == 400
    post(shopper, f"/addresses/{address.id}/delete")
    assert count(app, Address) == 0


def test_someone_elses_address_is_not_found(app, client, store_id, shopper_id):
    with app.app_context():
        s = db.session()
        address = Address(user_id=shopper_id, street="Mine")
        s.add(address)
        s.commit()
        address_id = address.id
    log_in_as(client, make_user(app, store_id, "maria@example.com"))
    assert post(client, f"/addresses/{address_id}/delete").status_code == 404


# ---------- Vouchers ----------

def test_apply_shows_the_discount_and_ordering_uses_the_code(app, shopper, store_id, water):
    voucher_id = make_voucher(app, store_id, min_spend=100000)
    add(shopper, unit_id(app, water, "Case of 24"), 4)  # 1,200 pesos
    key = checkout_key(shopper)
    preview = post(shopper, "/checkout", {"fulfilment": "pickup", "payment": "cash", "idempotency_key": key,
                                          "voucher": " suki50 ", "action": "apply"})
    page = preview.get_data(as_text=True)
    assert "SUKI50: 50 pesos off on orders of 1,000 pesos or more" in page and "₱1,150.00" in page
    assert count(app, Order) == 0  # Apply never orders
    post(shopper, "/checkout", {"fulfilment": "pickup", "payment": "cash", "idempotency_key": key, "voucher": "SUKI50"})
    order = only_order(app)
    assert (order.discount, order.total, order.voucher_code) == (5000, 115000, "SUKI50")
    assert get(app, Voucher, voucher_id).used_count == 1 and count(app, VoucherUse) == 1
    assert "Voucher SUKI50" in shopper.get(f"/orders/{order.ref}").get_data(as_text=True)


@pytest.mark.parametrize("fields, words", [
    ({"min_spend": 500000}, "for orders of 5,000.00 pesos or more"),
    ({"is_active": False}, "doesn&#39;t work"),
    ({"max_uses": 3, "used_count": 3}, "used up"),
    ({"ends_on": "past"}, "has ended"),
    ({"starts_on": "future"}, "That code starts on"),
])
def test_a_code_that_cant_be_used_says_why(app, shopper, store_id, water, fields, words):
    today = local_today()
    fields = {key: (today - timedelta(days=1) if value == "past" else today + timedelta(days=5) if value == "future"
                    else value) for key, value in fields.items()}
    make_voucher(app, store_id, **fields)
    add(shopper, unit_id(app, water, "Case of 24"), 1)
    response = place(shopper, voucher="SUKI50")
    assert response.status_code == 400 and words in response.get_data(as_text=True)
    assert count(app, Order) == 0


def test_each_account_uses_a_code_once(app, shopper, store_id, water):
    make_voucher(app, store_id)
    add(shopper, unit_id(app, water, "Case of 24"), 1)
    place(shopper, voucher="SUKI50")
    add(shopper, unit_id(app, water, "Case of 24"), 1)
    response = place(shopper, voucher="SUKI50")
    assert "You already used that code." in response.get_data(as_text=True)
    assert count(app, Order) == 1


def test_cancelling_gives_the_code_back(app, shopper, store_id, water):
    voucher_id = make_voucher(app, store_id, max_uses=1)
    add(shopper, unit_id(app, water, "Case of 24"), 1)
    place(shopper, voucher="SUKI50")
    post(shopper, f"/orders/{only_order(app).ref}/cancel")
    assert get(app, Voucher, voucher_id).used_count == 0 and count(app, VoucherUse) == 0
    add(shopper, unit_id(app, water, "Case of 24"), 1)
    place(shopper, voucher="SUKI50")  # usable again
    assert get(app, Voucher, voucher_id).used_count == 1


def test_a_percent_code_follows_the_wholesale_price(app, shopper, cashier_id, store_id, water):
    make_voucher(app, store_id, code="PASKO10", kind="percent", value=10)
    add(shopper, unit_id(app, water, "Case of 24"), 2)  # 600 retail, 552 wholesale
    place(shopper, voucher="PASKO10")
    order = only_order(app)
    assert (order.discount, order.total) == (6000, 54000)
    log_in_as(shopper, cashier_id)
    post(shopper, f"/manage/orders/{order.ref}/price",
         {"version": str(order.version), **{f"line_{line.id}": "wholesale" for line in order.lines}})
    order = only_order(app)
    assert (order.subtotal, order.discount, order.total) == (55200, 5520, 49680)


def test_free_delivery(app, shopper, store_id, water):
    make_voucher(app, store_id, code="LIBRENGHATID", kind="delivery", value=0)
    with app.app_context():
        area = DeliveryArea(store_id=store_id, name="Poblacion", fee=3000)
        db.session().add(area)
        db.session().commit()
        area_id = area.id
    add(shopper, unit_id(app, water, "Bottle"), 4)
    place(shopper, fulfilment="delivery", address_id="new", area_id=str(area_id), street="12 Luna",
          voucher="librenghatid")
    order = only_order(app)
    assert (order.delivery_fee, order.discount, order.total) == (3000, 3000, 6000)


def test_the_last_uses_of_a_code_go_to_one_shopper_each(app, store_id, water):
    """Five shoppers order with a code that has 2 uses left: exactly 2 orders get it."""
    voucher_id = make_voucher(app, store_id, max_uses=2)
    case = unit_id(app, water, "Case of 24")
    users = [make_user(app, store_id, f"racer{number}@example.com") for number in range(5)]
    with app.app_context():
        engine = db.engine()
        with Session(engine) as s:
            for user_id in users:
                s.add(CartItem(user_id=user_id, unit_id=case, quantity=1))
            s.commit()
    start, results = threading.Barrier(len(users)), []

    def shop(user_id):
        from dfjc import vouchers
        with Session(engine, expire_on_commit=False) as s:
            user, store, voucher = s.get(User, user_id), s.get(Store, store_id), s.get(Voucher, voucher_id)
            start.wait()
            try:
                checkout.place_order(s, user, store, {"fulfilment": "pickup", "payment_method": "cash_pickup",
                                                      "voucher": voucher}, f"key-{user_id}", utcnow())
                s.commit()
                results.append("got it")
            except vouchers.VoucherProblem:
                s.rollback()
                results.append("used up")

    threads = [threading.Thread(target=shop, args=(user_id,)) for user_id in users]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["got it"] * 2 + ["used up"] * 3
    assert get(app, Voucher, voucher_id).used_count == 2 and count(app, Order) == 2


def test_the_owner_makes_and_switches_off_promos(app, owner, store_id):
    response = post(owner, "/manage/promos", {"code": "suki50", "kind": "amount", "value": "50", "min_spend": "1000",
                                             "max_uses": "100", "description": "For suki", "show_on_home": "1"})
    assert response.status_code == 302
    with app.app_context():
        voucher = db.session().scalar(select(Voucher))
    assert (voucher.code, voucher.value, voucher.min_spend, voucher.max_uses) == ("SUKI50", 5000, 100000, 100)
    assert "SUKI50" in owner.get("/").get_data(as_text=True)  # on the home page
    assert post(owner, "/manage/promos", {"code": "SUKI50", "kind": "amount", "value": "20"}).status_code == 400
    post(owner, f"/manage/promos/{voucher.id}/active", {"active": "0"})
    assert not get(app, Voucher, voucher.id).is_active
    owner.get("/manage/promos")  # (shows the message about switching it off)
    assert 'promo-code">SUKI50' not in owner.get("/").get_data(as_text=True)


def test_only_the_owner_makes_promos(client, cashier_id):
    log_in_as(client, cashier_id)
    assert client.get("/manage/promos").status_code == 403


# ---------- Order emails ----------

def test_placing_and_pricing_send_emails(app, shopper, cashier_id, water):
    add(shopper, unit_id(app, water, "Bottle"), 2)
    place(shopper)
    sent = mailer.outbox(app)
    assert sent[-1]["To"] == "juan@example.com" and "We got your order" in sent[-1]["Subject"]
    order = only_order(app)
    log_in_as(shopper, cashier_id)
    post(shopper, f"/manage/orders/{order.ref}/price", {"version": str(order.version)})
    assert "Order " + order.ref + ": ₱30.00" in mailer.outbox(app)[-1]["Subject"]
    assert f"/orders/{order.ref}" in mailer.outbox(app)[-1].get_content()
    assert count(app, Notification, Notification.status == "sent") == 2


def test_no_emails_when_turned_off(app, shopper, shopper_id, water):
    post(shopper, "/settings/", {"action": "emails"})  # the box unticked
    assert not get(app, User, shopper_id).order_emails
    add(shopper, unit_id(app, water, "Bottle"))
    place(shopper)
    assert mailer.outbox(app) == [] and count(app, Notification) == 0


def test_no_email_for_a_change_that_rolled_back(app, shopper, water):
    add(shopper, unit_id(app, water, "Bottle"), 2)
    key = checkout_key(shopper)
    with app.app_context():
        db.session().get(Product, water).stock_pieces = 1
        db.session().commit()
    post(shopper, "/checkout", {"fulfilment": "pickup", "payment": "cash", "idempotency_key": key})
    assert count(app, Order) == 0 and count(app, Notification) == 0 and mailer.outbox(app) == []


def test_a_failed_email_never_stops_the_order(app, shopper, water, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("Gmail is down")
    monkeypatch.setattr(mailer, "send", broken)
    add(shopper, unit_id(app, water, "Bottle"))
    response = place(shopper)
    assert response.status_code == 302 and count(app, Order) == 1
    with app.app_context():
        notice = db.session().scalar(select(Notification))
    assert (notice.status, notice.tries, notice.error) == ("failed", 1, "Gmail is down")
    monkeypatch.undo()
    with app.app_context():
        notices.deliver(db.session(), utcnow())  # tried again later
    assert get(app, Notification, notice.id).status == "sent"


def test_the_send_emails_command(app, store_id):
    result = app.test_cli_runner().invoke(args=["send-emails"])
    assert result.exit_code == 0 and "Tried 0 emails" in result.output


def test_demo_stores_send_no_email(app):
    demo = app.test_client()
    with demo.session_transaction() as session:
        session["csrf"] = "x"
    post(demo, "/demo")
    assert count(app, Notification) == 0 and mailer.outbox(app) == []


# ---------- The demo ----------

def test_the_demo_has_favorites_promos_and_addresses(app):
    demo = app.test_client()
    with demo.session_transaction() as session:
        session["csrf"] = "x"
    post(demo, "/demo")
    home = demo.get("/").get_data(as_text=True)
    assert "Promos" in home and "SUKI50" in home and "LIBRENGHATID" in home and "PASKO10" not in home
    assert "3-in-1 coffee sachet" in demo.get("/favorites").get_data(as_text=True)
    assert "45 Mabini Street" in demo.get("/addresses").get_data(as_text=True)
    assert "Voucher LIBRENGHATID" in demo.get("/orders/" + demo_ref(app, "LIBRENGHATID")).get_data(as_text=True)
    post(demo, "/demo/view-as", {"role": "owner"})
    promos = demo.get("/manage/promos").get_data(as_text=True)
    for words in ("SUKI50", "PASKO10", "BAGONGTAON", "Ended"):
        assert words in promos


def demo_ref(app, code):
    with app.app_context():
        return db.session().scalar(select(Order.ref).where(Order.voucher_code == code).order_by(Order.id.desc()))
