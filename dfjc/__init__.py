"""DFJC Store: the online shop and counter for a wholesale and retail store.

Flask builds the app inside create_app() (the "app factory" pattern).
Tests call it with their own settings, so each test gets a fresh app and database.
"""
import mimetypes
import os
from datetime import timedelta
from pathlib import Path

from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

# Browsers want the right type for the app manifest (the name and icons used
# when someone adds the app to a home screen). Python doesn't know this one.
mimetypes.add_type("application/manifest+json", ".webmanifest")

DEV_SECRET_KEY = "dev-only-not-secret"

# Content-Security-Policy: the browser may only load scripts, styles, fonts
# and images from this site. Even if someone sneaked a <script> into a
# product description, the browser would refuse to run it.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "font-src 'self'",
    "img-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    # After "Pay", the browser goes to PayMongo's payment page (a form's redirect counts as form-action).
    "form-action 'self' https://checkout.paymongo.com",
    "frame-ancestors 'none'",
])


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    on_render = bool(os.environ.get("RENDER"))  # Render sets RENDER=true on its servers
    app.config.from_mapping(
        # Signs the login cookie and reset links. On Render it comes from an environment variable.
        SECRET_KEY=os.environ.get("SECRET_KEY", DEV_SECRET_KEY),
        # Local SQLite file, in the instance/ folder (git ignores it).
        DATABASE=str(Path(app.instance_path) / "dfjc.sqlite"),
        # Set on the live site: then we use PostgreSQL (Neon) instead of SQLite.
        DATABASE_URL=os.environ.get("DATABASE_URL"),
        # Gmail address + app password for emails (mailer.py). Without them emails go to the terminal.
        MAIL_USERNAME=os.environ.get("MAIL_USERNAME"),
        MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD"),
        # PayMongo test (or live) keys, from the PayMongo dashboard. Without them the real store
        # uses the Sandbox on your own computer, and online payment is off on the live site.
        PAYMONGO_SECRET_KEY=os.environ.get("PAYMONGO_SECRET_KEY"),
        PAYMONGO_WEBHOOK_SECRET=os.environ.get("PAYMONGO_WEBHOOK_SECRET"),
        ON_RENDER=on_render,
        # PayMongo's webhook can't send our form token: its signature is checked instead.
        CSRF_EXEMPT=("payments.webhook",),
        # Cookie safety: not readable by JavaScript (Flask's default), not sent
        # with forms from other sites, and only over HTTPS on the live site.
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=on_render,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=7 * 1024 * 1024,  # the biggest upload: a 6 MB photo, plus the form around it
    )
    if test_config:
        app.config.update(test_config)
    if on_render:
        # Render's proxy talks HTTPS to the visitor and HTTP to us. This makes
        # Flask believe the X-Forwarded-Proto header, so links in emails start with https://.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    if on_render and app.config["SECRET_KEY"] == DEV_SECRET_KEY:
        raise RuntimeError("Set the SECRET_KEY environment variable on Render.")
    if on_render and not app.config["DATABASE_URL"]:
        # Without it we'd use a SQLite file, and Render forgets files on every restart.
        raise RuntimeError("Set the DATABASE_URL environment variable on Render (Neon's connection string).")

    from . import auth, db, formatting, permissions
    from .models import (
        CATEGORY_ICONS, FULFILMENTS, MOVE_REASONS, PAYMENT_METHODS, PAYMENT_STATUS_LABELS, PRICE_TYPES, ROLE_LABELS,
    )
    from .views import cart, fulfil, manage, my_orders, payments, settings, shop, staff, staff_orders

    db.init_app(app)
    auth.init_app(app)
    formatting.init_app(app)
    permissions.init_app(app)
    shop.init_app(app)
    cart.init_app(app)
    staff_orders.init_app(app)
    fulfil.init_app(app)
    # auth comes first: its before_request (load_user) must run before the others'.
    for blueprint in (auth.bp, shop.bp, cart.bp, my_orders.bp, payments.bp, manage.bp, staff_orders.bp, fulfil.bp,
                      staff.bp, settings.bp):
        app.register_blueprint(blueprint)

    app.jinja_env.globals.update(APP_NAME="DFJC Store", ROLE_LABELS=ROLE_LABELS, CATEGORY_ICONS=CATEGORY_ICONS,
                                 FULFILMENTS=FULFILMENTS, PAYMENT_METHODS=PAYMENT_METHODS, PRICE_TYPES=PRICE_TYPES,
                                 PAYMENT_STATUS_LABELS=PAYMENT_STATUS_LABELS, MOVE_REASONS=MOVE_REASONS)

    @app.get("/healthz")
    def healthz():
        return "ok"  # Render checks this after a deploy

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")  # trust our file types
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")  # no showing us inside other sites
        if on_render:
            # Always use HTTPS for this site from now on (for a year).
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    def error_page(code, message):
        return render_template("error.html", code=code, message=message), code

    @app.errorhandler(400)
    def bad_request(error):
        return error_page(400, error.description or "Something in that request was wrong.")

    @app.errorhandler(403)
    def forbidden(error):
        return error_page(403, error.description or "You can't do that.")

    @app.errorhandler(404)
    def not_found(error):
        return error_page(404, "We couldn't find that page.")

    @app.errorhandler(413)
    def too_large(error):
        return error_page(413, "That file is too big. Photos can be up to 6 MB.")

    @app.errorhandler(503)
    def not_ready(error):
        return error_page(503, error.description or "The store is resting. Please try again soon.")

    return app
