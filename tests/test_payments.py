"""Paying online: the Sandbox, PayMongo (with a fake connection), webhooks, late payments and refunds."""
import json
import re
import time
from datetime import timedelta

import pytest
import requests
from sqlalchemy import func, select

from dfjc import db, gateways, payments
from dfjc.dates import utcnow
from dfjc.gateways import GatewayError, PayMongo, sign_webhook
from dfjc.models import (
    AWAITING_PAYMENT, CANCELLED, CONFIRMED, PAID, REFUNDED, Order, OrderEvent, Payment, PaymentEvent, Product, Refund,
    User,
)

from .conftest import add_product, get, log_in_as, make_user, post
from .test_orders import add, checkout_key, only_order, unit_id

WEBHOOK_SECRET = "whsk_test_secret"


# ---------- Helpers ----------

def count(app, model, *where):
    with app.app_context():
        return db.session().scalar(select(func.count()).select_from(model).where(*where))


def order_now(app):
    with app.app_context():
        order = db.session().scalar(select(Order))
        _ = order.lines, order.events, [payment.refunds for payment in order.payments]
        return order


def payment_now(app):
    with app.app_context():
        return db.session().scalar(select(Payment).order_by(Payment.id.desc()))


def priced_online_order(app, shopper, cashier_id, product_id, quantity=2):
    """A shopper orders online for pickup and the cashier prices it: it waits for payment."""
    add(shopper, unit_id(app, product_id, "Bottle"), quantity)
    post(shopper, "/checkout", {"fulfilment": "pickup", "payment": "online", "idempotency_key": checkout_key(shopper)})
    order = only_order(app)
    with app.app_context():
        from dfjc import orders
        s = db.session()
        loaded = s.get(Order, order.id)
        orders.price_order(s, loaded, {}, s.get(User, cashier_id), utcnow(), loaded.version)
        s.commit()
    return order_now(app)


@pytest.fixture
def water(app, store_id):
    return add_product(app, store_id, "Bottled water 500 mL", units=(("Bottle", 1, 1500, 1300),), stock=100)


@pytest.fixture
def waiting(app, shopper, cashier_id, water):
    return priced_online_order(app, shopper, cashier_id, water)


def press_pay(client, order):
    return post(client, f"/orders/{order.ref}/pay")


# ---------- Choosing to pay online ----------

def test_an_online_order_waits_for_payment_after_pricing(app, shopper, waiting):
    assert (waiting.payment_method, waiting.status, waiting.total) == ("online", AWAITING_PAYMENT, 3000)
    page = shopper.get(f"/orders/{waiting.ref}").get_data(as_text=True)
    assert "Please Pay Now" in page and "Pay ₱30.00" in page


def test_online_is_off_on_the_live_site_without_keys(app, shopper, water):
    app.config["ON_RENDER"] = True
    add(shopper, unit_id(app, water, "Bottle"), 2)
    page = shopper.get("/checkout").get_data(as_text=True)
    assert 'value="online"' in page and "Not available yet." in page
    response = post(shopper, "/checkout", {"fulfilment": "pickup", "payment": "online",
                                           "idempotency_key": checkout_key(shopper)})
    assert response.status_code == 400 and count(app, Order) == 0


def test_tiny_orders_pay_in_cash(app, shopper, cashier_id, water):
    order = priced_online_order(app, shopper, cashier_id, water, quantity=1)  # ₱15
    assert "Online payment starts at ₱20" in shopper.get(f"/orders/{order.ref}").get_data(as_text=True)
    press_pay(shopper, order)
    assert count(app, Payment) == 0


# ---------- The Sandbox, start to finish ----------

def test_pay_with_the_sandbox(app, shopper, waiting):
    response = press_pay(shopper, waiting)
    payment = payment_now(app)
    assert response.location == f"/sandbox/pay/{payment.session_id}"
    assert (payment.status, payment.amount, payment.gateway) == ("pending", 3000, "sandbox")
    assert "No real money moves" in shopper.get(response.location).get_data(as_text=True)
    done = post(shopper, response.location, {"action": "pay", "method": "card"})
    assert done.location == f"/orders/{waiting.ref}/payments/{payment.id}/done"
    page = shopper.get(done.location, follow_redirects=True).get_data(as_text=True)
    assert "Thank you!" in page and "Paid: ₱30.00" in page and "with card" in page
    order = order_now(app)
    assert order.status == PAID
    assert payment_now(app).status == "paid" and payment_now(app).paid_with == "card"
    assert [event.to_status for event in order.events][-1] == PAID


def test_pressing_pay_twice_goes_back_to_the_same_page(app, shopper, waiting):
    first = press_pay(shopper, waiting).location
    second = press_pay(shopper, waiting).location
    assert first == second and count(app, Payment) == 1


def test_the_return_page_twice_pays_once(app, shopper, waiting):
    sandbox = press_pay(shopper, waiting).location
    done = post(shopper, sandbox, {"action": "pay"}).location
    shopper.get(done)
    shopper.get(done)
    post(shopper, done)  # and the Check payment button
    assert count(app, OrderEvent, OrderEvent.to_status == PAID) == 1


def test_a_failed_payment_can_be_tried_again(app, shopper, waiting):
    sandbox = press_pay(shopper, waiting).location
    response = post(shopper, sandbox, {"action": "fail"}, follow_redirects=True)
    assert "practice failure" in response.get_data(as_text=True)
    assert "no money was taken" in shopper.get(f"/orders/{waiting.ref}").get_data(as_text=True)
    assert payment_now(app).status == "failed" and order_now(app).status == AWAITING_PAYMENT
    again = press_pay(shopper, waiting).location
    assert again != sandbox and count(app, Payment) == 2


def test_an_old_payment_page_expires_and_a_new_one_starts(app, shopper, waiting):
    press_pay(shopper, waiting)
    with app.app_context():
        payment = db.session().scalar(select(Payment))
        payment.expires_at = utcnow() - timedelta(minutes=1)
        db.session().commit()
    press_pay(shopper, waiting)
    with app.app_context():
        statuses = db.session().scalars(select(Payment.status).order_by(Payment.id)).all()
    assert statuses == ["expired", "pending"]


def test_leaving_the_payment_page_pays_nothing(app, shopper, waiting):
    sandbox = press_pay(shopper, waiting).location
    response = post(shopper, sandbox, {"action": "cancel"}, follow_redirects=True)
    assert "nothing was paid" in response.get_data(as_text=True)
    assert order_now(app).status == AWAITING_PAYMENT


def test_switching_between_cash_and_online(app, shopper, waiting):
    press_pay(shopper, waiting)
    post(shopper, f"/orders/{waiting.ref}/pay-cash")
    order = order_now(app)
    assert (order.status, order.payment_method) == (CONFIRMED, "cash_pickup")
    assert payment_now(app).status == "expired"  # the open payment page was closed
    post(shopper, f"/orders/{waiting.ref}/pay-online")
    order = order_now(app)
    assert (order.status, order.payment_method) == (AWAITING_PAYMENT, "online")


def test_a_payment_that_comes_after_a_cancel_is_flagged_for_a_refund(app, client, shopper, shopper_id, cashier_id,
                                                                     waiting, water):
    sandbox = press_pay(shopper, waiting).location
    session_id = sandbox.rsplit("/", 1)[1]
    post(shopper, f"/orders/{waiting.ref}/cancel")
    assert order_now(app).status == CANCELLED
    # The shopper had the page open in another tab and paid anyway: the event still arrives.
    with app.app_context():
        result = payments.handle_event(db.session(), "evt_late", payments.PAID_EVENT, session_id, "pay_late", "gcash",
                                       utcnow())
        db.session().commit()
    assert result == "needs_refund"
    payment = payment_now(app)
    assert payment.status == "paid" and payment.needs_refund
    assert order_now(app).status == CANCELLED  # a cancelled order never comes back
    assert "The store will send it back" in shopper.get(f"/orders/{waiting.ref}").get_data(as_text=True)
    # The cashier gives it back.
    log_in_as(client, cashier_id)
    page = client.get(f"/manage/orders/{waiting.ref}").get_data(as_text=True)
    assert "came in after the order changed" in page and "Give the Money Back" in page
    post(client, f"/manage/orders/{waiting.ref}/refund", {"reason": "Paid after cancelling"})
    payment = payment_now(app)
    assert (payment.status, payment.refunded_amount, payment.needs_refund) == ("refunded", 3000, False)
    assert order_now(app).status == CANCELLED
    assert count(app, OrderEvent, OrderEvent.to_status == REFUNDED) == 1


# ---------- Refunds ----------

def paid(app, shopper, waiting):
    sandbox = press_pay(shopper, waiting).location
    post(shopper, sandbox, {"action": "pay"})
    assert order_now(app).status == PAID


def test_cancel_and_refund_a_paid_order(app, client, shopper, shopper_id, cashier_id, waiting, water):
    paid(app, shopper, waiting)
    assert get(app, Product, water).reserved_pieces == 2
    log_in_as(client, cashier_id)
    assert "Cancel and Refund" in client.get(f"/manage/orders/{waiting.ref}").get_data(as_text=True)
    response = post(client, f"/manage/orders/{waiting.ref}/refund", {"reason": "Sorry, we ran out."})
    assert response.status_code == 302
    order = order_now(app)
    assert order.status == REFUNDED and order.events[-1].note == "Sorry, we ran out."
    assert get(app, Product, water).reserved_pieces == 0
    with app.app_context():
        refund = db.session().scalar(select(Refund))
    assert (refund.status, refund.amount) == ("done", 3000) and refund.gateway_refund_id.startswith("sbx_ref_")
    log_in_as(client, shopper_id)
    assert "Cancelled and Refunded" in client.get(f"/orders/{waiting.ref}").get_data(as_text=True)


def test_a_refund_the_gateway_refuses_changes_nothing_but_is_on_record(app, client, shopper, cashier_id, waiting):
    paid(app, shopper, waiting)
    app.config["SANDBOX_REFUNDS_FAIL"] = True
    log_in_as(client, cashier_id)
    response = post(client, f"/manage/orders/{waiting.ref}/refund", {"reason": "Ran out"}, follow_redirects=True)
    assert "The refund wasn&#39;t sent" in response.get_data(as_text=True)
    assert order_now(app).status == PAID and payment_now(app).status == "paid"
    with app.app_context():
        refund = db.session().scalar(select(Refund))
    assert refund.status == "failed" and "refused" in refund.error


def test_a_refund_needs_a_reason_and_a_cashier(app, client, shopper, rider_id, cashier_id, waiting):
    paid(app, shopper, waiting)
    log_in_as(client, rider_id)
    assert post(client, f"/manage/orders/{waiting.ref}/refund", {"reason": "x"}).status_code == 403
    log_in_as(client, cashier_id)
    post(client, f"/manage/orders/{waiting.ref}/refund", {"reason": ""})
    assert order_now(app).status == PAID


def test_a_cash_order_has_nothing_to_refund(app, client, cashier_id, shopper, water):
    add(shopper, unit_id(app, water, "Bottle"), 2)
    post(shopper, "/checkout", {"fulfilment": "pickup", "payment": "cash", "idempotency_key": checkout_key(shopper)})
    order = only_order(app)
    log_in_as(client, cashier_id)
    response = post(client, f"/manage/orders/{order.ref}/refund", {"reason": "x"}, follow_redirects=True)
    assert "no online payment" in response.get_data(as_text=True)


# ---------- Privacy ----------

def test_another_shopper_cant_touch_my_payment(app, client, store_id, shopper, waiting):
    sandbox = press_pay(shopper, waiting).location
    payment = payment_now(app)
    log_in_as(client, make_user(app, store_id, "maria@example.com"))
    assert client.get(sandbox).status_code == 404
    assert post(client, sandbox, {"action": "pay"}).status_code == 404
    assert client.get(f"/orders/{waiting.ref}/payments/{payment.id}/done").status_code == 404
    assert post(client, f"/orders/{waiting.ref}/pay").status_code == 404


# ---------- PayMongo, with a fake connection ----------

class FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        self.content = json.dumps(data).encode()

    def json(self):
        return self._data


class FakePayMongo:
    """Answers like PayMongo's API, and remembers every call."""

    def __init__(self):
        self.calls = []
        self.session_status = "active"
        self.paid = False
        self.fail_with = None

    def request(self, method, url, json=None, auth=None, timeout=None):
        self.calls.append((method, url.replace(gateways.PAYMONGO_API, ""), json, auth))
        if self.fail_with == "network":
            raise requests.ConnectionError("no internet")
        if self.fail_with:
            return FakeResponse(400, {"errors": [{"detail": self.fail_with}]})
        path = url.replace(gateways.PAYMONGO_API, "")
        if method == "POST" and path == "/checkout_sessions":
            return FakeResponse(200, {"data": {"id": "cs_test_123", "attributes": {
                "checkout_url": "https://checkout.paymongo.com/cs_test_123"}}})
        if method == "GET" and path.startswith("/checkout_sessions/"):
            payments_list = [{"id": "pay_test_9", "attributes": {"status": "paid", "source": {"type": "gcash"}}}] \
                if self.paid else []
            return FakeResponse(200, {"data": {"id": "cs_test_123", "attributes": {
                "status": self.session_status, "payments": payments_list}}})
        if method == "POST" and path.endswith("/expire"):
            return FakeResponse(200, {"data": {"id": "cs_test_123"}})
        if method == "POST" and path == "/refunds":
            return FakeResponse(200, {"data": {"id": "ref_test_7"}})
        return FakeResponse(404, {"errors": [{"detail": "not found"}]})


@pytest.fixture
def paymongo(app):
    fake = FakePayMongo()
    app.config.update(PAYMONGO_SECRET_KEY="sk_test_key", PAYMONGO_WEBHOOK_SECRET=WEBHOOK_SECRET,
                      PAYMONGO_FACTORY=lambda key, secret: PayMongo(key, secret, http=fake))
    return fake


def test_pay_sends_the_shopper_to_paymongo(app, shopper, paymongo, waiting):
    response = press_pay(shopper, waiting)
    assert response.location == "https://checkout.paymongo.com/cs_test_123"
    method, path, body, auth = paymongo.calls[0]
    attributes = body["data"]["attributes"]
    assert (method, path, auth) == ("POST", "/checkout_sessions", ("sk_test_key", ""))
    assert attributes["line_items"] == [{"currency": "PHP", "amount": 3000, "name": f"DFJC Store order {waiting.ref}",
                                         "quantity": 1}]
    assert attributes["reference_number"] == waiting.ref
    assert attributes["success_url"].endswith(f"/orders/{waiting.ref}/payments/{payment_now(app).id}/done")
    assert set(attributes["payment_method_types"]) >= {"gcash", "paymaya", "card"}
    assert payment_now(app).session_id == "cs_test_123"


def test_paymongo_being_down_starts_nothing(app, shopper, paymongo, waiting):
    paymongo.fail_with = "network"
    response = post(shopper, f"/orders/{waiting.ref}/pay", follow_redirects=True)
    assert "couldn&#39;t reach PayMongo" in response.get_data(as_text=True)
    assert count(app, Payment) == 0


def test_the_return_page_asks_paymongo(app, shopper, paymongo, waiting):
    press_pay(shopper, waiting)
    payment = payment_now(app)
    done = f"/orders/{waiting.ref}/payments/{payment.id}/done"
    response = shopper.get(done, follow_redirects=True)  # back too early: not paid yet
    assert "haven&#39;t received the payment yet" in response.get_data(as_text=True)
    paymongo.paid = True
    shopper.get(done)
    assert order_now(app).status == PAID and payment_now(app).gateway_payment_id == "pay_test_9"


def test_paymongo_refund(app, client, shopper, cashier_id, paymongo, waiting):
    press_pay(shopper, waiting)
    paymongo.paid = True
    shopper.get(f"/orders/{waiting.ref}/payments/{payment_now(app).id}/done")
    log_in_as(client, cashier_id)
    post(client, f"/manage/orders/{waiting.ref}/refund", {"reason": "Out of stock"})
    method, path, body, _ = paymongo.calls[-1]
    assert (method, path) == ("POST", "/refunds")
    assert body["data"]["attributes"]["amount"] == 3000 and body["data"]["attributes"]["payment_id"] == "pay_test_9"
    assert order_now(app).status == REFUNDED


def test_paymongo_errors_are_readable():
    fake = FakePayMongo()
    fake.fail_with = "amount is below the minimum"
    with pytest.raises(GatewayError, match="amount is below the minimum"):
        PayMongo("sk", http=fake).refund("pay_1", 100, "x")


# ---------- Webhooks ----------

def event_body(session_id, event_id="evt_test_1", event_type=payments.PAID_EVENT):
    return json.dumps({"data": {"id": event_id, "type": "event", "attributes": {
        "type": event_type, "livemode": False, "data": {"id": session_id, "type": "checkout_session", "attributes": {
            "payments": [{"id": "pay_hook_1", "attributes": {"status": "paid", "source": {"type": "paymaya"}}}]}}}}}).encode()


def send_webhook(client, body, secret=WEBHOOK_SECRET, stamp=None):
    header = sign_webhook(secret, body, stamp or int(time.time()))
    # No login and no form token: PayMongo's signature is the proof.
    return client.post("/payments/webhook", data=body, headers={"Paymongo-Signature": header},
                       content_type="application/json")


def test_a_signed_webhook_marks_the_order_paid(app, client, shopper, paymongo, waiting):
    press_pay(shopper, waiting)
    response = send_webhook(app.test_client(), event_body("cs_test_123"))
    assert response.status_code == 200 and response.json["result"] == "paid"
    order = order_now(app)
    assert order.status == PAID and payment_now(app).paid_with == "paymaya"


def test_the_same_webhook_twice_changes_nothing(app, shopper, paymongo, waiting):
    press_pay(shopper, waiting)
    hook = app.test_client()
    body = event_body("cs_test_123")
    send_webhook(hook, body)
    again = send_webhook(hook, body)
    assert again.status_code == 200 and again.json["result"] == "duplicate"
    assert count(app, PaymentEvent) == 1 and count(app, OrderEvent, OrderEvent.to_status == PAID) == 1


def test_a_webhook_after_the_return_page_changes_nothing(app, shopper, paymongo, waiting):
    press_pay(shopper, waiting)
    paymongo.paid = True
    shopper.get(f"/orders/{waiting.ref}/payments/{payment_now(app).id}/done")
    response = send_webhook(app.test_client(), event_body("cs_test_123"))
    assert response.json["result"] == "already"
    assert count(app, OrderEvent, OrderEvent.to_status == PAID) == 1


def test_a_webhook_with_a_bad_or_old_signature_is_refused(app, shopper, paymongo, waiting):
    press_pay(shopper, waiting)
    hook = app.test_client()
    assert send_webhook(hook, event_body("cs_test_123"), secret="whsk_wrong").status_code == 400
    assert send_webhook(hook, event_body("cs_test_123"), stamp=int(time.time()) - 3600).status_code == 400
    assert hook.post("/payments/webhook", data=event_body("cs_test_123")).status_code == 400  # no header
    assert order_now(app).status == AWAITING_PAYMENT and count(app, PaymentEvent) == 0


def test_a_webhook_for_a_session_we_dont_know_is_noted(app, paymongo, store_id):
    response = send_webhook(app.test_client(), event_body("cs_someone_else"))
    assert response.status_code == 200 and response.json["result"] == "unknown session"


def test_a_webhook_that_isnt_json_is_refused(app, paymongo, store_id):
    assert send_webhook(app.test_client(), b"not json").status_code == 400


def test_the_signature_check_itself():
    gateway = PayMongo("sk", WEBHOOK_SECRET)
    body = b'{"data": {}}'
    now = 1_800_000_000
    assert gateway.verify_webhook(body, sign_webhook(WEBHOOK_SECRET, body, now), now=now)
    assert not gateway.verify_webhook(body + b" ", sign_webhook(WEBHOOK_SECRET, body, now), now=now)
    assert not gateway.verify_webhook(body, f"t={now},te=,li=", now=now)
    assert not PayMongo("sk", "").verify_webhook(body, sign_webhook("", body, now), now=now)


def test_the_page_may_send_forms_to_paymongo(client, store_id):
    policy = client.get("/login").headers["Content-Security-Policy"]
    assert "form-action 'self' https://checkout.paymongo.com" in policy


# ---------- The demo ----------

def test_the_demo_shows_every_kind_of_payment(app):
    demo = app.test_client()
    with demo.session_transaction() as session:
        session["csrf"] = "x"
    post(demo, "/demo")
    post(demo, "/demo/view-as", {"role": "cashier"})
    assert "Ana Reyes" in demo.get("/manage/orders?tab=waiting").get_data(as_text=True)
    cancelled = demo.get("/manage/orders?tab=cancelled").get_data(as_text=True)
    assert "Refunded" in cancelled and "Ramon Garcia" in cancelled
    assert "Paid online" in demo.get("/manage/packing").get_data(as_text=True)


def test_a_failed_payment_says_what_it_means(app):
    """The demo's waiting order failed once on purpose: the cashier reads what that means."""
    import re
    demo = app.test_client()
    with demo.session_transaction() as session:
        session["csrf"] = "x"
    post(demo, "/demo")
    post(demo, "/demo/view-as", {"role": "cashier"})
    queue = demo.get("/manage/orders?tab=waiting").get_data(as_text=True)
    ref = re.search(r'/manage/orders/(DF-[A-Z0-9]+)"', queue).group(1)
    page = demo.get(f"/manage/orders/{ref}").get_data(as_text=True)
    assert ">Not paid<" in page and "No money was taken" in page and "practice payment, no real money" in page
    assert "go through" not in page
