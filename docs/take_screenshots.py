"""Take the README screenshots (docs/screenshots/*.png).

Retake them in every pull request that visibly changes a page, and add a new
one when a page or feature is added: the README shows everything the app can
do. tests/test_readme.py fails if the pictures and the README disagree.

Every picture uses "Try Demo", so only made-up products and people show.
It uses Microsoft Edge, which Windows already has, through Playwright.
Playwright is only a tool for this script, so it is not in requirements.txt
(the owner borrows expense_tracker's .venv, which has it):

    $env:DATABASE_URL = "sqlite:///C:/Temp/dfjc-screens.sqlite"
    .venv\\Scripts\\flask --app dfjc upgrade-db
    .venv\\Scripts\\flask --app dfjc run --port 5009      (keep this one running)
    ..\\expense_tracker\\.venv\\Scripts\\python docs\\take_screenshots.py      (in another terminal)

Every picture is one screen (no full-page shots), so they are all the same size.
Add a line to PAGES when a page is added.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5009"
OUT = Path(__file__).parent / "screenshots"
DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}

# (file name, View As role, address, theme, screen size). {...} addresses are found by address().
PAGES = [
    ("home.png", "shopper", "/", "light", DESKTOP),
    ("home-dark.png", "shopper", "/", "dark", DESKTOP),
    ("products.png", "shopper", "/products", "light", DESKTOP),
    ("search.png", "shopper", "/products?q=noodles&sort=price-low", "light", DESKTOP),
    ("product.png", "shopper", "{water}", "light", DESKTOP),
    ("product-kinds.png", "shopper", "{coffee}", "light", DESKTOP),
    ("product-kinds-dark.png", "shopper", "{coffee}", "dark", DESKTOP),
    ("phone-product-kinds.png", "shopper", "{coffee}", "light", PHONE),
    ("add-kind.png", "owner", "{coffee-like}", "light", DESKTOP),
    ("phone.png", "shopper", "/", "light", PHONE),
    ("phone-products.png", "shopper", "/products", "light", PHONE),
    ("manage-products.png", "owner", "/manage/products", "light", DESKTOP),
    ("manage-products-dark.png", "owner", "/manage/products", "dark", DESKTOP),
    ("manage-product.png", "owner", "{water-admin}", "light", DESKTOP),
    ("units.png", "owner", "{water-admin}#units", "light", DESKTOP),
    ("new-product.png", "owner", "/manage/products/new", "light", DESKTOP),
    ("categories.png", "owner", "/manage/categories", "light", DESKTOP),
    ("staff.png", "owner", "/manage/staff", "light", DESKTOP),
    ("store-details.png", "owner", "/manage/store", "light", DESKTOP),
    ("low-stock.png", "cashier", "/manage/products?show=low", "light", DESKTOP),
    ("settings.png", "shopper", "/settings/", "light", DESKTOP),
    ("cart.png", "shopper", "/cart", "light", DESKTOP),
    ("checkout.png", "shopper", "/checkout", "light", DESKTOP),
    ("my-orders.png", "shopper", "/orders", "light", DESKTOP),
    ("order-waiting.png", "shopper", "{my-order:Waiting for the cashier}", "light", DESKTOP),
    ("order-priced.png", "shopper", "{my-order:Confirmed}", "light", DESKTOP),
    ("phone-order.png", "shopper", "{my-order:Confirmed}", "light", PHONE),
    ("phone-cart.png", "shopper", "/cart", "light", PHONE),
    ("orders-queue.png", "cashier", "/manage/orders", "light", DESKTOP),
    ("price-order.png", "cashier", "{bulk-order}", "light", DESKTOP),
    ("price-order-dark.png", "cashier", "{bulk-order}", "dark", DESKTOP),
    ("delivery-areas.png", "owner", "/manage/delivery-areas", "light", DESKTOP),
    ("order-pay.png", "shopper", "{my-order:Waiting for payment}", "light", DESKTOP),
    ("order-paid.png", "shopper", "{my-order:Paid}", "light", DESKTOP),
    ("staff-paid-order.png", "cashier", "{staff-order:confirmed:Paid}", "light", DESKTOP),
    ("staff-refunded-order.png", "cashier", "{staff-order:cancelled:Refunded}", "light", DESKTOP),
    ("orders-waiting.png", "cashier", "/manage/orders?tab=waiting", "light", DESKTOP),
    ("packing.png", "cashier", "/manage/packing", "light", DESKTOP),
    ("packing-dark.png", "cashier", "/manage/packing", "dark", DESKTOP),
    ("deliveries.png", "rider", "/deliveries", "light", DESKTOP),
    ("phone-deliveries.png", "rider", "/deliveries", "light", PHONE),
    ("rider-cash.png", "cashier", "/manage/rider-cash", "light", DESKTOP),
    ("product-stock.png", "cashier", "{water-admin}#stock", "light", DESKTOP),
    ("stock-history.png", "cashier", "/manage/stock", "light", DESKTOP),
    ("import.png", "owner", "/manage/products/import", "light", DESKTOP),
    ("order-on-the-way.png", "shopper", "{my-order:Delivered}", "light", DESKTOP),
    ("close-shift.png", "cashier", "/counter/shift", "light", DESKTOP),
    ("shifts.png", "owner", "/counter/shifts", "light", DESKTOP),
    ("favorites.png", "shopper", "/favorites", "light", DESKTOP),
    ("addresses.png", "shopper", "/addresses", "light", DESKTOP),
    ("promos.png", "owner", "/manage/promos", "light", DESKTOP),
    ("insights.png", "owner", "/manage/insights?range=30", "light", DESKTOP),
    ("insights-dark.png", "owner", "/manage/insights", "dark", DESKTOP),
    ("phone-insights.png", "owner", "/manage/insights", "light", PHONE),
]


def demo_page(browser, theme, size):
    page = browser.new_page(viewport=size, color_scheme=theme)
    page.goto(f"{BASE}/login")
    page.get_by_role("button", name="Try Demo").click()
    page.wait_for_url(f"{BASE}/", timeout=120000)  # making a demo store takes a while
    return page


def view_as(page, role):
    current = page.locator("#view-as").input_value()
    if current != role:
        # Choosing a role sends the form at once (app.js), so wait for that page to arrive.
        with page.expect_navigation():
            page.locator("#view-as").select_option(role)
        page.wait_for_load_state("networkidle")


def save(page, name):
    page.wait_for_load_state("networkidle")
    # Toasts float over the corner; close them so every picture shows the page itself.
    page.evaluate("document.querySelectorAll('[data-flash]').forEach((toast) => toast.remove())")
    page.wait_for_timeout(300)
    page.screenshot(path=OUT / name)
    print("saved", name)


def address(page, path):
    """{water} is the demo's bottled water in the shop, {water-admin} the same product's staff page,
    {my-order:<status>} the visitor's order with that status, {bulk-order} the reseller's order to price."""
    if path.startswith("{my-order:"):
        page.goto(f"{BASE}/orders")
        row = page.locator("tr", has_text=path[len("{my-order:"):-1])
        return row.locator(".row-link").first.get_attribute("href")
    if path.startswith("{staff-order:"):
        tab, words = path[len("{staff-order:"):-1].split(":")
        page.goto(f"{BASE}/manage/orders?tab={tab}")
        return page.locator("tr", has=page.locator(".pill", has_text=words)).locator(".row-link").first.get_attribute("href")
    if path == "{bulk-order}":
        page.goto(f"{BASE}/manage/orders")
        row = page.locator("tr", has_text="Marites Santos").filter(has_text="Has bulk items")
        return row.locator(".row-link").first.get_attribute("href")
    if path in ("{coffee}", "{coffee-like}"):
        # The 3-in-1 coffee: two brands, four kinds. Open Creamy White, so both choices show.
        page.goto(f"{BASE}/products?q=3-in-1")
        page.goto(BASE + page.locator(".card-link", has_text="3-in-1 coffee sachet").get_attribute("href"))
        link = page.locator(".option-chip", has_text="Creamy White").get_attribute("href")
        if path == "{coffee}":
            return link
        return "/manage/products/new?like=" + link.rsplit("/", 1)[1]
    if path.startswith("{water"):
        name, _, anchor = path.partition("#")
        page.goto(f"{BASE}/products?q=bottled+water")
        link = page.locator(".card-link", has_text="Bottled water 500 mL").get_attribute("href")
        if name == "{water-admin}":
            link = link.replace("/products/", "/manage/products/")
        return link + (f"#{anchor}" if anchor else "")
    return path


def shoot_sandbox(page):
    """The practice payment page: press Pay on the order that waits for payment, then go back."""
    view_as(page, "shopper")
    page.goto(f"{BASE}{address(page, '{my-order:Waiting for payment}')}")
    page.get_by_role("button", name="Pay ₱").click()
    page.wait_for_url("**/sandbox/pay/**")
    save(page, "sandbox.png")
    page.get_by_role("button", name="Go back without paying").click()


def shoot_counter(page):
    """The counter with a sale rung up, then its receipt."""
    view_as(page, "cashier")
    page.goto(f"{BASE}/counter?q=pancit")
    page.locator(".counter-unit", has_text="Box of 72").first.get_by_role("button", name="Wholesale").click()
    page.goto(f"{BASE}/counter?q=soy")
    page.locator(".counter-unit", has_text="Bottle").first.get_by_role("button", name="Retail").click()
    page.goto(f"{BASE}/counter?q=candles")
    page.locator(".counter-unit", has_text="Piece").first.locator("input[name=quantity]").fill("4")
    page.locator(".counter-unit", has_text="Piece").first.get_by_role("button", name="Retail").click()
    page.locator('input[name="cash_given"]').fill("2000")
    save(page, "counter.png")
    page.get_by_role("button", name="Pay and print receipt").click()
    page.wait_for_url("**/counter/sales/**")
    save(page, "receipt.png")


def shoot_visitor_pages(browser):
    page = browser.new_page(viewport=DESKTOP, color_scheme="light")
    for name, path in (("login.png", "/login"), ("signup.png", "/signup")):
        page.goto(f"{BASE}{path}")
        save(page, name)
    page.close()


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        shoot_visitor_pages(browser)
        pages = {}
        for name, role, path, theme, size in PAGES:
            key = (theme, size["width"])
            if key not in pages:
                pages[key] = demo_page(browser, theme, size)
            page = pages[key]
            view_as(page, role)
            page.goto(f"{BASE}{address(page, path)}")
            save(page, name)
        shoot_sandbox(pages[("light", DESKTOP["width"])])
        shoot_counter(pages[("light", DESKTOP["width"])])
        browser.close()


if __name__ == "__main__":
    main()
