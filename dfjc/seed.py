"""The demo: every "Try Demo" gets its own small store, filled with sample products and people.

All names are made up and no product is a real brand. Every page the demo
can open must show an example (tests/test_demo.py checks it), so each new
feature adds its sample data here in the same pull request.
"""
import secrets
from datetime import timedelta

from sqlalchemy import select
from werkzeug.security import generate_password_hash

from . import invites
from .models import CASHIER, OWNER, RIDER, SHOPPER, Category, Product, ProductUnit, Store, User

DEMO_LIFETIME = timedelta(hours=2)
DEMO_NAME = "Ana Reyes"

# (category, icon, [(product, description, stock pieces, low-stock level, [(unit, pieces, retail, wholesale)])])
# Prices are in pesos here and turned into centavos below.
CATALOG = [
    ("Drinks", "bottle", [
        ("Bottled water 500 mL", "Purified drinking water.", 240, 48,
         [("Bottle", 1, 15, 13), ("Case of 24", 24, 300, 276)]),
        ("Cola 1.5 L", "Big bottle for the family table.", 60, 12,
         [("Bottle", 1, 75, 70), ("Case of 12", 12, 860, 820)]),
        ("Orange juice drink 250 mL", "Ready to drink, in a tetra pack.", 96, 24,
         [("Pack", 1, 22, 20), ("Pack of 6", 6, 125, 115), ("Case of 24", 24, 480, 450)]),
        ("3-in-1 coffee sachet", "Coffee, creamer and sugar in one sachet.", 300, 50,
         [("Sachet", 1, 12, 10.50), ("Pack of 10", 10, 110, 100), ("Box of 30", 30, 320, 300)]),
        ("Powdered juice, mango", "Makes 1 litre. Mix with cold water.", 7, 12,
         [("Sachet", 1, 20, 18), ("Pack of 12", 12, 220, 205)]),
    ]),
    ("Snacks", "snack", [
        ("Cheese crackers", "Small packs, good for baon.", 200, 30,
         [("Pack", 1, 9, 8), ("Bundle of 10", 10, 85, 78)]),
        ("Corn chips 100 g", "Crunchy and salty.", 40, 10,
         [("Bag", 1, 38, 35), ("Box of 20", 20, 720, 680)]),
        ("Roasted peanuts 50 g", "Salted, with garlic.", 144, 24,
         [("Pack", 1, 15, 13), ("Bundle of 12", 12, 165, 150)]),
        ("Cream sandwich cookies", "Vanilla cream between two cookies.", 0, 12,
         [("Pack", 1, 10, 9), ("Box of 12", 12, 110, 100)]),
    ]),
    ("Canned Goods", "can", [
        ("Corned beef 150 g", "Easy breakfast with rice and egg.", 96, 24,
         [("Can", 1, 45, 42), ("Case of 48", 48, 2100, 1990)]),
        ("Sardines in tomato sauce 155 g", "A pantry staple.", 200, 40,
         [("Can", 1, 25, 23), ("Case of 100", 100, 2350, 2250)]),
        ("Meat loaf 150 g", "Slice and fry.", 48, 12,
         [("Can", 1, 32, 30), ("Case of 48", 48, 1480, 1400)]),
        ("Evaporated milk 370 mL", "For coffee, desserts and cooking.", 72, 24,
         [("Can", 1, 38, 35), ("Case of 48", 48, 1780, 1690)]),
    ]),
    ("Noodles", "noodles", [
        ("Instant noodles, chicken 55 g", "Soup noodles, ready in 3 minutes.", 360, 72,
         [("Pack", 1, 12, 10.80), ("Box of 72", 72, 820, 770)]),
        ("Instant pancit canton 60 g", "Stir-fry noodles, sweet and spicy.", 288, 72,
         [("Pack", 1, 16, 14.50), ("Box of 72", 72, 1080, 1020)]),
        ("Cup noodles, beef", "Add hot water, wait 3 minutes.", 18, 24,
         [("Cup", 1, 28, 26), ("Case of 24", 24, 640, 600)]),
    ]),
    ("Rice and Grains", "rice", [
        ("Well-milled rice", "Sold by the kilo or by the 25 kg sack.", 500, 75,
         [("Kilo", 1, 52, 50), ("Sack of 25 kg", 25, 1250, 1180)]),
        ("Brown sugar", "Sold by the kilo or by the 50 kg sack.", 120, 25,
         [("Kilo", 1, 85, 80), ("Sack of 50 kg", 50, 3950, 3800)]),
        ("All-purpose flour", "For bread, cakes and frying.", 60, 10,
         [("Kilo", 1, 65, 60), ("Sack of 25 kg", 25, 1450, 1380)]),
    ]),
    ("Condiments", "condiment", [
        ("Soy sauce 1 L", "For adobo and dipping.", 48, 12,
         [("Bottle", 1, 48, 45), ("Case of 12", 12, 560, 530)]),
        ("Cane vinegar 1 L", "Sour and clear.", 36, 12,
         [("Bottle", 1, 40, 37), ("Case of 12", 12, 460, 430)]),
        ("Cooking oil 1 L", "Palm oil, for frying.", 30, 12,
         [("Bottle", 1, 105, 99), ("Case of 12", 12, 1230, 1170)]),
        ("Iodized salt 500 g", "Fine salt.", 50, 10,
         [("Pack", 1, 18, 16), ("Bundle of 10", 10, 170, 155)]),
    ]),
    ("Personal Care", "care", [
        ("Shampoo sachet 12 mL", "One wash in each sachet.", 240, 36,
         [("Sachet", 1, 7, 6.25), ("Strip of 12", 12, 80, 72)]),
        ("Bath soap 90 g", "Gentle soap bar.", 60, 12,
         [("Bar", 1, 38, 35), ("Pack of 6", 6, 220, 205)]),
        ("Toothpaste 50 g", "Mint, for the whole family.", 30, 6,
         [("Tube", 1, 55, 51), ("Box of 12", 12, 640, 600)]),
    ]),
    ("Household", "home", [
        ("Laundry powder 70 g", "One load in each sachet.", 288, 48,
         [("Sachet", 1, 10, 9), ("Strip of 6", 6, 58, 53), ("Case of 144", 144, 1350, 1260)]),
        ("Dishwashing liquid 250 mL", "Lemon. Cuts grease.", 40, 10,
         [("Bottle", 1, 45, 42), ("Case of 24", 24, 1040, 980)]),
        ("Candles, big", "For brownouts and the altar.", 100, 20,
         [("Piece", 1, 8, 7), ("Pack of 10", 10, 75, 68)]),
    ]),
    ("Sweets", "candy", [
        ("Assorted candies", "Fruit and milk candies.", 1000, 100,
         [("Piece", 1, 1, 0.80), ("Pack of 100", 100, 90, 75)]),
        ("Chocolate bar 30 g", "Milk chocolate.", 48, 12,
         [("Bar", 1, 15, 13.50), ("Box of 24", 24, 340, 320)]),
    ]),
]
SWITCHED_OFF = ("Ice candy, buko", "Summer only. Switched off until the hot months.", 0, 0, "candy",
                [("Piece", 1, 10, 9)])


def centavos(pesos):
    return round(pesos * 100)


def fill(s, store, moment):
    """The sample categories and products. The newest product was added today, the oldest a month ago."""
    order = 0
    added = 0
    by_icon = {}
    for sort_order, (category_name, icon, products) in enumerate(CATALOG):
        category = Category(store_id=store.id, name=category_name, icon=icon, sort_order=sort_order)
        s.add(category)
        s.flush()
        by_icon[icon] = category
        for name, description, stock, low, units in products:
            order += 1
            created = moment - timedelta(days=30) + timedelta(hours=order * 20)
            product = Product(store_id=store.id, category_id=category.id, name=name, description=description,
                              stock_pieces=stock, low_stock_pieces=low, created_at=created, updated_at=created)
            for unit_name, pieces, retail, wholesale in units:
                product.units.append(ProductUnit(name=unit_name, pieces_per_unit=pieces,
                                                 retail_price=centavos(retail), wholesale_price=centavos(wholesale)))
            s.add(product)
            added += 1
    name, description, stock, low, icon, units = SWITCHED_OFF
    product = Product(store_id=store.id, category_id=by_icon[icon].id, name=name, description=description,
                      stock_pieces=stock, low_stock_pieces=low, is_active=False,
                      created_at=moment - timedelta(days=40), updated_at=moment - timedelta(days=40))
    for unit_name, pieces, retail, wholesale in units:
        product.units.append(ProductUnit(name=unit_name, pieces_per_unit=pieces, retail_price=centavos(retail),
                                         wholesale_price=centavos(wholesale)))
    s.add(product)
    s.flush()
    return added + 1


def person(s, store, name, email, role, mobile, moment, days_ago):
    """A sample person nobody can log in as (their password is random and never shown)."""
    user = User(store_id=store.id, name=name, email=email, role=role, mobile=mobile,
                password_hash=generate_password_hash(secrets.token_urlsafe(16), method="pbkdf2:sha256:1000"),
                created_at=moment - timedelta(days=days_ago))
    user.store = store
    s.add(user)
    s.flush()
    return user


def make_demo(s, moment):
    """A new demo store with its visitor (who plays every role through View As). Returns the visitor."""
    store = Store(name="DFJC Store", tagline="Wholesale and retail. Sari-sari needs by the piece, the pack or the case.",
                  address="123 Rizal Street, Poblacion (a sample address)", phone="0917 555 0123",
                  hours="Every day, 6:00 AM to 9:00 PM",
                  pickup_note="Pick up at the counter. Show your order number to the cashier.",
                  is_demo=True, demo_expires_at=moment + DEMO_LIFETIME, created_at=moment)
    s.add(store)
    s.flush()
    visitor = person(s, store, DEMO_NAME, "ana.reyes@example.com", OWNER, "09171234567", moment, 90)
    cashier = person(s, store, "Lorna Cruz", "lorna.cruz@example.com", CASHIER, "09181234567", moment, 60)
    rider = person(s, store, "Jun Dizon", "jun.dizon@example.com", RIDER, "09191234567", moment, 45)
    marites = person(s, store, "Marites Santos", "marites.santos@example.com", SHOPPER, "09201234567", moment, 20)
    ramon = person(s, store, "Ramon Garcia", "ramon.garcia@example.com", SHOPPER, "09211234567", moment, 12)
    # Invite links: one used by the rider, one still waiting for a new cashier.
    used, _ = invites.create_invite(s, store, RIDER, "Jun", visitor, moment - timedelta(days=46))
    used.used_at, used.used_by_id = moment - timedelta(days=45), rider.id
    invites.create_invite(s, store, CASHIER, "New cashier for the night shift", visitor, moment - timedelta(days=1))
    fill(s, store, moment)
    fill_orders(s, store, {"ana": visitor, "lorna": cashier, "marites": marites, "ramon": ramon}, moment)
    return visitor


# ---------- Stage 2: delivery areas, addresses, a cart and orders ----------

# (barangay, fee in pesos, delivered to now?)
AREAS = [("Poblacion", 30, True), ("San Isidro", 40, True), ("Santa Cruz", 50, True), ("Bagong Silang", 60, True),
         ("Malinis", 80, False)]


def fill_orders(s, store, people, moment):
    """Orders made with the real checkout and pricing code, so the held stock adds up.
    Ana (the visitor) has one waiting for the cashier, one priced and one she cancelled;
    other shoppers have bulk orders waiting, so the cashier has something to price."""
    from . import checkout, orders
    from .models import DELIVERY, PICKUP, WHOLESALE, Address, DeliveryArea

    areas = {}
    for order_index, (name, fee, active) in enumerate(AREAS):
        areas[name] = DeliveryArea(store_id=store.id, name=name, fee=centavos(fee), is_active=active,
                                   sort_order=order_index)
        s.add(areas[name])
    s.flush()
    units = {(unit.product.name, unit.name): unit
             for unit in s.scalars(select(ProductUnit).join(Product).where(Product.store_id == store.id))}

    home = Address(user_id=people["ana"].id, label="Home", area_id=areas["San Isidro"].id,
                   street="45 Mabini Street, Purok 3", landmark="the blue gate beside the chapel",
                   created_at=moment - timedelta(days=30))
    home.area = areas["San Isidro"]
    s.add(home)

    def order(who, lines, ago, fulfilment=PICKUP, address=None, note=""):
        user = people[who]
        for product_name, unit_name, quantity in lines:
            checkout.add_to_cart(s, user, units[(product_name, unit_name)], quantity, moment - ago)
        s.flush()
        area = address.area if address else None
        choice = {"fulfilment": fulfilment, "payment_method": "cash_pickup" if fulfilment == PICKUP else "cod",
                  "area": area, "address": address.one_line if address else "", "note": note}
        placed, _ = checkout.place_order(s, user, store, choice, checkout.new_checkout_key(), moment - ago)
        return placed

    def price(placed, ago, wholesale_bulk=True, note=""):
        choices = {line.id: WHOLESALE for line in placed.lines if wholesale_bulk and line.pieces_per_unit > 1}
        orders.price_order(s, placed, choices, people["lorna"], moment - ago, placed.version, note)

    hours, days = timedelta(hours=1), timedelta(days=1)
    # Ana's own orders (what the visitor sees as a shopper), oldest first.
    cancelled = order("ana", [("Chocolate bar 30 g", "Box of 24", 1)], 4 * days)
    orders.cancel(s, cancelled, people["ana"], moment - 4 * days + hours, "I ordered the wrong size.")
    priced = order("ana", [("Cola 1.5 L", "Case of 12", 1), ("Bottled water 500 mL", "Bottle", 6)], days,
                   DELIVERY, home, "Please call when you are near.")
    price(priced, days - 2 * hours, wholesale_bulk=False)
    order("ana", [("Well-milled rice", "Sack of 25 kg", 1), ("Soy sauce 1 L", "Bottle", 2),
                  ("Cheese crackers", "Bundle of 10", 1)], timedelta(minutes=35))

    # Other shoppers: a reseller's bulk orders (one priced at wholesale), a delivery, and one the store cancelled.
    bulk = order("marites", [("Instant pancit canton 60 g", "Box of 72", 1), ("3-in-1 coffee sachet", "Box of 30", 2),
                             ("Candles, big", "Piece", 4)], 2 * days)
    price(bulk, 2 * days - hours, note="Wholesale price for the boxes. Salamat po!")
    order("marites", [("Instant noodles, chicken 55 g", "Box of 72", 2), ("Sardines in tomato sauce 155 g", "Case of 100", 1),
                      ("Laundry powder 70 g", "Case of 144", 1), ("Shampoo sachet 12 mL", "Strip of 12", 5)],
          timedelta(minutes=12), note="For my sari-sari store. I will pick up tomorrow morning.")
    ramon_home = Address(user_id=people["ramon"].id, label="Home", area_id=areas["Poblacion"].id,
                         street="12 Luna Street", landmark="", created_at=moment - 5 * days)
    ramon_home.area = areas["Poblacion"]
    s.add(ramon_home)
    sold_out = order("ramon", [("Meat loaf 150 g", "Case of 48", 1)], 3 * days, DELIVERY, ramon_home)
    orders.cancel(s, sold_out, people["lorna"], moment - 3 * days + hours,
                  "Sorry, the supplier was late with this case. We will text you when it arrives.")
    order("ramon", [("Corned beef 150 g", "Can", 3), ("Evaporated milk 370 mL", "Can", 2)], timedelta(minutes=4),
          DELIVERY, ramon_home)

    # Something in Ana's cart, so the cart page has an example.
    checkout.add_to_cart(s, people["ana"], units[("Bottled water 500 mL", "Case of 24")], 1, moment)
    checkout.add_to_cart(s, people["ana"], units[("Instant pancit canton 60 g", "Pack")], 6, moment)
    s.flush()
