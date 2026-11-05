# Putting DFJC Store Online

These steps put the store on the internet for free: the website on **Render**, the data on **Neon**. Do them in order. Each one says where to click.

You need: this GitHub repo, a Neon account, a Render account, and (for online payments) a PayMongo account. All are free.

## 1. The database (Neon)

1. Go to [neon.tech](https://neon.tech) and log in.
2. Press **New Project**. Name: `dfjc-store`. Region: **Asia Pacific (Singapore)**. Press **Create**.
3. On the project page, press **Connect**. Copy the **connection string**. It starts with `postgresql://`.
4. Keep it secret: it has the database password in it. Never put it in a file in the repo or in a chat.

## 2. Check the tests on PostgreSQL (optional, but good)

The tests have only run on SQLite so far. Make a separate, empty Neon branch for them:

1. In Neon, open **Branches**, press **New Branch**, name it `test`. Copy its connection string.
2. In PowerShell, in the project folder:

```
$env:TEST_DATABASE_URL = "postgresql://...the test branch's string..."
.venv\Scripts\python -m pytest
Remove-Item Env:TEST_DATABASE_URL
```

It is slow over the internet (maybe 20 minutes). If your connection drops, run `.venv\Scripts\python -m pytest --lf` to run only the ones that didn't finish.

## 3. Make the tables and your sister's login (from your computer)

Render's free plan has no command line, so do this once from your own computer, with the **main** branch's string:

```
$env:DATABASE_URL = "postgresql://...the main branch's string..."
.venv\Scripts\flask --app dfjc upgrade-db
.venv\Scripts\flask --app dfjc create-owner
Remove-Item Env:DATABASE_URL
```

`create-owner` asks for her name, email and a password. That login can do everything: products, prices, staff, promos.

## 4. The website (Render)

1. Go to [render.com](https://render.com) and log in with GitHub.
2. Press **New**, then **Blueprint**. Choose the `ecommerce_store` repo.
3. Render reads `render.yaml` and asks for the values it doesn't know:
   - `DATABASE_URL`: the Neon **main** branch's connection string.
   - `PAYMONGO_SECRET_KEY` and `PAYMONGO_WEBHOOK_SECRET`: leave empty for now (step 5).
   - `MAIL_USERNAME` and `MAIL_PASSWORD`: leave empty for now (step 6).
4. Press **Apply**. The first deploy takes a few minutes. Then open the address Render shows (like `https://dfjc-store.onrender.com`).
5. Log in with your sister's email and password.

The free plan sleeps after 15 quiet minutes. The next visit wakes it up in about 30 to 50 seconds.

## 5. Online payments (PayMongo, test mode)

Test mode moves no real money. Use it until everything works.

1. Make an account at [paymongo.com](https://www.paymongo.com) and open the dashboard.
2. Open **Developers**, then **API keys**. Copy the **secret key** that starts with `sk_test_`.
3. Open **Developers**, then **Webhooks**, and add one:
   - URL: `https://<your Render address>/payments/webhook`
   - Event: `checkout_session.payment.paid`
   - Copy its **secret key** (it starts with `whsk_`).
4. On Render, open the service, then **Environment**. Set `PAYMONGO_SECRET_KEY` and `PAYMONGO_WEBHOOK_SECRET`. Save; Render deploys again.
5. Try it: sign up as a shopper, order something for at least ₱20, choose **Pay online**. Log in as the owner and price it. As the shopper, press **Pay**. PayMongo's test page lets you approve or fail the payment, and its docs list test card numbers.
6. Check: the order says **Paid**. In PayMongo, **Webhooks** shows the event was delivered.

To take real money later, PayMongo checks the business first. Then swap the `sk_test_` and `whsk_` values for the live ones.

## 6. Emails (Gmail)

Order emails and password-reset links go out through a Gmail account.

1. Use a Gmail account for the store. In **Google Account**, open **Security** and turn on **2-Step Verification**.
2. Search the account settings for **App passwords**. Make one named `DFJC Store`. Copy the 16 letters.
3. On Render, set `MAIL_USERNAME` (the Gmail address) and `MAIL_PASSWORD` (the 16 letters). Save.
4. Try **Forgot your password?** on the login page. The email should arrive in a minute.

## 7. Staff, delivery areas and products

As the owner, on the live site:

1. **Store Details**: the address, phone, opening hours and the pickup note.
2. **Delivery Areas**: the barangays the rider goes to, and the fee for each.
3. **Staff**: make an invite link for each cashier and rider. Send it to them by Messenger or text. Each link works once.
4. **Products**: add them one by one with photos, or many at once with **Import from a file**.

## After a change

Every merge into `main` deploys by itself. Once, a deploy didn't start after a merge (on another project): if the site doesn't change, open the service on Render and press **Manual Deploy**.
