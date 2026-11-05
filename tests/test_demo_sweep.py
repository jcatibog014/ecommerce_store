"""Every page the demo can open, as every role: it opens, and it shows an example (never "No ... Yet")."""
import re

import pytest

from .conftest import post

PAGES = {
    "shopper": ["/", "/products", "/products?sort=new", "/cart", "/checkout", "/orders", "/favorites", "/addresses",
                "/settings/"],
    "cashier": ["/counter", "/counter/shift", "/counter/shifts", "/manage/orders", "/manage/orders?tab=waiting",
                "/manage/orders?tab=confirmed", "/manage/orders?tab=done", "/manage/orders?tab=cancelled",
                "/manage/orders?tab=all", "/manage/packing", "/deliveries", "/manage/rider-cash", "/manage/products",
                "/manage/products?show=low", "/manage/products?show=off", "/manage/categories", "/manage/stock",
                "/settings/"],
    "rider": ["/deliveries", "/", "/settings/"],
    "owner": ["/manage/insights", "/manage/insights?range=30", "/manage/insights?range=90", "/manage/staff",
              "/manage/store", "/manage/delivery-areas", "/manage/promos", "/manage/products/new",
              "/manage/products/import", "/counter/shifts"],
}
EMPTY = re.compile(r'<div class="empty-state[^"]*">\s*<h2>([^<]+)</h2>')


@pytest.fixture(scope="module")
def demo_pages(tmp_path_factory):
    """One demo, every page as every role (made once: the demo store is big)."""
    from dfjc import create_app, db
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE_URL": None,
                      "DATABASE": str(tmp_path_factory.mktemp("sweep") / "demo.sqlite")})
    with app.app_context():
        db.create_tables()
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = "x"
    post(client, "/demo")
    found = {}
    for role, urls in PAGES.items():
        post(client, "/demo/view-as", {"role": role})
        for url in urls:
            response = client.get(url)
            found[(role, url)] = (response.status_code, response.get_data(as_text=True))
    return found


@pytest.mark.parametrize("role, url", [(role, url) for role, urls in PAGES.items() for url in urls])
def test_every_demo_page_opens_and_has_an_example(demo_pages, role, url):
    status, page = demo_pages[(role, url)]
    assert status == 200
    assert EMPTY.findall(page) == []
