# DFJC Store

DFJC Store is a website for a real store. The store sells **wholesale and retail**, like a big sari-sari store. People can buy one piece, one pack, or a whole case.

Now shoppers can fill a cart and order for pickup or delivery. They pay in cash or online (GCash, Maya or card, through PayMongo). The cashier checks every order and gives the wholesale price where it applies. The full plan is in [docs/PLAN.md](docs/PLAN.md).

![The home page](docs/screenshots/home.png)

## What you can do now

**Shoppers** (anyone can look; you need an account to order):

- See the products by category, like Drinks or Canned Goods.
- Search for a product. Sort by name, by price, or the newest first.
- See every way to buy a product (a bottle, a case of 24) and the price of one piece.
- See if a product is in stock, almost gone, or out of stock.
- Put products in a cart, then check out.
- Choose **pickup** at the store, or **delivery** to your barangay (each barangay has its own fee).
- Pay in cash (at the counter, or to the rider), or **pay online** with GCash, Maya or a card after the cashier prices the order.
- See every order in **My Orders**: what the store is doing now, and what happened when.
- Cancel an order before the store starts packing it.
- Make an account with your name, email and mobile number.
- Forgot your password? The store emails you a link to make a new one.

**The cashier:**

- Sees new orders in **Orders**, with a number in the menu.
- Chooses **Retail** or **Wholesale** for each line (or for all lines at once). The total changes as you click.
- Confirms the price. Then the shopper sees the final total.
- Cancels an order with a reason. The items go back on sale.
- Gives the money back (a **refund**) for an order that was paid online.

**The owner:**

- Add products with a photo, a category and the number of pieces in stock.
- Give each product units, like "Piece", "Pack of 6" or "Case of 24".
- Each unit has a **retail price** (shoppers see it) and a **wholesale price** (the cashier gives it).
- See products that are running low.
- Invite a cashier or a rider with a one-time link.
- Change the store's name, address, phone and opening hours.
- Choose the barangays for delivery, and the fee for each.
- Do everything the cashier does.

**The cashier** can also see all products, stock and wholesale prices, but can't change them.

## How an order works

1. The shopper puts products in the cart and checks out. The store **holds** those pieces, so nobody else can buy them.
2. The order waits for the cashier. The shopper sees an **estimated** total at retail price.
3. The cashier chooses retail or wholesale for each line and confirms. The shopper sees the **final** total.
4. The shopper pays: in cash at pickup or on delivery, or online right away on PayMongo's page.

If two shoppers try to buy the last pieces at the same moment, only one gets them. The other sees how many are left. If someone presses "Place order" twice, there is still only one order.

## How paying online works

- The store hears about a payment in **two ways**: PayMongo sends a signed message (a "webhook"), and the shopper comes back to the store's page. Either can come first. The order is marked paid **only once**.
- A message with a wrong signature is refused. The same message sent twice changes nothing.
- If money comes in for an order that was cancelled, it is not lost: the cashier sees "Give the money back".
- The demo uses a **practice payment page** (the sandbox). No real money moves.

### Turn on PayMongo test mode (for the owner)

1. Make a free account at [paymongo.com](https://www.paymongo.com) and open the **Developers** page. Copy the **test secret key** (it starts with `sk_test_`).
2. Make a **webhook** for `https://<your site>/payments/webhook` with the event `checkout_session.payment.paid`. Copy its **secret key** (it starts with `whsk_`).
3. On Render, add two environment variables: `PAYMONGO_SECRET_KEY` and `PAYMONGO_WEBHOOK_SECRET`.
4. Order something and pay. PayMongo's test pages let you approve or fail a payment, and their docs list test card numbers.

Without the keys, your own computer uses the practice page, and the live site only takes cash.

## Pictures

| Page | Picture |
|---|---|
| Log in | ![Log in](docs/screenshots/login.png) |
| Sign up | ![Sign up](docs/screenshots/signup.png) |
| Home (dark mode) | ![Home in dark mode](docs/screenshots/home-dark.png) |
| All products | ![All products](docs/screenshots/products.png) |
| Search and sort | ![Search for noodles, cheapest first](docs/screenshots/search.png) |
| One product | ![A product with its units](docs/screenshots/product.png) |
| On a phone | ![Home on a phone](docs/screenshots/phone.png) |
| Products on a phone | ![Products on a phone](docs/screenshots/phone-products.png) |
| Staff: products | ![The staff product list](docs/screenshots/manage-products.png) |
| Staff: products (dark mode) | ![The staff product list in dark mode](docs/screenshots/manage-products-dark.png) |
| Staff: one product | ![Editing a product](docs/screenshots/manage-product.png) |
| Staff: units and prices | ![Retail and wholesale prices per unit](docs/screenshots/units.png) |
| Staff: new product | ![Adding a product](docs/screenshots/new-product.png) |
| Staff: categories | ![Categories](docs/screenshots/categories.png) |
| Staff: people and invite links | ![Staff and invite links](docs/screenshots/staff.png) |
| Staff: store details | ![Store details](docs/screenshots/store-details.png) |
| Cashier: low stock | ![Products running low](docs/screenshots/low-stock.png) |
| Settings | ![Settings](docs/screenshots/settings.png) |
| Cart | ![The cart](docs/screenshots/cart.png) |
| Cart on a phone | ![The cart on a phone](docs/screenshots/phone-cart.png) |
| Check out | ![Pickup or delivery, and payment](docs/screenshots/checkout.png) |
| My orders | ![Order history](docs/screenshots/my-orders.png) |
| An order waiting for the cashier | ![An order with an estimated total](docs/screenshots/order-waiting.png) |
| A priced order | ![An order with its final total](docs/screenshots/order-priced.png) |
| A priced order on a phone | ![A priced order on a phone](docs/screenshots/phone-order.png) |
| Cashier: orders to price | ![The orders queue](docs/screenshots/orders-queue.png) |
| Cashier: price an order | ![Retail or wholesale for each line](docs/screenshots/price-order.png) |
| Cashier: price an order (dark mode) | ![Pricing in dark mode](docs/screenshots/price-order-dark.png) |
| Owner: delivery areas | ![Barangays and delivery fees](docs/screenshots/delivery-areas.png) |
| An order to pay online | ![Pay now, or pay in cash instead](docs/screenshots/order-pay.png) |
| The practice payment page | ![The sandbox payment page](docs/screenshots/sandbox.png) |
| A paid order | ![Paid with GCash](docs/screenshots/order-paid.png) |
| Cashier: waiting for payment | ![Orders waiting for payment](docs/screenshots/orders-waiting.png) |
| Cashier: a paid order | ![Payments and Cancel and Refund](docs/screenshots/staff-paid-order.png) |
| Cashier: a refunded order | ![A refunded order](docs/screenshots/staff-refunded-order.png) |

All pictures come from **Try Demo**. The names and products in the demo are made up.

## Try the demo

Press **Try Demo** on the login page. You get your own sample store for 2 hours. Nobody else sees it. Use **View as** at the top to be the shopper, the cashier, the rider or the owner.

## Tools we use

| Tool | What it is | What it does here |
|---|---|---|
| Python 3.13 | A programming language | All the code on the server |
| Flask | A small web framework | Makes the pages |
| SQLAlchemy | A database library | Reads and saves products and people |
| Alembic | A migration tool | Changes the database tables safely |
| SQLite | A small database in one file | The database on your own computer and in tests |
| PostgreSQL (Neon) | A big database in the cloud | The database of the live site |
| Pillow | A picture library | Makes product photos small and fast |
| HTML, CSS, JavaScript | The languages of web pages | The look of the pages |
| pytest | A test tool | Checks that everything works |
| Playwright | A browser robot | Takes the pictures in this README |
| Render | A web host | Runs the live site (stage 8) |
| PayMongo (test mode) | A Philippine payment service | Online payments with GCash, Maya and cards (stage 3) |

## Run it on your computer

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\flask --app dfjc upgrade-db
.venv\Scripts\flask --app dfjc create-owner
.venv\Scripts\flask --app dfjc run --debug --port 5009
```

Then open http://127.0.0.1:5009.

Run the tests:

```
.venv\Scripts\python -m pytest
```
