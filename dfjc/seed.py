"""The demo: every "Try Demo" gets its own small store, filled with sample products and people.

All names are made up and no product is a real brand. Every page the demo
can open must show an example (tests/test_demo.py checks it), so each new
feature adds its sample data here in the same pull request.
"""
import secrets
from datetime import date, timedelta

from sqlalchemy import select
from werkzeug.security import generate_password_hash

from . import invites, stock
from .stock import starting_stock
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
# Brands and kinds (all made up). The CATALOG product becomes the first kind; each extra
# kind is its own product with its own stock: (brand, kind, stock pieces, pesos more per piece).
VARIANTS = {
    "3-in-1 coffee sachet": ("Kape Juan", "Original", [("Kape Juan", "Creamy White", 240, 0),
                                                       ("Barako Gold", "Original", 180, 1),
                                                       ("Barako Gold", "Black", 0, 1)]),
    "Instant pancit canton 60 g": ("Pancit Ko", "Sweet and Spicy", [("Pancit Ko", "Calamansi", 216, 0),
                                                                    ("Pancit Ko", "Hot Chili", 144, 0)]),
    "Shampoo sachet 12 mL": ("Lusog Hair", "Anti-dandruff", [("Lusog Hair", "Smooth and Silky", 200, 0),
                                                             ("Bango", "Fresh Mint", 150, 0.5)]),
    "Bath soap 90 g": ("Puti", "White", [("Puti", "Lemon", 48, 0)]),
    "Laundry powder 70 g": ("Kalinis", "Original", [("Kalinis", "Floral", 200, 0)]),
}

SWITCHED_OFF = ("Ice candy, buko", "Summer only. Switched off until the hot months.", 0, 0, "candy",
                [("Piece", 1, 10, 9)])


def centavos(pesos):
    return round(pesos * 100)


def fill(s, store, moment):
    """The sample categories and products. The newest product was added today, the oldest a month ago."""
    order = 0
    added = 0
    by_icon = {}
    made = {}
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
            s.flush()
            starting_stock(s, product, None, created)
            made[name] = (product, units, created)
            added += 1
    added += fill_variants(s, store, made)
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
    # A random session_version: SQLite gives a deleted demo's ids to the next demo, so an old
    # demo cookie (user id + version 1) could otherwise open a new visitor's demo.
    user = User(store_id=store.id, name=name, email=email, role=role, mobile=mobile,
                password_hash=generate_password_hash(secrets.token_urlsafe(16), method="pbkdf2:sha256:1000"),
                session_version=secrets.randbelow(10**9) + 2, created_at=moment - timedelta(days=days_ago))
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
    fill_orders(s, store, {"ana": visitor, "lorna": cashier, "jun": rider, "marites": marites, "ramon": ramon},
                moment)
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
    units = {}
    for unit in s.scalars(select(ProductUnit).join(Product).where(Product.store_id == store.id)
                          .order_by(ProductUnit.id)):
        units.setdefault((unit.product.group_title, unit.name), unit)

    home = Address(user_id=people["ana"].id, label="Home", area_id=areas["San Isidro"].id,
                   street="45 Mabini Street, Purok 3", landmark="the blue gate beside the chapel",
                   created_at=moment - timedelta(days=30))
    home.area = areas["San Isidro"]
    s.add(home)

    promos = fill_promos(s, store, moment)

    def order(who, lines, ago, fulfilment=PICKUP, address=None, note="", online=False, voucher=None):
        user = people[who]
        for product_name, unit_name, quantity in lines:
            checkout.add_to_cart(s, user, units[(product_name, unit_name)], quantity, moment - ago)
        s.flush()
        area = address.area if address else None
        method = "online" if online else "cash_pickup" if fulfilment == PICKUP else "cod"
        choice = {"fulfilment": fulfilment, "payment_method": method, "voucher": promos.get(voucher),
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
                   DELIVERY, home, "Please call when you are near.", voucher="LIBRENGHATID")
    price(priced, days - 2 * hours, wholesale_bulk=False)
    order("ana", [("Well-milled rice", "Sack of 25 kg", 1), ("Soy sauce 1 L", "Bottle", 2),
                  ("Cheese crackers", "Bundle of 10", 1)], timedelta(minutes=35))

    # Other shoppers: a reseller's bulk orders (one priced at wholesale), a delivery, and one the store cancelled.
    bulk = order("marites", [("Instant pancit canton 60 g", "Box of 72", 1), ("3-in-1 coffee sachet", "Box of 30", 2),
                             ("Candles, big", "Piece", 4)], 2 * days, voucher="SUKI50")
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

    fill_payments(s, store, people, order, price, moment, ramon_home)
    fill_fulfilment(s, store, people, order, price, moment, areas, home)
    fill_counter(s, store, people, units, moment)

    # Ana's favorites: what she buys every week.
    from .models import Favorite
    for name in ("Bottled water 500 mL", "Instant pancit canton 60 g", "3-in-1 coffee sachet", "Well-milled rice"):
        product = s.scalar(select(Product).where(Product.store_id == store.id,
                                                 (Product.title == name) | (Product.name == name))
                           .order_by(Product.id).limit(1))
        s.add(Favorite(user_id=people["ana"].id, product_id=product.id, created_at=moment - 20 * days))

    # Something in Ana's cart, so the cart page has an example.
    checkout.add_to_cart(s, people["ana"], units[("Bottled water 500 mL", "Case of 24")], 1, moment)
    checkout.add_to_cart(s, people["ana"], units[("Instant pancit canton 60 g", "Pack")], 6, moment)
    s.flush()


# ---------- Stage 3: paying online (with the Sandbox, the demo's practice gateway) ----------

def fill_payments(s, store, people, order, price, moment, ramon_home):
    """Ana has one order paid with GCash and one waiting for payment (her first try failed);
    Ramon's paid order was cancelled and refunded. All go through payments.py, like the real thing."""
    from . import payments
    from .gateways import Sandbox
    from .models import DELIVERY

    sandbox = Sandbox()
    minutes, hours = timedelta(minutes=1), timedelta(hours=1)

    def urls(payment):
        return "", ""

    def pay(placed, ago, result="pay", paid_with="gcash"):
        payment = payments.start(s, placed, sandbox, moment - ago, urls)
        event = payments.PAID_EVENT if result == "pay" else payments.FAILED_EVENT
        payments.handle_event(s, "sbx_evt_" + secrets.token_urlsafe(10), event, payment.session_id, f"sbx_pay_{payment.id}",
                              paid_with, moment - ago + 2 * minutes)
        return payment

    paid = order("ana", [("3-in-1 coffee sachet", "Box of 30", 1), ("Bath soap 90 g", "Pack of 6", 1)], 5 * hours,
                 online=True)
    price(paid, 4 * hours)
    pay(paid, 3 * hours)

    waiting = order("ana", [("Brown sugar", "Kilo", 2), ("Evaporated milk 370 mL", "Can", 4),
                            ("All-purpose flour", "Kilo", 1)], 50 * minutes, online=True,
                    note="For a birthday cake this weekend.")
    price(waiting, 30 * minutes)
    pay(waiting, 20 * minutes, result="fail", paid_with="card")

    refunded = order("ramon", [("Well-milled rice", "Sack of 25 kg", 1)], 2 * timedelta(days=1), DELIVERY,
                     ramon_home, online=True)
    price(refunded, 2 * timedelta(days=1) - hours, wholesale_bulk=False)
    pay(refunded, 2 * timedelta(days=1) - 2 * hours, paid_with="paymaya")
    s.flush()
    payments.refund(s, refunded, people["lorna"], moment - timedelta(days=1),
                    "Sorry, the rice sack was torn when it arrived from the supplier.", sandbox)


# ---------- Stage 4: packing, pickup, the rider, and the stock history ----------

def fill_fulfilment(s, store, people, order, price, moment, areas, ana_home):
    """Something in every column of the Packing board, a delivery out with Jun, cash Jun still
    holds, yesterday's cash hand-over, and restocks and a count in the stock history."""
    from . import fulfil
    from .models import DELIVERY, Address

    minutes, hours, days = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)
    lorna, jun = people["lorna"], people["jun"]
    nena = person(s, store, "Nena Bautista", "nena.bautista@example.com", SHOPPER, "09221234567", moment, 30)
    carlo = person(s, store, "Carlo Mendoza", "carlo.mendoza@example.com", SHOPPER, "09231234567", moment, 25)
    lito = person(s, store, "Lito Ramos", "lito.ramos@example.com", SHOPPER, "09241234567", moment, 15)
    people.update(nena=nena, carlo=carlo, lito=lito)

    def address(user, street, area_name, landmark=""):
        new = Address(user_id=user.id, label="Home", area_id=areas[area_name].id, street=street, landmark=landmark,
                      created_at=moment - 10 * days)
        new.area = areas[area_name]
        s.add(new)
        return new

    lito_home = address(lito, "8 Bonifacio Street", "Santa Cruz", "the sari-sari store with a green roof")
    carlo_home = address(carlo, "21 Aguinaldo Street", "Bagong Silang")

    def through(placed, steps, ago, rider=None):
        """Walk an order through steps ("pack", "packed", "send_out", "picked_up", "delivered"),
        one minute apart, ending ago before now."""
        at = moment - ago - len(steps) * 10 * minutes
        for step in steps:
            at += 10 * minutes
            if step == "pack":
                fulfil.start_packing(s, placed, lorna, at)
            elif step == "packed":
                fulfil.packed(s, placed, lorna, at)
            elif step == "send_out":
                fulfil.send_out(s, placed, rider or jun, lorna, at)
            elif step == "picked_up":
                fulfil.hand_over(s, placed, lorna, at)
            elif step == "delivered":
                fulfil.delivered(s, placed, rider or jun, "rider", at)
        return placed

    # Last week Ana's delivery came, and the cash was handed over the next day.
    old = order("ana", [("Sardines in tomato sauce 155 g", "Can", 10), ("Iodized salt 500 g", "Pack", 2)], 7 * days,
                DELIVERY, ana_home)
    price(old, 7 * days - hours, wholesale_bulk=False)
    through(old, ["pack", "packed", "send_out", "delivered"], 7 * days - 3 * hours)
    fulfil.remit(s, store, jun, [payment.id for payment in fulfil.cash_with(s, jun)], lorna, moment - 6 * days)

    # Yesterday: Carlo's delivery, cash handed over in the evening.
    yesterday = order("carlo", [("Cooking oil 1 L", "Case of 12", 1)], days + 6 * hours, DELIVERY, carlo_home)
    price(yesterday, days + 5 * hours)
    through(yesterday, ["pack", "packed", "send_out", "delivered"], days + 2 * hours)
    fulfil.remit(s, store, jun, [payment.id for payment in fulfil.cash_with(s, jun)], lorna, moment - days + 3 * hours)

    # This morning: Nena picked up her wholesale order; Lito's delivery came (Jun still has that cash).
    picked = order("nena", [("Instant noodles, chicken 55 g", "Box of 72", 1), ("Candles, big", "Pack of 10", 2)],
                   5 * hours)
    price(picked, 4 * hours + 30 * minutes, note="Wholesale for the box of noodles.")
    through(picked, ["pack", "packed", "picked_up"], 3 * hours)
    morning = order("lito", [("Brown sugar", "Kilo", 3), ("Dishwashing liquid 250 mL", "Bottle", 2)], 4 * hours,
                    DELIVERY, lito_home)
    price(morning, 3 * hours + 40 * minutes, wholesale_bulk=False)
    through(morning, ["pack", "packed", "send_out", "delivered"], 2 * hours)

    # On the board now: packing, ready for pickup, ready to go out, out with Jun.
    packing = order("nena", [("Laundry powder 70 g", "Strip of 6", 4), ("Bath soap 90 g", "Bar", 6)], 2 * hours,
                    note="Please pack the soap separately.")
    price(packing, 100 * minutes)
    through(packing, ["pack"], 15 * minutes)
    ready = order("carlo", [("Soy sauce 1 L", "Case of 12", 1), ("Cane vinegar 1 L", "Bottle", 3)], 3 * hours)
    price(ready, 150 * minutes)
    through(ready, ["pack", "packed"], 40 * minutes)
    to_send = order("lito", [("Well-milled rice", "Kilo", 10)], 2 * hours, DELIVERY, lito_home)
    price(to_send, 100 * minutes, wholesale_bulk=False)
    through(to_send, ["pack", "packed"], 25 * minutes)
    out = order("carlo", [("Bottled water 500 mL", "Case of 24", 2), ("Chocolate bar 30 g", "Bar", 5)],
                90 * minutes, DELIVERY, carlo_home, note="Leave it with the guard if I am not home.")
    price(out, 80 * minutes, wholesale_bulk=False)
    through(out, ["pack", "packed", "send_out"], 10 * minutes)

    # The stock history: deliveries from the supplier and a monthly count.
    units = {}
    for product in s.scalars(select(Product).where(Product.store_id == store.id).order_by(Product.id)):
        units.setdefault(product.group_title, product)
    stock.restock(s, units["Cup noodles, beef"], 24, lorna, moment - 3 * days, "Supplier: Mabuhay Trading, receipt 4417")
    stock.restock(s, units["Cooking oil 1 L"], 24, people["ana"], moment - 2 * days, "Two cases from the market")
    soap = units["Bath soap 90 g"]
    s.refresh(soap)
    stock.count(s, soap, soap.stock_pieces - 2, soap.stock_pieces, lorna, moment - days, "Two bars were wet. Thrown away.")
    s.flush()


# ---------- Stage 5: the counter ----------

def fill_counter(s, store, people, units, moment):
    """Ana's shift is open today with a few sales (one wholesale, one voided); Lorna's two shifts
    from earlier are closed, one with the drawer 20 pesos short."""
    from . import counter
    from .models import RETAIL, WHOLESALE

    hours, days = timedelta(hours=1), timedelta(days=1)

    def line(product, unit, quantity, kind=RETAIL):
        return [units[(product, unit)].id, quantity, kind]

    def sale(shift, cashier, ticket, cash, at):
        done, _ = counter.sell(s, store, shift, cashier, ticket, centavos(cash), counter.new_key(), at)
        return done

    lorna = people["lorna"]
    for days_ago, short in ((2, 0), (1, 20)):
        opened = moment - days_ago * days - 10 * hours
        shift = counter.open_shift(s, store, lorna, centavos(1000), opened)
        sale(shift, lorna, [line("Bottled water 500 mL", "Bottle", 3), line("Cheese crackers", "Pack", 5)], 100,
             opened + hours)
        sale(shift, lorna, [line("Instant noodles, chicken 55 g", "Box of 72", 1, WHOLESALE)], 1000, opened + 3 * hours)
        sale(shift, lorna, [line("Candles, big", "Piece", 4), line("Assorted candies", "Piece", 20)], 100,
             opened + 6 * hours)
        expected = counter.summary(s, shift, opened + 9 * hours)["expected"]
        counter.close_shift(s, shift, expected - centavos(short),
                            "Short 20 pesos. Maybe wrong change at lunch." if short else "", opened + 9 * hours)

    ana = people["ana"]
    shift = counter.open_shift(s, store, ana, centavos(1500), moment - 3 * hours)
    sale(shift, ana, [line("Soy sauce 1 L", "Bottle", 1), line("Cane vinegar 1 L", "Bottle", 1)], 100,
         moment - 2 * hours)
    sale(shift, ana, [line("Corned beef 150 g", "Case of 48", 1, WHOLESALE),
                      line("Laundry powder 70 g", "Strip of 6", 10, WHOLESALE)], 3000, moment - 90 * timedelta(minutes=1))
    oops = sale(shift, ana, [line("Cola 1.5 L", "Case of 12", 1)], 1000, moment - hours)
    counter.void(s, oops, ana, moment - hours + 3 * timedelta(minutes=1), "Rang up a case instead of one bottle")
    sale(shift, ana, [line("Cola 1.5 L", "Bottle", 1), line("Corn chips 100 g", "Bag", 2)], 200,
         moment - hours + 5 * timedelta(minutes=1))
    s.flush()


# ---------- Stage 6: promos ----------

def fill_promos(s, store, moment):
    """Two codes on the home page, one that starts in December, one that ended. Returns them by code."""
    from .dates import local_today
    from .models import VOUCHER_AMOUNT, VOUCHER_DELIVERY, VOUCHER_PERCENT, Voucher

    today = local_today(moment)
    made = {}
    for code, kind, value, min_spend, max_uses, starts, ends, description, active in (
            ("SUKI50", VOUCHER_AMOUNT, centavos(50), centavos(1000), 100, None, today + timedelta(days=30),
             "For our suki: 50 pesos off big orders.", True),
            ("LIBRENGHATID", VOUCHER_DELIVERY, 0, centavos(500), None, None, None,
             "Our rider brings it for free.", True),
            ("PASKO10", VOUCHER_PERCENT, 10, 0, 20, date(today.year, 12, 1), date(today.year, 12, 31),
             "Christmas: 10 percent off.", True),
            ("BAGONGTAON", VOUCHER_AMOUNT, centavos(30), 0, 50, today - timedelta(days=300), today - timedelta(days=280),
             "New Year promo.", True)):
        voucher = Voucher(store_id=store.id, code=code, kind=kind, value=value, min_spend=min_spend,
                          max_uses=max_uses, starts_on=starts, ends_on=ends, description=description,
                          is_active=active, used_count=12 if code == "BAGONGTAON" else 0,
                          created_at=moment - timedelta(days=40))
        s.add(voucher)
        made[code] = voucher
    s.flush()
    return made


def fill_variants(s, store, made):
    """Turn some products into brands and kinds: the first kind is the CATALOG product."""
    from .catalog import compose_name
    added = 0
    for title, (brand, kind, extras) in VARIANTS.items():
        base, units, created = made[title]
        base.title, base.brand, base.kind = title, brand, kind
        base.name = compose_name(title, brand, kind)
        for number, (other_brand, other_kind, stock_pieces, more) in enumerate(extras, start=1):
            at = created + timedelta(minutes=number)
            product = Product(store_id=store.id, category_id=base.category_id, title=title, brand=other_brand,
                              kind=other_kind, name=compose_name(title, other_brand, other_kind),
                              description=base.description, stock_pieces=stock_pieces,
                              low_stock_pieces=base.low_stock_pieces, created_at=at, updated_at=at)
            for unit_name, pieces, retail, wholesale in units:
                product.units.append(ProductUnit(name=unit_name, pieces_per_unit=pieces,
                                                 retail_price=centavos(retail + more * pieces),
                                                 wholesale_price=centavos(wholesale + more * pieces)))
            s.add(product)
            s.flush()
            starting_stock(s, product, None, at)
            added += 1
    s.flush()
    return added
