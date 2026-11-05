"""Payment gateways: PayMongo (test mode) and the built-in Sandbox. Both have the same small methods:

    create_checkout(...)       -> (session id, address of the payment page)
    checkout_status(session)   -> ("paid", payment id, paid with) | ("unpaid", "", "") | ("expired", "", "")
    expire(session)            -> stop a session that wasn't paid (best effort)
    refund(payment id, amount) -> refund id, or raises GatewayError
    verify_webhook(body, signature header) -> True only if PayMongo signed it

payments.py only talks to these methods, so the rest of the app never knows
which gateway it is using. The Sandbox runs the demo and every test with no
keys and no internet; its "payment page" is /sandbox/pay/<session>.

PayMongo's API: https://developers.paymongo.com (Checkout Sessions, Refunds, Webhooks).
Keys come from environment variables and are never in the repository.
"""
import hashlib
import hmac
import secrets
import time

import requests
from flask import current_app, url_for

PAYMONGO_API = "https://api.paymongo.com/v1"
# PayMongo refuses smaller amounts, so orders under this are paid in cash.
ONLINE_MINIMUM = 2000  # centavos (₱20)
WEBHOOK_TOLERANCE = 5 * 60  # seconds: an older signed message is a replay
PAYMENT_TYPES = ["gcash", "paymaya", "card", "grab_pay"]


class GatewayError(Exception):
    """The gateway said no, or couldn't be reached. The message is safe to show staff."""


# ---------- PayMongo ----------

class PayMongo:
    name = "paymongo"

    def __init__(self, secret_key, webhook_secret="", http=None):
        self.secret_key = secret_key
        self.webhook_secret = webhook_secret
        # Tests pass a fake here, so nothing goes online.
        self.http = http or requests.Session()

    def call(self, method, path, body=None):
        try:
            response = self.http.request(method, PAYMONGO_API + path, json=body, auth=(self.secret_key, ""),
                                         timeout=20)
        except requests.RequestException as problem:
            raise GatewayError("We couldn't reach PayMongo. Please try again in a minute.") from problem
        data = response.json() if response.content else {}
        if response.status_code >= 400:
            errors = data.get("errors") or [{}]
            raise GatewayError(f"PayMongo said: {errors[0].get('detail', 'something went wrong')}")
        return data.get("data", {})

    def create_checkout(self, *, amount, ref, description, success_url, cancel_url, payment_id):
        data = self.call("POST", "/checkout_sessions", {"data": {"attributes": {
            # One line with the order's total: delivery and discounts are already in it.
            "line_items": [{"currency": "PHP", "amount": amount, "name": f"DFJC Store order {ref}", "quantity": 1}],
            "payment_method_types": PAYMENT_TYPES,
            "description": description,
            "reference_number": ref,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "send_email_receipt": False,
            "show_description": True,
            "show_line_items": True,
            "metadata": {"payment_id": str(payment_id)},
        }}})
        return data["id"], data["attributes"]["checkout_url"]

    def checkout_status(self, session_id):
        attributes = self.call("GET", f"/checkout_sessions/{session_id}").get("attributes", {})
        for payment in attributes.get("payments") or []:
            details = payment.get("attributes", {})
            if details.get("status") == "paid":
                source = (details.get("source") or {}).get("type", "")
                return "paid", payment.get("id", ""), source
        return ("expired" if attributes.get("status") == "expired" else "unpaid"), "", ""

    def expire(self, session_id):
        try:
            self.call("POST", f"/checkout_sessions/{session_id}/expire")
        except GatewayError:
            pass  # already paid or expired: payments.py checks the status anyway

    def refund(self, gateway_payment_id, amount, reason):
        data = self.call("POST", "/refunds", {"data": {"attributes": {
            "amount": amount, "payment_id": gateway_payment_id, "reason": "requested_by_customer",
            "notes": reason[:200]}}})
        return data["id"]

    def verify_webhook(self, body, header, now=None):
        """The Paymongo-Signature header is "t=<time>,te=<test signature>,li=<live signature>".
        The signature is HMAC-SHA256 of "<time>.<raw body>" with the webhook's secret key."""
        if not self.webhook_secret or not header:
            return False
        parts = dict(part.split("=", 1) for part in header.split(",") if "=" in part)
        stamp = parts.get("t", "")
        if not stamp.isdigit() or abs((now or time.time()) - int(stamp)) > WEBHOOK_TOLERANCE:
            return False
        expected = hmac.new(self.webhook_secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, parts.get(key, "")) for key in ("te", "li"))


def sign_webhook(secret, body, stamp):
    """What PayMongo puts in the header (tests use it to send real-looking webhooks)."""
    signature = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={stamp},te={signature},li="


# ---------- The Sandbox ----------

class Sandbox:
    """A practice gateway inside this app. Its payment page has Pay and Fail buttons; pressing
    Pay records an event the same way a PayMongo webhook would (payments.handle_event)."""
    name = "sandbox"

    def create_checkout(self, *, amount, ref, description, success_url, cancel_url, payment_id):
        session_id = "sbx_" + secrets.token_urlsafe(12)
        return session_id, url_for("payments.sandbox", session_id=session_id)

    def checkout_status(self, session_id):
        from . import db
        from .models import PaymentEvent
        from sqlalchemy import select
        paid = db.session().scalar(select(PaymentEvent).where(PaymentEvent.session_id == session_id,
                                                              PaymentEvent.event_type == "checkout_session.payment.paid"))
        return ("paid", f"sbx_pay_{paid.id}", "gcash") if paid else ("unpaid", "", "")

    def expire(self, session_id):
        pass

    def refund(self, gateway_payment_id, amount, reason):
        if current_app.config.get("SANDBOX_REFUNDS_FAIL"):  # tests: what if the gateway says no?
            raise GatewayError("The sandbox refused the refund (a test).")
        return "sbx_ref_" + secrets.token_urlsafe(8)

    def verify_webhook(self, body, header, now=None):
        return False  # the sandbox never sends webhooks from outside


def for_store(store):
    """The gateway this store pays with, or None if paying online is off.

    Demo stores always use the Sandbox. The real store uses PayMongo when its keys
    are set; on your own computer it falls back to the Sandbox so you can try it;
    on the live site without keys, online payment is simply off."""
    config = current_app.config
    if store is None:
        return None
    if store.is_demo:
        return Sandbox()
    if config.get("PAYMONGO_SECRET_KEY"):
        return config.get("PAYMONGO_FACTORY", PayMongo)(config["PAYMONGO_SECRET_KEY"],
                                                       config.get("PAYMONGO_WEBHOOK_SECRET", ""))
    if config.get("ON_RENDER"):
        return None
    return Sandbox()
