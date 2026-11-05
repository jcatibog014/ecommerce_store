# DFJC Store — online + counter store for a wholesale/retail tindahan

## Context
The user wants a mini e-commerce store to practise **transactions, payment integration and order state**. It is for their sister's real store, **DFJC**: a wholesale and retail store, like a big sari-sari store. Their choices:
- product catalog, shopping cart, checkout and order history, with **PayMongo test mode** for online payment **plus cash** (cash at pickup, cash on delivery, cash at the counter);
- **accounts only**: shoppers must sign up to order;
- **the cashier decides wholesale or retail**. An online order is placed at retail price. The cashier reviews it and picks Retail or Wholesale (for the whole order or per line), and the shopper sees the final total. Only after that can they pay;
- **pickup + delivery** (delivery fee by area);
- staff: **Owner** (the sister: everything), **Cashier**, **Rider**;
- extras: Store admin tools, Shopper extras, Promos, Insights + PWA, and **walk-in sales** (counter/POS);
- the look: "Bright tindahan" (white cards, warm yellow + red like a store sign), plus a **dried bamboo/wood accent**;
- **one PR per stage**: I stop after each stage for review.

## Progress
- [x] Stage 1: setup and catalog (PR #1)
- [x] Stage 2: cart, checkout and order state
- [x] Stage 3: payments (stages 2 to 8 share one PR, branch `stages-2-to-8`, a commit per stage)
- [x] Stage 4: fulfilment and store admin
- [x] Stage 5: walk-in sales
- [x] Stage 6: shopper extras and promos
- [x] Stage 7: insights and phone app
- [ ] Stage 8: demo, README and deploy

## Setup
- Repo: https://github.com/jcatibog014/ecommerce_store (made by the user). First step after approval: `git clone` it into a new folder `C:\Users\USER\Documents\Workspace\ecommerce_store`, make `.venv`, and branch `stage-1-setup`. Package `dfjc`, local port **5009** (ShiroMoto has 5008).
- Stack as Hire Wire / Plan-It: Flask 3, SQLAlchemy 2.0 typed models, Alembic, SQLite locally, Neon Postgres live, pytest, `.venv`, `AGENTS.md` + `CLAUDE.md`. New: `requests` for the PayMongo API, Pillow (product photos stored as WebP in the DB, like Sangkap).
- Copy from Hire Wire (`job_application_tracker\hirewire`):
  - `db.py`, including `upgrade_db` with the SQLite foreign-keys fix (around :106);
  - `auth.py`: `csrf_token`/`check_csrf`, login lockout, `safe_next`, `login_required`, `not_for_demo`, the demo pattern, `create_user_command`;
  - `workflow.py` (`TRANSITIONS`, `MoveNotAllowed`, `can_move`, `move`) as the model for the order state machine;
  - `formatting.py` (`money`, `nice_datetime`), `dates.py`;
  - toasts in `app.js`, `data-watch-changes`, `docs/take_screenshots.py`, `tests/test_readme.py`, `tests/test_look.py`, `tests/test_demo.py`.
- Copy from Plan-It: the app factory with CSP and Render checks, the "View as" demo switch, the `seed.py` builder style, `docs/make_icons.py`, `site.webmanifest`, the theme radio in Settings.
- Money is stored as integer centavos (₱). Time zone `Asia/Manila`.

## People and roles
| Role | Can do |
|---|---|
| Owner | everything: products, prices, staff, delivery areas, promos, Insights, Settings |
| Cashier | review and price online orders (Retail/Wholesale), take cash, walk-in sales, packing, stock, refunds |
| Rider | My deliveries, mark delivered / failed, record cash collected (COD), hand cash to the cashier |
| Shopper | browse, cart, checkout, pay, order history, addresses, favorites |

The owner is made with `flask create-owner`. Staff join by an invite link. Shoppers sign up with name, mobile and email. Password reset is by an email link.

## Data model (main tables)
- **Catalog:** `categories`; `products` (name, description, photo, active, `stock_pieces`, `reserved_pieces`, low-stock level, `version`); `product_units` (piece / pack / box / case, `pieces_per_unit`, **retail price**, **wholesale price**, optional barcode). Stock is counted in pieces, so a case of 24 takes 24 pieces.
- **Shoppers:** `users` (role), `addresses` (label, barangay, street, landmark), `delivery_areas` (barangay → fee, active).
- **Cart:** `carts` / `cart_items` (one cart per user; unit + qty).
- **Orders:** `orders` (ref like `DF-7K3Q`, user, fulfilment pickup/delivery, address snapshot, status, `price_type` retail/wholesale/mixed, subtotal, delivery fee, discount, total, `version`, unique `idempotency_key`, timestamps); `order_lines` (product, unit, qty, pieces, **price snapshot**, line `price_type`); `order_events` (status timeline: who, when, note).
- **Payments:** `payments` (order, method `paymongo` / `cash_pickup` / `cod` / `cash_counter`, status, amount, PayMongo checkout session id, payment id, paid_at); `payment_events` (webhook log, **UNIQUE `event_id`**); `refunds`.
- **Stock:** `stock_moves` (reason: order, cancel, walk-in, restock, adjust, return; pieces ±; who).
- **Counter:** `walkin_sales` + `walkin_lines` (price type picked per sale, cash given, change); `cash_shifts` (opening cash, expected, counted, difference); `remittances` (rider cash → cashier).
- **Promos:** `vouchers` (code, ₱ or %, min spend, start/end, max uses, `used_count`, free delivery); `voucher_uses` with **UNIQUE(voucher_id, user_id)**.
- **Extras:** `favorites`, `notifications` (email outbox).

## Order states (`dfjc/orders.py`, the state machine)
```
placed ──cashier prices──> awaiting_payment (online)  ──paid──> paid ──> packing ──> ready_for_pickup ──> picked_up
   │                    └> confirmed (cash)  ───────────────────────────┘            └> out_for_delivery ──> delivered
   └──> cancelled (by shopper before pricing, by cashier any time before packing; paid orders → refunded)
```
- Payment status is kept separate from order status: unpaid → pending → paid / failed / expired → refunded.
- A failed or expired online payment goes back to `awaiting_payment` (try again, or switch to cash).
- A failed delivery goes back to `ready`. A COD order counts as paid when the rider records the cash.
- Every move goes through `move()`. It checks `TRANSITIONS`, uses a conditional `UPDATE orders SET status=? WHERE id=? AND status=?`, writes an `order_events` row and queues a notice. An illegal move raises `MoveNotAllowed`.

## How the practice skills show up
**Transactions:**
- placing an order is one transaction: re-read the cart, check prices and stock, reserve pieces, create the order and its lines with price snapshots, use the voucher, empty the cart. Either all of it happens or none of it;
- the cashier's pricing re-prices every line, recomputes the voucher and total, and moves the status, all together;
- the paid webhook updates the payment and the order together. Cancel releases the reserved stock. A refund updates the payment, the order and the stock in one transaction;
- a walk-in sale takes away stock, records the cash and writes the receipt in one transaction.

**Concurrency and safety:**
- stock is never oversold: `UPDATE products SET reserved_pieces = reserved_pieces + q WHERE stock_pieces - reserved_pieces >= q`. If no row changes, the shopper sees "Only N left" and nothing is saved. On Postgres there is also `SELECT … FOR UPDATE`;
- the voucher limit uses a conditional `used_count` update, and the UNIQUE use row stops a second use;
- an idempotency key on the checkout form stops a double-tap from placing 2 orders;
- the `version` column means two cashiers can't price the same order at once;
- tests: 10 threads race for the last 3 pieces and exactly the right number win (SQLite file DB, and Neon via `TEST_DATABASE_URL`).

**Payment integration** (`dfjc/payments.py`, a small gateway interface with `PayMongoGateway` and `FakeGateway`):
- create a PayMongo **Checkout Session** (test keys, ₱, GCash / Maya / card; reference = order ref) and redirect to its page;
- `POST /payments/webhook` checks the `Paymongo-Signature` HMAC (`t.payload`, test-mode key). It is idempotent through the UNIQUE `event_id`, and a replay or out-of-order event changes nothing;
- the return page (and a "Check payment" button) asks PayMongo for the session's status. This matters locally, where webhooks can't reach `localhost`, and when Render's free plan is asleep;
- unpaid sessions expire, and the order goes back to "awaiting payment";
- refunds use PayMongo's refund API in test mode, and cash refunds are recorded by the cashier;
- the fake gateway (a built-in "Sandbox pay" page) runs the tests and the demo with no keys. Keys come from env vars `PAYMONGO_SECRET_KEY`, `PAYMONGO_PUBLIC_KEY` and `PAYMONGO_WEBHOOK_SECRET`, and are never in the repo.

## Look: "Bright tindahan + bamboo"
- White cards on a warm off-white page. Warm yellow (store-sign yellow) as the main accent, red for prices and sale tags (contrast checked), and a **dried bamboo / wood** tone (around #c9a66b / #a67c52) for the header strip, category shelves and dividers. A faint woven-bamboo texture appears only on the login panel and the empty states.
- A friendly rounded font (Nunito or Baloo 2, self-hosted). Dark mode with warm dark wood tones.
- The login panel takes 1/3 of the screen. Dark mode and Log out go in Settings. Few emoji, aligned controls.
- Product photos are uploaded by the owner. Demo products use generic names (no brand logos) and a neat placeholder tile when there is no photo.

## Pages
- **Shopper:** Home (categories, best sellers, promos); Catalog (search, category filter, sort, unit picker with retail price); Product page; Cart; Checkout (pickup or delivery, address, voucher, payment choice: online or cash); Order page (status timeline, "Waiting for the cashier's price", Pay now, cancel); Order History (filters, Buy again); Favorites; Addresses; Settings.
- **Staff:**
  - Orders queue: tabs for To price, Awaiting payment, To pack, Ready, Out for delivery;
  - Price order: a Retail/Wholesale switch per order or per line, with a live total;
  - Counter (walk-in sale): search or scan, Retail/Wholesale, cash given and change, printable receipt; Cash shift;
  - Deliveries: the rider's list, delivered/failed, COD cash, remit;
  - Products, Units & prices, Stock (moves, low-stock list, restock, CSV import), Delivery areas, Promos, Staff, Insights (owner), Settings.

## Stages (one PR each; I stop after each for review)
1. **Setup and catalog.** Scaffold from Hire Wire / Plan-It: the theme with the bamboo accent, sidebar, roles, `create-owner`, staff invites, shopper sign-up and email reset. Products, categories, units with retail and wholesale prices, photos, and the public catalog with search and filters. A demo with View As (Shopper / Cashier / Rider / Owner).
2. **Cart, checkout and order state.** Cart, addresses, delivery areas and fees, the checkout transaction with stock reservation and the idempotency key, the order state machine and timeline, the cashier's **Price order** (Retail/Wholesale), cancel, Order History. The oversell race tests.
3. **Payments.** The gateway interface, PayMongo Checkout Sessions in test mode, the signed idempotent webhook, the return-page check, expiry, cash methods (pickup, COD), refunds, and the fake gateway with Sandbox pay. Tests for webhook replay, a bad signature, out-of-order events and a double return.
4. **Fulfilment and store admin.** The orders queue board, packing, ready for pickup and picked up, rider deliveries (assign, delivered/failed, COD cash, remittance), stock moves, the low-stock list, restock and adjust, CSV product import, optimistic locking.
5. **Walk-in sales.** The counter screen (Retail/Wholesale per sale, cash and change, a receipt), cash shifts (open, close, count difference), and walk-in stock moves that share the same stock guard as online orders.
6. **Shopper extras and promos.** Favorites, Buy again from history, saved addresses, order status emails (Gmail app password, outbox), and voucher codes (₱ or %, min spend, one use per account, free delivery, limited uses with a race test).
7. **Insights and phone app.** Sales per day (online vs counter), best sellers, retail vs wholesale share, payment method split, an average order, low stock. Colours are checked with the dataviz validator. A PWA: manifest, `sw.js`, offline page, Install buttons, iPhone steps in Settings.
8. **Demo, README and deploy.** A realistic week of sample orders in every state, walk-in sales, shifts and promos, so every demo page has examples (`tests/test_demo.py`). App icons, README screenshots (`docs/take_screenshots.py`, `tests/test_readme.py`), `render.yaml` (`dfjc-store`, with `upgrade-db` before gunicorn), and step-by-step notes for the user: Neon, Render, PayMongo test keys and the webhook URL.

Each stage updates `docs/PLAN.md` (this plan, copied in during stage 1), adds tests, and adds sample data for its own features. Each PR retakes the screenshots of pages it changed.

## Verification (every stage)
- `pytest` passes on SQLite. From stage 2, the race tests also run against a Neon `test` branch via `TEST_DATABASE_URL`.
- `flask --app dfjc run --port 5009`, then click through the stage's pages as each View As role, in light and dark mode, and at phone width (390 px) with Playwright borrowed from expense_tracker's `.venv`.
- Stage 2 by hand: two browsers order the last items at the same time. One succeeds and the other sees "Only N left".
- Stage 3 by hand, with PayMongo test keys: pay an order with test GCash, a test card and a failed card. Check that the order moves only after the payment is confirmed, and that resending the same webhook (PayMongo dashboard or a saved payload) changes nothing.
- Stage 5: a counter sale's change and the shift's expected cash add up. Stock drops the same as for an online order.
