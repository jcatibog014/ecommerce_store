"""Brands and kinds: each its own product (prices, stock, photo), one card in the shop, chosen on its page."""
import io

from sqlalchemy import select

from dfjc import catalog, db
from dfjc.models import Product, ProductUnit

from .conftest import add_product, get, post


def coffee(app, store_id):
    """3-in-1 coffee: two brands, four kinds; Barako Gold Black is out of stock."""
    ids = {}
    for brand, kind, retail, stock in (("Kape Juan", "Original", 1200, 300), ("Kape Juan", "Creamy White", 1200, 240),
                                       ("Barako Gold", "Original", 1300, 180), ("Barako Gold", "Black", 1300, 0)):
        ids[(brand, kind)] = add_product(
            app, store_id, catalog.compose_name("3-in-1 coffee sachet", brand, kind), title="3-in-1 coffee sachet",
            brand=brand, kind=kind, category="Drinks", units=(("Sachet", 1, retail, retail - 150),), stock=stock)
    return ids


def test_the_full_name_has_the_brand_and_kind():
    assert catalog.compose_name("3-in-1 coffee sachet", "Kape Juan", "Creamy White") == \
        "3-in-1 coffee sachet - Kape Juan Creamy White"
    assert catalog.compose_name("Soy sauce 1 L") == "Soy sauce 1 L"


def test_kinds_are_one_card_in_the_shop(app, client, store_id):
    coffee(app, store_id)
    add_product(app, store_id, "Soy sauce 1 L", category="Condiments")
    page = client.get("/products").get_data(as_text=True)
    assert "2 products" in page  # the coffee is one card
    assert page.count('class="card-link"') == 2
    assert "2 brands, 4 kinds" in page and "from</span> <span class=\"price\">₱12.00" in page


def test_the_card_opens_the_cheapest_kind_that_can_be_bought(app, client, store_id):
    ids = coffee(app, store_id)
    page = client.get("/products").get_data(as_text=True)
    assert f'href="/products/{ids[("Kape Juan", "Original")]}"' in page


def test_the_product_page_has_brand_and_kind_choices(app, client, store_id):
    ids = coffee(app, store_id)
    page = client.get(f"/products/{ids[('Kape Juan', 'Creamy White')]}").get_data(as_text=True)
    assert "<h1>3-in-1 coffee sachet</h1>" in page and "Kape Juan Creamy White" in page
    assert ">Kape Juan</a>" in page and ">Barako Gold</a>" in page
    # Barako Gold has no Creamy White, so its chip opens its first kind (Black, sorted by name).
    brands = page.split('id="brand-label"')[1].split("</ul>")[0]
    assert f'href="/products/{ids[("Barako Gold", "Black")]}"' in brands
    # The kinds are Kape Juan's only, and the chosen one is marked.
    kinds = page.split('id="kind-label"')[1].split("</ul>")[0]
    assert "Original" in kinds and "Creamy White" in kinds and "Black" not in kinds
    assert f'href="/products/{ids[("Kape Juan", "Creamy White")]}" aria-current="true"' in kinds


def test_switching_brand_keeps_the_kind_when_it_can(app, client, store_id):
    ids = coffee(app, store_id)
    page = client.get(f"/products/{ids[('Kape Juan', 'Original')]}").get_data(as_text=True)
    brands = page.split('id="brand-label"')[1].split("</ul>")[0]
    assert f'href="/products/{ids[("Barako Gold", "Original")]}"' in brands


def test_an_out_of_stock_kind_is_marked(app, client, store_id):
    ids = coffee(app, store_id)
    page = client.get(f"/products/{ids[('Barako Gold', 'Original')]}").get_data(as_text=True)
    assert 'class="option-chip is-out"' in page and "(out of stock)" in page


def test_each_kind_has_its_own_price_and_stock(app, client, store_id):
    ids = coffee(app, store_id)
    page = client.get(f"/products/{ids[('Barako Gold', 'Original')]}").get_data(as_text=True)
    assert "₱13.00" in page
    assert get(app, Product, ids[("Barako Gold", "Black")]).stock_pieces == 0


def test_a_product_without_kinds_has_no_choices(app, client, store_id):
    product_id = add_product(app, store_id, "Soy sauce 1 L")
    page = client.get(f"/products/{product_id}").get_data(as_text=True)
    assert 'id="brand-label"' not in page and 'id="kind-label"' not in page


def test_search_finds_a_brand(app, client, store_id):
    coffee(app, store_id)
    add_product(app, store_id, "Soy sauce 1 L")
    page = client.get("/products?q=barako").get_data(as_text=True)
    assert "1 result" in page and "3-in-1 coffee sachet" in page and "2 kinds" in page


def test_categories_count_cards_not_kinds(app, client, store_id):
    coffee(app, store_id)
    assert ">1</span>" in client.get("/products").get_data(as_text=True).split("Drinks")[1][:80]


def test_the_owner_adds_a_kind_with_brand(app, owner, store_id):
    post(owner, "/manage/products/new", {
        "title": "3-in-1 coffee sachet", "brand": "Kape Juan", "kind": "Creamy White", "category_id": "",
        "stock_pieces": "100", "low_stock_pieces": "0", "is_active": "1", "unit_name": "Sachet",
        "unit_pieces_per_unit": "1", "unit_retail_price": "12"})
    with app.app_context():
        product = db.session().scalar(select(Product))
    assert (product.title, product.brand, product.kind) == ("3-in-1 coffee sachet", "Kape Juan", "Creamy White")
    assert product.name == "3-in-1 coffee sachet - Kape Juan Creamy White"


def test_add_another_kind_copies_the_product_and_its_sizes(app, owner, store_id):
    source = add_product(app, store_id, "Coffee", title="3-in-1 coffee sachet", brand="Kape Juan", kind="Original",
                         units=(("Sachet", 1, 1200, 1050), ("Box of 30", 30, 32000, 30000)))
    form = owner.get(f"/manage/products/new?like={source}").get_data(as_text=True)
    assert 'value="3-in-1 coffee sachet"' in form and 'value="Kape Juan"' in form and "copied from another" in form
    post(owner, "/manage/products/new", {
        "like": str(source), "title": "3-in-1 coffee sachet", "brand": "Kape Juan", "kind": "Creamy White",
        "category_id": "", "stock_pieces": "60", "low_stock_pieces": "0", "is_active": "1", "unit_name": "Sachet",
        "unit_pieces_per_unit": "1", "unit_retail_price": "12", "unit_wholesale_price": "10.50"})
    with app.app_context():
        new = db.session().scalar(select(Product).where(Product.kind == "Creamy White"))
        units = db.session().scalars(select(ProductUnit).where(ProductUnit.product_id == new.id)
                                     .order_by(ProductUnit.pieces_per_unit)).all()
    assert [(unit.name, unit.retail_price) for unit in units] == [("Sachet", 1200), ("Box of 30", 32000)]
    assert new.stock_pieces == 60


def test_import_with_brand_and_kind(app, owner):
    csv = (b"name,brand,kind,category,unit,pieces,retail,wholesale,stock\n"
           b"3-in-1 coffee sachet,Kape Juan,Original,Drinks,Sachet,1,12,10.50,300\n"
           b"3-in-1 coffee sachet,Kape Juan,Creamy White,Drinks,Sachet,1,12,10.50,240\n")
    post(owner, "/manage/products/import", {"text": csv.decode(), "confirm": "1"})
    with app.app_context():
        found = db.session().scalars(select(Product).order_by(Product.id)).all()
    assert [(p.title, p.brand, p.kind, p.stock_pieces) for p in found] == [
        ("3-in-1 coffee sachet", "Kape Juan", "Original", 300), ("3-in-1 coffee sachet", "Kape Juan", "Creamy White", 240)]


def test_orders_and_receipts_keep_the_full_name(app, client, store_id, shopper_id):
    from .conftest import log_in_as
    from .test_orders import add, checkout_key, only_order, unit_id
    ids = coffee(app, store_id)
    log_in_as(client, shopper_id)
    add(client, unit_id(app, ids[("Kape Juan", "Creamy White")], "Sachet"), 3)
    post(client, "/checkout", {"fulfilment": "pickup", "payment": "cash", "idempotency_key": checkout_key(client)})
    assert only_order(app).lines[0].product_name == "3-in-1 coffee sachet - Kape Juan Creamy White"


def test_the_demo_has_brands_and_kinds(app):
    demo = app.test_client()
    with demo.session_transaction() as session:
        session["csrf"] = "x"
    post(demo, "/demo")
    page = demo.get("/products").get_data(as_text=True)
    assert "2 brands, 4 kinds" in page and "3 kinds" in page
    with app.app_context():
        coffee_id = db.session().scalar(select(Product.id).where(Product.kind == "Creamy White",
                                                                 Product.title == "3-in-1 coffee sachet"))
    product_page = demo.get(f"/products/{coffee_id}").get_data(as_text=True)
    assert "Barako Gold" in product_page and 'class="product-side"' in product_page


def test_a_photo_fills_the_right_side(app, owner, store_id):
    from PIL import Image
    product_id = add_product(app, store_id, "Water")
    picture = io.BytesIO()
    Image.new("RGB", (800, 800), (20, 120, 200)).save(picture, "PNG")
    post(owner, f"/manage/products/{product_id}/photo", {"photo": (io.BytesIO(picture.getvalue()), "w.png")},
         content_type="multipart/form-data")
    page = owner.get(f"/products/{product_id}").get_data(as_text=True)
    assert 'class="product-visual"' in page and 'class="product-side"' not in page
    assert "Available In" in page  # the buy box moves to the left, under the details
