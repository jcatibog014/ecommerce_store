"""Insights (what counts as a sale, per day, best sellers, how people paid) and the phone-app files."""
import json
import re
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from dfjc import counter, db, insights, orders
from dfjc.dates import local_today, utcnow
from dfjc.models import CashShift, Order, Store, User

from .conftest import add_product, log_in_as, post
from .test_orders import add, checkout_key, unit_id

STATIC = Path(__file__).parent.parent / "dfjc" / "static"


def sell_online(app, client, shopper_id, cashier_id, unit, quantity, wholesale=False):
    log_in_as(client, shopper_id)
    add(client, unit, quantity)
    post(client, "/checkout", {"fulfilment": "pickup", "payment": "cash", "idempotency_key": checkout_key(client)})
    with app.app_context():
        s = db.session()
        order = s.scalar(select(Order).order_by(Order.id.desc()))
        orders.price_order(s, order, {line.id: "wholesale" for line in order.lines} if wholesale else {},
                           s.get(User, cashier_id), utcnow(), order.version)
        s.commit()
        return order.id


def test_the_report_counts_only_real_sales(app, client, store_id, shopper_id, cashier_id):
    water = add_product(app, store_id, "Water", units=(("Bottle", 1, 1500, 1300), ("Case", 24, 30000, 27600)),
                        stock=500)
    soap = add_product(app, store_id, "Soap", units=(("Bar", 1, 4000, 3500),), stock=50)
    sell_online(app, client, shopper_id, cashier_id, unit_id(app, water, "Case"), 2, wholesale=True)  # 552
    cancelled = sell_online(app, client, shopper_id, cashier_id, unit_id(app, soap, "Bar"), 5)
    with app.app_context():
        s = db.session()
        order = s.get(Order, cancelled)
        orders.cancel(s, order, s.get(User, cashier_id), utcnow(), "x")
        store, cashier = s.get(Store, store_id), s.get(User, cashier_id)
        shift = counter.open_shift(s, store, cashier, 0, utcnow())
        counter.sell(s, store, shift, cashier, [[unit_id(app, soap, "Bar"), 2, "retail"]], 10000, "k1", utcnow())
        voided, _ = counter.sell(s, store, shift, cashier, [[unit_id(app, water, "Bottle"), 10, "retail"]], 20000,
                                 "k2", utcnow())
        counter.void(s, voided, cashier, utcnow(), "oops", {line.id: line.quantity for line in voided.lines})
        s.commit()
        report = insights.report(s, store, 7)
    now = report["now"]
    assert (now["online"], now["counter"], now["total"], now["count"]) == (55200, 8000, 63200, 2)
    assert now["average"] == 31600 and report["online_share"] == 87
    assert report["rows"][-1]["day"] == local_today() and report["rows"][-1]["total"] == 63200
    assert [(name, pieces, amount) for (name, pieces, amount), _ in report["best"]] == [
        ("Water", 48, 55200), ("Soap", 2, 8000)]
    assert report["kinds"] == {"retail": 8000, "wholesale": 55200} and report["wholesale_share"] == 87
    payments = dict(item for item, _ in report["payments"])
    assert payments["Cash at pickup"] == 55200 and payments["Counter (cash)"] == 8000
    assert report["change"] is None  # nothing in the week before


def test_the_chart_geometry(app):
    today = local_today()
    rows = [{"day": today - timedelta(days=6 - i), "online": i * 10000, "counter": 5000, "total": i * 10000 + 5000}
            for i in range(7)]
    chart = insights.day_chart(rows)
    assert chart["grid"][-1]["value"] == 80000  # the top: a round number above the biggest day (65,000)
    assert len(chart["bars"]) == 7 and all(bar["label"] for bar in chart["bars"])
    first = chart["bars"][0]
    assert first["online_path"] == "" and first["counter_path"].startswith("M")  # nothing online that day
    assert insights.nice_ceiling(0) == 100000 and insights.nice_ceiling(340000) == 400000


def test_a_change_against_the_period_before():
    assert insights.change(12000, 10000) == 20 and insights.change(5000, 10000) == -50
    assert insights.change(100, 0) is None


def test_insights_are_for_the_owner(client, owner, cashier_id):
    page = owner.get("/manage/insights?range=30").get_data(as_text=True)
    assert "Sales per Day" in page and "Last 30 Days" in page and "Show as a table" in page
    log_in_as(client, cashier_id)
    assert client.get("/manage/insights").status_code == 403


def test_the_demo_insights_have_examples(app):
    demo = app.test_client()
    with demo.session_transaction() as session:
        session["csrf"] = "x"
    post(demo, "/demo")
    post(demo, "/demo/view-as", {"role": "owner"})
    page = demo.get("/manage/insights").get_data(as_text=True)
    assert "Best Sellers" in page and "No sales in these days yet" not in page
    assert page.count('class="bar-counter"') >= 3 and page.count('class="bar-online"') >= 3
    assert re.search(r"(\d+)% <span class=\"stat-inline\">at the wholesale price", page).group(1) != "0"


# ---------- The phone app ----------

def test_the_service_worker_is_served_from_the_root(client):
    response = client.get("/sw.js")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-cache"
    assert response.headers["Service-Worker-Allowed"] == "/"
    assert "javascript" in response.mimetype


def test_everything_the_worker_keeps_exists():
    worker = (STATIC / "sw.js").read_text(encoding="utf-8")
    files = re.findall(r'"/static/([^"]+)"', worker)
    assert "offline.html" in files
    assert [name for name in files if not (STATIC / name).exists()] == []


def test_the_manifest_and_icons(client, store_id):
    manifest = json.loads((STATIC / "site.webmanifest").read_text(encoding="utf-8"))
    assert manifest["name"] == "DFJC Store" and manifest["display"] == "standalone"
    assert [icon["src"] for icon in manifest["icons"] if not (STATIC / icon["src"]).exists()] == []
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])
    page = client.get("/").get_data(as_text=True)
    assert 'rel="manifest"' in page and "apple-touch-icon" in page
    policy = client.get("/").headers["Content-Security-Policy"]
    assert "worker-src 'self'" in policy


def test_settings_offers_the_app(shopper):
    page = shopper.get("/settings/").get_data(as_text=True)
    assert "Phone App" in page and "Add to Home Screen" in page and "data-install-button" in page


def test_cash_shifts_dont_break_the_report(app, store_id, cashier_id):
    with app.app_context():
        s = db.session()
        assert s.scalar(select(CashShift)) is None
        assert insights.report(s, s.get(Store, store_id), 90)["now"]["total"] == 0
