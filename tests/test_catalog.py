"""What shoppers see: money, the home page, search, categories, sorting, product pages."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from dfjc import catalog, db
from dfjc.dates import utcnow
from dfjc.models import Category, Store
from dfjc.money import parse_peso, peso, plain_peso

from .conftest import add_product


# ---------- Money ----------

@pytest.mark.parametrize("typed, centavos", [
    ("20", 2000), ("20.5", 2050), ("20.50", 2050), ("1,250.75", 125075), ("₱ 15", 1500), ("0.80", 80), ("PHP 7", 700),
])
def test_prices_are_read_as_whole_centavos(typed, centavos):
    assert parse_peso(typed) == centavos


@pytest.mark.parametrize("typed", ["", "abc", "20.555", "-5", "1.2.3", "20,50.1x"])
def test_bad_prices_are_refused(typed):
    assert parse_peso(typed) is None


def test_money_is_shown_with_the_peso_sign():
    assert peso(2050) == "₱20.50"
    assert peso(123456789) == "₱1,234,567.89"
    assert peso(-500) == "-₱5.00"
    assert plain_peso(80) == "0.80"


def test_whole_centavos_always_add_up():
    # 0.1 + 0.2 isn't 0.3 in floats; in centavos it is.
    assert parse_peso("0.10") + parse_peso("0.20") == parse_peso("0.30")


# ---------- Pages ----------

def test_the_home_page_shows_categories_and_new_products(client, store_id):
    add_product(client.application, store_id, "Corned beef 150 g", category="Canned Goods")
    page = client.get("/").get_data(as_text=True)
    assert "DFJC Store" in page
    assert "Shop by Category" in page and "Canned Goods" in page and "1 item" in page
    assert "Corned beef 150 g" in page and "₱15.00" in page


def test_the_home_page_before_the_store_exists(client):
    response = client.get("/")
    assert response.status_code == 503 and "Opening Soon" in response.get_data(as_text=True)


def test_shoppers_only_see_products_that_are_on_and_have_a_unit(client, store_id):
    app = client.application
    add_product(app, store_id, "Visible soap")
    add_product(app, store_id, "Hidden soap", is_active=False)
    no_units = add_product(app, store_id, "Soap with no units", units=())
    page = client.get("/products").get_data(as_text=True)
    assert "Visible soap" in page and "Hidden soap" not in page and "Soap with no units" not in page
    assert client.get(f"/products/{no_units}").status_code == 404


def test_search_needs_every_word(client, store_id):
    app = client.application
    add_product(app, store_id, "Soy sauce 1 L", description="For adobo")
    add_product(app, store_id, "Fish sauce 750 mL")
    page = client.get("/products?q=sauce+adobo").get_data(as_text=True)
    assert "Soy sauce 1 L" in page and "Fish sauce" not in page
    assert '1 result for "sauce adobo"' in page


def test_search_treats_percent_as_a_plain_letter(client, store_id):
    add_product(client.application, store_id, "Vinegar")
    assert "Vinegar" not in client.get("/products?q=%25").get_data(as_text=True)


def test_nothing_found_says_so(client, store_id):
    add_product(client.application, store_id, "Rice")
    assert "Nothing Found" in client.get("/products?q=zzz").get_data(as_text=True)


def test_a_category_shows_only_its_products(app, client, store_id):
    add_product(app, store_id, "Cola", category="Drinks")
    add_product(app, store_id, "Sardines", category="Canned Goods")
    with app.app_context():
        drinks = db.session().query(Category).filter_by(name="Drinks").one().id
    page = client.get(f"/products?category={drinks}").get_data(as_text=True)
    assert "<h1>Drinks</h1>" in page and "Cola" in page and "Sardines" not in page


def test_bad_filters_are_dropped_not_errors(client, store_id):
    add_product(client.application, store_id, "Rice")
    response = client.get("/products?category=abc&sort=evil&page=-3")
    assert response.status_code == 200 and "Rice" in response.get_data(as_text=True)


def test_sorting_by_price_uses_the_cheapest_unit(client, store_id):
    app = client.application
    add_product(app, store_id, "Expensive", units=(("Piece", 1, 9000, 9000),))
    add_product(app, store_id, "Cheap", units=(("Piece", 1, 100, 100), ("Case", 24, 99999, 99999)))
    add_product(app, store_id, "Middle", units=(("Piece", 1, 5000, 5000),))
    page = client.get("/products?sort=price-low").get_data(as_text=True)
    assert page.index("Cheap") < page.index("Middle") < page.index("Expensive")
    page = client.get("/products?sort=price-high").get_data(as_text=True)
    assert page.index("Expensive") < page.index("Middle") < page.index("Cheap")


def test_newest_first(client, store_id):
    app = client.application
    add_product(app, store_id, "Old one", created_at=utcnow() - timedelta(days=5))
    add_product(app, store_id, "New one")
    page = client.get("/products?sort=new").get_data(as_text=True)
    assert page.index("New one") < page.index("Old one")


def test_pages_of_24(client, store_id):
    for number in range(30):
        add_product(client.application, store_id, f"Item {number:02d}")
    first = client.get("/products").get_data(as_text=True)
    second = client.get("/products?page=2").get_data(as_text=True)
    assert "Page 1 of 2" in first and "Item 23" in first and "Item 24" not in first
    assert "Item 24" in second and "Item 29" in second


def test_the_product_page_has_available_in_choices(app, client, store_id):
    product_id = add_product(client.application, store_id, "Bottled water 500 mL",
                             units=(("Bottle", 1, 1500, 1300), ("Case of 24", 24, 30000, 27600)))
    with app.app_context():
        from dfjc.models import ProductUnit
        case = db.session().scalar(select(ProductUnit.id).where(ProductUnit.name == "Case of 24"))
    page = client.get(f"/products/{product_id}").get_data(as_text=True)
    # Every size is a choice; the smallest one is chosen first.
    assert "Available In" in page and ">Bottle</a>" in page and ">Case of 24</a>" in page
    assert "₱15.00" in page and "₱300.00" not in page
    page = client.get(f"/products/{product_id}?size={case}").get_data(as_text=True)
    assert "₱300.00" in page and "24 pieces, ₱12.50 each" in page  # 300 / 24 each
    assert f'size={case}" aria-current="true"' in page
    # Shoppers never see the wholesale price: the cashier gives it.
    assert "₱276.00" not in page
    assert "cashier checks every order" in page


def test_a_product_without_a_photo_has_no_picture_and_the_buy_box_on_the_right(client, store_id):
    product_id = add_product(client.application, store_id, "Soy sauce 1 L")
    page = client.get(f"/products/{product_id}").get_data(as_text=True)
    assert 'class="product-side"' in page and "product-visual" not in page and "<svg viewBox=\"0 0 48 48\"" not in page


def test_a_size_from_another_product_is_ignored(app, client, store_id):
    first = add_product(app, store_id, "Water", units=(("Bottle", 1, 1500, 1300),))
    add_product(app, store_id, "Cola", units=(("Bottle", 1, 7500, 7000),))
    from dfjc.models import ProductUnit
    with app.app_context():
        cola_unit = db.session().scalar(select(ProductUnit.id).where(ProductUnit.retail_price == 7500))
    page = client.get(f"/products/{first}?size={cola_unit}").get_data(as_text=True)
    assert "₱15.00" in page and "₱75.00" not in page


def test_stock_words(app, store_id):
    low = add_product(app, store_id, "Low", stock=5, low_stock_pieces=10)
    out = add_product(app, store_id, "Out", stock=0)
    fine = add_product(app, store_id, "Fine", stock=100, low_stock_pieces=10)
    case_only = add_product(app, store_id, "Case only", units=(("Case", 24, 100, 100),), stock=23)
    with app.app_context():
        s = db.session()
        store = s.get(Store, store_id)

        def word(product_id):
            return catalog.stock_word(catalog.shop_product(s, store, product_id))
        assert word(low) == ("low", "Only 5 left")
        assert word(out) == ("out", "Out of stock")
        assert word(fine) == ("in", "In stock")
        assert word(case_only) == ("out", "Out of stock")  # 23 pieces is less than one case


def test_another_stores_product_is_not_found(app, client, store_id):
    with app.app_context():
        s = db.session()
        other = Store(name="Someone's demo", is_demo=True, demo_expires_at=utcnow() + timedelta(hours=1))
        s.add(other)
        s.commit()
        other_id = other.id
    product_id = add_product(app, other_id, "Demo-only rice")
    assert client.get(f"/products/{product_id}").status_code == 404
    assert "Demo-only rice" not in client.get("/products").get_data(as_text=True)
