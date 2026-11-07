# DFJC Store

DFJC Store is the website of a real store that sells **wholesale and retail**, like a big sari-sari store. People can buy one piece, one pack, or a whole case.

Shoppers order online for pickup or delivery, and pay in cash or online (GCash, Maya or a card, through PayMongo). The cashier checks every order and gives the wholesale price where it applies. The store also sells to walk-in customers at the counter. The plan, stage by stage, is in [docs/PLAN.md](docs/PLAN.md).

![The home page](docs/screenshots/home.png)

## What It Does

**Shoppers** (anyone can look; you need an account to order):

- See the products by category, search, and sort by name, price or newest.
- Choose the **brand** and the **kind** (like Original or Creamy White) of a product. Each one has its own price and stock.
- Not in the list? **Ask for it**: type the brand or kind you want, the size and how many. The cashier checks if the store has it and gives the price before you pay.
- Choose the size in **Available in** (like Sachet, Pack of 10 or Box of 30: the store types these), see its price, pick how many and add it to the cart.
- Put products in a cart and check out: **pickup** at the store, or **delivery** to your barangay (each barangay has its own fee).
- Pay in cash (at the counter, or to the rider), or **pay online** after the cashier prices the order.
- Type a **voucher code** at checkout (like SUKI50) for pesos off, percent off, or free delivery.
- Follow every order in **My Orders**, cancel it before packing starts, or press **Buy again** later.
- Save **Favorites** and **addresses**, and get an **email** when an order changes (you can turn it off).
- Make an account with your name, email and mobile number. Forgot your password? The store emails you a link.

**The cashier:**

- Sees new online orders in **Orders**, chooses **Retail** or **Wholesale** for each line (or all at once), and confirms the price. Then the shopper sees the final total.
- For each asked-for item, chooses **We have it** (and types its price) or **Not available**.
- Moves orders along the **Packing** board: To Pack, Packing, Ready, Out for Delivery (the orders out with a rider are here). Starting to pack takes the items off the shelf.
- Hands over pickup orders (and takes the cash), or sends delivery orders out with a rider. Receives the riders' cash (**Rider Cash**).
- Cancels an order with a reason, or gives the money back (a **refund**) for an order paid online.
- Sells to walk-in customers at the **Counter**: type a name or scan a barcode, choose retail or wholesale, type the cash given, see the change, and print the receipt. A mistake can be voided.
- Opens a **shift** with the cash in the drawer and closes it with a count. The store sees if the drawer has more or less than it should.
- Adds new stock (**Restock**) and saves **stock counts**. Every change is in the **Stock History**.
- Adds and changes products, brands, kinds, sizes, prices, photos and categories, like the owner. Everything shoppers see is typed by the owner or a cashier.

**The rider:**

- Sees the deliveries with them in **My Deliveries**: the address, the phone number and how much cash to collect.
- Presses **Delivered**, or **Nobody home** to bring the order back, and sees the cash to hand to the cashier.

**The owner** does everything the cashier does, and also:

- (With the cashier) adds products with photos and sizes (like "Sachet", "Pack of 6" or "Case of 24"), with a **retail** and a **wholesale** price for each size. Many products can come at once from a spreadsheet (**Import from a file**).
- (With the cashier) gives a product brands and kinds: the same product name with another brand or kind shows as **one card** in the shop. **Add another kind** copies a product with its sizes and prices, so only the brand, kind and stock need typing.
- Sets up the store: its details and opening hours (in **Settings**), **Delivery Areas** and their fees, **Staff** (a one-time invite link for each cashier and rider), and **Promos** (voucher codes).
- Sees **Insights** for the last 7, 30 or 90 days: sales per day (online and counter), the best sellers, how people paid, how much sold at the wholesale price, and what is running low.

## How an Order Works

1. The shopper checks out. The store **holds** those pieces, so nobody else can buy them.
2. The order waits for the cashier. The shopper sees an **estimated** total at retail price.
3. The cashier chooses retail or wholesale for each line. The shopper sees the **final** total.
4. The shopper pays: in cash at pickup or on delivery, or online right away on PayMongo's page.
5. The cashier packs the order. Pickup orders wait at the counter; delivery orders go out with a rider.
6. The shopper picks it up, or the rider delivers it. Cash is recorded with the person who took it.

## Safe by Design

This project practises **transactions**, **payments** and **order state**:

- **No overselling.** If two shoppers (or a shopper and the counter) try to buy the last pieces at the same moment, only one gets them. The other sees how many are left.
- **All or nothing.** Placing an order, pricing it, a counter sale, a CSV import: each is saved completely, or not at all.
- **No double orders.** Pressing "Place order" or "Pay" twice still makes one order or one sale.
- **Paid only once.** The store hears about an online payment in two ways (PayMongo's signed message, and the shopper coming back). Either can come first; the order is marked paid once. A message with a wrong signature is refused, and the same message sent twice changes nothing.
- **No lost money.** A payment that comes in after an order was cancelled is flagged, and the cashier sees "Give the money back".
- **Fair vouchers.** A voucher with 50 uses is used at most 50 times, each account uses a code once, and a cancelled order gives the use back.
- **Emails follow the truth.** Emails wait in an outbox saved together with the order's change. If the change fails, no email goes out. If sending fails, the order is still fine.
- **No overwriting.** If two staff change the same order or product at once, the second change is refused instead of quietly undoing the first.

## Pictures

| Page | Picture |
|---|---|
| Log in | ![Log in](docs/screenshots/login.png) |
| Sign up | ![Sign up](docs/screenshots/signup.png) |
| Home (dark mode) | ![Home in dark mode](docs/screenshots/home-dark.png) |
| All products | ![All products](docs/screenshots/products.png) |
| Search and sort | ![Search for noodles, cheapest first](docs/screenshots/search.png) |
| One product | ![A product with its units](docs/screenshots/product.png) |
| Choose the brand and kind | ![3-in-1 coffee: brand and kind](docs/screenshots/product-kinds.png) |
| Brand and kind (dark mode) | ![Brand and kind in dark mode](docs/screenshots/product-kinds-dark.png) |
| Brand and kind on a phone | ![Brand and kind on a phone](docs/screenshots/phone-product-kinds.png) |
| Not in the list? Ask for it | ![Asking for a brand that isn't listed](docs/screenshots/ask-for.png) |
| On a phone | ![Home on a phone](docs/screenshots/phone.png) |
| Products on a phone | ![Products on a phone](docs/screenshots/phone-products.png) |
| Cart | ![The cart](docs/screenshots/cart.png) |
| Cart on a phone | ![The cart on a phone](docs/screenshots/phone-cart.png) |
| Check out | ![Pickup or delivery, and payment](docs/screenshots/checkout.png) |
| My orders | ![Order history](docs/screenshots/my-orders.png) |
| An order waiting for the cashier | ![An order with an estimated total](docs/screenshots/order-waiting.png) |
| A priced order | ![An order with its final total](docs/screenshots/order-priced.png) |
| A priced order on a phone | ![A priced order on a phone](docs/screenshots/phone-order.png) |
| An order to pay online | ![Pay now, or pay in cash instead](docs/screenshots/order-pay.png) |
| The practice payment page | ![The sandbox payment page](docs/screenshots/sandbox.png) |
| A paid order | ![Paid with GCash](docs/screenshots/order-paid.png) |
| A delivered order | ![A delivered order](docs/screenshots/order-on-the-way.png) |
| Favorites | ![Saved products](docs/screenshots/favorites.png) |
| Saved addresses | ![Delivery addresses](docs/screenshots/addresses.png) |
| Settings | ![Settings](docs/screenshots/settings.png) |
| Cashier: orders to price | ![The orders queue](docs/screenshots/orders-queue.png) |
| Cashier: price an order | ![Retail or wholesale for each line](docs/screenshots/price-order.png) |
| Cashier: price an order (dark mode) | ![Pricing in dark mode](docs/screenshots/price-order-dark.png) |
| Cashier: waiting for payment | ![Orders waiting for payment](docs/screenshots/orders-waiting.png) |
| Cashier: a paid order | ![Payments and Cancel and Refund](docs/screenshots/staff-paid-order.png) |
| Cashier: a refunded order | ![A refunded order](docs/screenshots/staff-refunded-order.png) |
| Cashier: the packing board | ![To Pack, Packing, Ready, Out for Delivery](docs/screenshots/packing.png) |
| Cashier: the packing board (dark mode) | ![The packing board in dark mode](docs/screenshots/packing-dark.png) |
| Cashier: rider cash | ![Cash from the rider](docs/screenshots/rider-cash.png) |
| Cashier: the counter | ![A walk-in sale with retail and wholesale lines](docs/screenshots/counter.png) |
| Cashier: a receipt | ![The printed receipt and the change](docs/screenshots/receipt.png) |
| Cashier: close the shift | ![Expected cash and the count](docs/screenshots/close-shift.png) |
| Cashier: low stock | ![Products running low](docs/screenshots/low-stock.png) |
| Cashier: restock and count | ![The stock of one product](docs/screenshots/product-stock.png) |
| Stock history | ![Every change to stock](docs/screenshots/stock-history.png) |
| Rider: my deliveries | ![Deliveries with the rider](docs/screenshots/deliveries.png) |
| Rider: on a phone | ![My deliveries on a phone](docs/screenshots/phone-deliveries.png) |
| Staff: products | ![The staff product list](docs/screenshots/manage-products.png) |
| Staff: products (dark mode) | ![The staff product list in dark mode](docs/screenshots/manage-products-dark.png) |
| Staff: one product | ![Editing a product](docs/screenshots/manage-product.png) |
| Staff: units and prices | ![Retail and wholesale prices per unit](docs/screenshots/units.png) |
| Owner: new product | ![Adding a product](docs/screenshots/new-product.png) |
| Owner: add another kind | ![A new kind, copied from another](docs/screenshots/add-kind.png) |
| Owner: import products | ![Import from a CSV file](docs/screenshots/import.png) |
| Owner: categories | ![Categories](docs/screenshots/categories.png) |
| Owner: people and invite links | ![Staff and invite links](docs/screenshots/staff.png) |
| Owner: store details, in Settings | ![Store details in Settings](docs/screenshots/store-details.png) |
| Owner: delivery areas | ![Barangays and delivery fees](docs/screenshots/delivery-areas.png) |
| Owner: promos | ![Voucher codes and their uses](docs/screenshots/promos.png) |
| Owner: cash shifts | ![Every shift, with the drawer's difference](docs/screenshots/shifts.png) |
| Owner: insights | ![Sales per day, best sellers and payments](docs/screenshots/insights.png) |
| Owner: insights (dark mode) | ![Insights in dark mode](docs/screenshots/insights-dark.png) |
| Insights on a phone | ![Insights on a phone](docs/screenshots/phone-insights.png) |

All pictures come from **Try Demo**. The names and products in the demo are made up.

## Try the Demo

Press **Try Demo** on the login page. You get your own sample store for 2 hours. Nobody else sees it. Use **View as** at the top to be the shopper, the cashier, the rider or the owner. The demo pays on a **practice payment page**: no real money moves.

## Put It on Your Phone

DFJC Store installs like an app. On Android, open **Settings** and press **Install app** (or use Chrome's menu). On an iPhone, open the site in Safari, press Share, then **Add to Home Screen**. Without internet, it shows a small "You're offline" page; prices and orders always come fresh from the store.

## Put It Online

Step-by-step notes for Neon, Render, PayMongo test mode and Gmail are in [docs/DEPLOY.md](docs/DEPLOY.md).

## Tools We Use

| Tool | What it is | What it does here |
|---|---|---|
| Python 3.13 | A programming language | All the code on the server |
| Flask | A small web framework | Makes the pages |
| SQLAlchemy | A database library | Reads and saves products, people and orders |
| Alembic | A migration tool | Changes the database tables safely |
| SQLite | A small database in one file | The database on your own computer and in tests |
| PostgreSQL (Neon) | A big database in the cloud | The database of the live site |
| PayMongo (test mode) | A Philippine payment service | Online payments with GCash, Maya and cards |
| Gmail | Email | Order emails and password-reset links |
| Pillow | A picture library | Makes product photos small and fast |
| HTML, CSS, JavaScript | The languages of web pages | The look of the pages, and the phone app |
| pytest | A test tool | Checks that everything works |
| Playwright | A browser robot | Takes the pictures in this README, and draws the app icons |
| Render | A web host | Runs the live site |

## Run It on Your Computer

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\flask --app dfjc upgrade-db
.venv\Scripts\flask --app dfjc create-owner
.venv\Scripts\flask --app dfjc run --debug --port 5009
```

Then open http://127.0.0.1:5009. Without PayMongo keys, online payments use the practice page. Without Gmail settings, emails are printed in the terminal.

Run the tests:

```
.venv\Scripts\python -m pytest
```
