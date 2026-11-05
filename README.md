# DFJC Store

DFJC Store is a website for a real store. The store sells **wholesale and retail**, like a big sari-sari store. People can buy one piece, one pack, or a whole case.

This is **stage 1 of 8**. Now you can look at the products and their prices. Ordering, paying and the counter come in the next stages. The full plan is in [docs/PLAN.md](docs/PLAN.md).

![The home page](docs/screenshots/home.png)

## What you can do now

**Shoppers** (anyone can look; you need an account to order later):

- See the products by category, like Drinks or Canned Goods.
- Search for a product. Sort by name, by price, or the newest first.
- See every way to buy a product (a bottle, a case of 24) and the price of one piece.
- See if a product is in stock, almost gone, or out of stock.
- Make an account with your name, email and mobile number.
- Forgot your password? The store emails you a link to make a new one.

**The owner:**

- Add products with a photo, a category and the number of pieces in stock.
- Give each product units, like "Piece", "Pack of 6" or "Case of 24".
- Each unit has a **retail price** (shoppers see it) and a **wholesale price** (the cashier gives it).
- See products that are running low.
- Invite a cashier or a rider with a one-time link.
- Change the store's name, address, phone and opening hours.

**The cashier** can see all products, stock and wholesale prices, but can't change them.

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
