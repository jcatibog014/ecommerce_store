"""Staff catalog pages: who may open them, products, units and prices, photos, categories."""
import io

import pytest
from PIL import Image
from sqlalchemy import select

from dfjc import db
from dfjc.models import Category, Photo, Product, ProductUnit

from .conftest import add_product, get, log_in_as, post

NEW_PRODUCT = {"title": "Soy sauce 1 L", "description": "For adobo", "category_id": "", "stock_pieces": "48",
               "low_stock_pieces": "12", "is_active": "1", "unit_name": "Bottle", "unit_pieces_per_unit": "1",
               "unit_retail_price": "48", "unit_wholesale_price": "45.50", "unit_barcode": ""}


def png_bytes(size=(1600, 1200), color=(200, 30, 30)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


def only_product(app):
    with app.app_context():
        return db.session().scalar(select(Product))


# ---------- Who may open what ----------

@pytest.mark.parametrize("url", ["/manage/products", "/manage/categories", "/manage/staff", "/manage/store"])
def test_visitors_are_asked_to_log_in(client, store_id, url):
    response = client.get(url)
    assert response.status_code == 302 and "/login" in response.location


@pytest.mark.parametrize("url", ["/manage/products", "/manage/products/new", "/manage/categories",
                                 "/manage/staff", "/manage/store"])
def test_shoppers_and_riders_cant_open_staff_pages(client, shopper_id, rider_id, url):
    for user_id in (shopper_id, rider_id):
        log_in_as(client, user_id)
        assert client.get(url).status_code == 403


def test_a_cashier_keeps_products_too(app, client, cashier_id, store_id):
    """The owner or the cashier types every product, size and price; staff and the store stay the owner's."""
    product_id = add_product(app, store_id, "Rice", units=(("Kilo", 1, 5200, 5000),))
    log_in_as(client, cashier_id)
    page = client.get(f"/manage/products/{product_id}").get_data(as_text=True)
    assert "Only a cashier or the owner" not in page and "Add another kind" in page
    assert client.get("/manage/products/new").status_code == 200
    response = post(client, f"/manage/products/{product_id}/units", {
        "new_name": "Sack of 25 kg", "new_pieces_per_unit": "25", "new_retail_price": "1250"})
    assert response.status_code == 302
    assert post(client, "/manage/categories", {"name": "Rice", "icon": "rice", "sort_order": "0"}).status_code == 302
    assert client.get("/manage/staff").status_code == 403
    assert client.get("/manage/store").status_code == 403


def test_riders_and_shoppers_cant_change_products(client, rider_id, shopper_id, store_id):
    product_id = add_product(client.application, store_id, "Rice")
    for user_id in (rider_id, shopper_id):
        log_in_as(client, user_id)
        assert client.get("/manage/products/new").status_code == 403
        assert post(client, f"/manage/products/{product_id}/units", {"new_name": "Sack"}).status_code == 403


# ---------- Products ----------

def test_the_owner_adds_a_product_with_its_first_unit(app, owner):
    response = post(owner, "/manage/products/new", NEW_PRODUCT)
    assert response.status_code == 302
    product = only_product(app)
    with app.app_context():
        unit = db.session().scalar(select(ProductUnit))
    assert (product.name, product.stock_pieces, product.low_stock_pieces, product.is_active) == ("Soy sauce 1 L", 48, 12, True)
    assert (unit.name, unit.retail_price, unit.wholesale_price) == ("Bottle", 4800, 4550)
    assert response.location.endswith(f"/manage/products/{product.id}#units")


def test_an_empty_wholesale_price_is_the_retail_price(app, owner):
    post(owner, "/manage/products/new", {**NEW_PRODUCT, "unit_wholesale_price": ""})
    with app.app_context():
        unit = db.session().scalar(select(ProductUnit))
    assert unit.wholesale_price == unit.retail_price == 4800


def test_the_new_product_form_explains_its_problems(app, owner):
    response = post(owner, "/manage/products/new", {**NEW_PRODUCT, "title": "", "stock_pieces": "-3",
                                                    "unit_retail_price": "abc", "unit_pieces_per_unit": "0"})
    page = response.get_data(as_text=True)
    assert response.status_code == 400
    for message in ("name is needed.", "Use a whole number from 0", "Use a price like 20 or 20.50.",
                    "Use a whole number from 1"):
        assert message in page
    assert only_product(app) is None


def test_wholesale_cant_cost_more_than_retail(app, owner):
    response = post(owner, "/manage/products/new", {**NEW_PRODUCT, "unit_wholesale_price": "60"})
    assert response.status_code == 400
    assert "be more than the retail price" in response.get_data(as_text=True)


def test_the_owner_edits_a_product(app, owner, store_id):
    product_id = add_product(app, store_id, "Soap")
    response = post(owner, f"/manage/products/{product_id}", {
        "title": "Bath soap 90 g", "description": "", "category_id": "", "stock_pieces": "60",
        "low_stock_pieces": "6", "version": "1"})
    assert response.status_code == 302
    product = get(app, Product, product_id)
    # Stock is not on this form any more (Restock and Count keep its history), so it stays 100.
    assert (product.name, product.stock_pieces, product.is_active, product.version) == ("Bath soap 90 g", 100, False, 2)


def test_a_save_from_an_old_form_is_refused(app, owner, store_id):
    """Two staff opened the same product. The second save would undo the first, so it's refused."""
    product_id = add_product(app, store_id, "Soap")
    form = {"title": "First save", "category_id": "", "stock_pieces": "10", "low_stock_pieces": "0",
            "is_active": "1", "version": "1"}
    post(owner, f"/manage/products/{product_id}", form)
    response = post(owner, f"/manage/products/{product_id}", {**form, "title": "Second save"}, follow_redirects=True)
    assert "Someone else saved this product" in response.get_data(as_text=True)
    assert get(app, Product, product_id).name == "First save"


def test_the_database_catches_a_save_in_between(app, store_id):
    """Even if both read version 1 at the same moment, only one UPDATE finds it."""
    from sqlalchemy.orm import Session
    from sqlalchemy.orm.exc import StaleDataError
    product_id = add_product(app, store_id, "Soap")
    with app.app_context():
        one, two = Session(db.engine()), Session(db.engine())
        a, b = one.get(Product, product_id), two.get(Product, product_id)
        a.name = "From one"
        one.commit()
        b.name = "From two"
        with pytest.raises(StaleDataError):
            two.commit()
        one.close()
        two.close()
    assert get(app, Product, product_id).name == "From one"


def test_a_category_from_another_store_is_refused(app, owner, store_id):
    from dfjc.models import Store
    with app.app_context():
        s = db.session()
        other = Store(name="Other", is_demo=True)
        s.add(other)
        s.flush()
        category = Category(store_id=other.id, name="Theirs")
        s.add(category)
        s.commit()
        category_id = category.id
    response = post(owner, "/manage/products/new", {**NEW_PRODUCT, "category_id": str(category_id)})
    assert response.status_code == 400 and "Choose a category from the list." in response.get_data(as_text=True)


def test_another_stores_product_is_not_found(app, owner):
    from dfjc.models import Store
    with app.app_context():
        s = db.session()
        other = Store(name="Other", is_demo=True)
        s.add(other)
        s.commit()
        other_id = other.id
    product_id = add_product(app, other_id, "Not yours")
    assert owner.get(f"/manage/products/{product_id}").status_code == 404
    assert post(owner, f"/manage/products/{product_id}/units", {"new_name": "Case"}).status_code == 404


def test_the_product_list_filters(app, owner, store_id):
    add_product(app, store_id, "Running low", stock=3, low_stock_pieces=10)
    add_product(app, store_id, "Plenty", stock=100, low_stock_pieces=10)
    add_product(app, store_id, "Switched off", is_active=False)
    low = owner.get("/manage/products?show=low").get_data(as_text=True)
    assert "Running low" in low and "Plenty" not in low
    off = owner.get("/manage/products?show=off").get_data(as_text=True)
    assert "Switched off" in off and "Plenty" not in off
    found = owner.get("/manage/products?q=plen").get_data(as_text=True)
    assert "Plenty" in found and "Running low" not in found


# ---------- Units ----------

def test_add_save_and_remove_units(app, owner, store_id):
    product_id = add_product(app, store_id, "Water")
    response = post(owner, f"/manage/products/{product_id}/units", {
        "new_name": "Case of 24", "new_pieces_per_unit": "24", "new_retail_price": "300", "new_wholesale_price": "276"})
    assert response.status_code == 302
    with app.app_context():
        case = db.session().scalar(select(ProductUnit).where(ProductUnit.name == "Case of 24"))
    assert (case.pieces_per_unit, case.retail_price, case.wholesale_price) == (24, 30000, 27600)

    p = f"u{case.id}_"
    post(owner, f"/manage/products/{product_id}/units/{case.id}", {
        p + "name": "Case of 24", p + "pieces_per_unit": "24", p + "retail_price": "310",
        p + "wholesale_price": "280", p + "barcode": "4800000000017", p + "is_active_sent": "1"})
    case = get(app, ProductUnit, case.id)
    assert (case.retail_price, case.barcode, case.is_active) == (31000, "4800000000017", False)

    post(owner, f"/manage/products/{product_id}/units/{case.id}/delete")
    assert get(app, ProductUnit, case.id) is None


def test_two_units_cant_share_a_name(app, owner, store_id):
    product_id = add_product(app, store_id, "Water")
    response = post(owner, f"/manage/products/{product_id}/units", {
        "new_name": "Bottle", "new_pieces_per_unit": "1", "new_retail_price": "15"}, follow_redirects=True)
    assert "already has a unit called" in response.get_data(as_text=True)


# ---------- Photos ----------

def test_a_photo_is_made_small_and_served(app, owner, store_id):
    product_id = add_product(app, store_id, "Water")
    response = post(owner, f"/manage/products/{product_id}/photo",
                    {"photo": (io.BytesIO(png_bytes()), "water.png")}, content_type="multipart/form-data")
    assert response.status_code == 302
    product = get(app, Product, product_id)
    with app.app_context():
        photo = db.session().get(Photo, product.photo_id)
        assert (photo.width, photo.height) == (1000, 750)
    big = owner.get(f"/photos/{product.photo_id}.webp")
    small = owner.get(f"/photos/{product.photo_id}/small.webp")
    assert big.mimetype == small.mimetype == "image/webp"
    assert Image.open(io.BytesIO(small.data)).size == (400, 300)
    assert "immutable" in big.headers["Cache-Control"]
    # Shoppers see it on the card.
    assert f"/photos/{product.photo_id}/small.webp" in owner.get("/products").get_data(as_text=True)

    # A new photo replaces the old one, which is deleted.
    old = product.photo_id
    post(owner, f"/manage/products/{product_id}/photo", {"photo": (io.BytesIO(png_bytes(color=(0, 0, 200))), "b.png")},
         content_type="multipart/form-data")
    assert get(app, Photo, old) is None
    post(owner, f"/manage/products/{product_id}/photo/delete")
    assert get(app, Product, product_id).photo_id is None


def test_a_file_that_isnt_a_photo_is_refused(app, owner, store_id):
    product_id = add_product(app, store_id, "Water")
    response = post(owner, f"/manage/products/{product_id}/photo",
                    {"photo": (io.BytesIO(b"%PDF-1.4 not a picture"), "menu.png")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "a photo we can read" in response.get_data(as_text=True)
    assert get(app, Product, product_id).photo_id is None


def test_a_missing_photo_is_not_found(client, store_id):
    assert client.get("/photos/999.webp").status_code == 404


# ---------- Categories ----------

def test_categories(app, owner, store_id):
    assert post(owner, "/manage/categories", {"name": "Drinks", "icon": "bottle", "sort_order": "1"}).status_code == 302
    duplicate = post(owner, "/manage/categories", {"name": "Drinks", "icon": "bottle", "sort_order": "2"})
    assert duplicate.status_code == 400 and "already a category" in duplicate.get_data(as_text=True)
    with app.app_context():
        category = db.session().scalar(select(Category))
    post(owner, f"/manage/categories/{category.id}", {"name": "Cold Drinks", "icon": "frozen", "sort_order": "3"})
    category = get(app, Category, category.id)
    assert (category.name, category.icon, category.sort_order) == ("Cold Drinks", "frozen", 3)

    product_id = add_product(app, store_id, "Cola", category="Cold Drinks")
    post(owner, f"/manage/categories/{category.id}/delete")
    assert get(app, Category, category.id) is None
    assert get(app, Product, product_id).category_id is None  # the product stays


def test_an_unknown_icon_becomes_the_box(app, owner):
    post(owner, "/manage/categories", {"name": "Odd", "icon": "<script>", "sort_order": "0"})
    with app.app_context():
        assert db.session().scalar(select(Category)).icon == "box"
