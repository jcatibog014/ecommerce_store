"""Who may do what. Every staff page asks here, never by checking role names itself.

| Role    | Can do                                                               |
|---------|----------------------------------------------------------------------|
| Owner   | everything: products, prices, staff, store details                   |
| Cashier | see products and stock; price orders (retail or wholesale), cancel   |
| Rider   | deliveries (from stage 4)                                            |
| Shopper | browse, cart, checkout, order history                                |

In the demo one person plays every role: View As (session["view_as"])
picks which one. It only works in a demo store, so it can't lift anyone's
rights in the real store.
"""
import functools

from flask import abort, g, has_request_context, session

from .models import CASHIER, OWNER, RIDER, ROLES, SHOPPER

VIEW_AS_ROLES = (SHOPPER, CASHIER, RIDER, OWNER)

# What each kind of work needs.
MANAGE_PRODUCTS = (OWNER,)  # add and edit products, units, prices, photos, categories
SEE_PRODUCTS = (OWNER, CASHIER)  # the staff product list with stock
MANAGE_STAFF = (OWNER,)
MANAGE_STORE = (OWNER,)  # store details and delivery areas
SHOP = (SHOPPER,)  # a cart, checkout and order history (staff sell at the counter instead)
SEE_ORDERS = (OWNER, CASHIER)
PRICE_ORDERS = (OWNER, CASHIER)  # choose retail or wholesale, and cancel


def role_of(user):
    """The role this person is using right now (None when logged out)."""
    if user is None:
        return None
    if user.is_demo and has_request_context() and session.get("view_as") in VIEW_AS_ROLES:
        return session["view_as"]
    return user.role


def can(roles):
    return g.get("role") in roles


def is_staff():
    return g.get("role") not in (None, SHOPPER)


def needs(roles, message="Your role can't open this page."):
    """A view decorator: log in first, then have one of these roles."""
    def decorate(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            from .auth import login_required
            if g.user is not None and not can(roles):
                abort(403, message)
            return login_required(view)(*args, **kwargs)
        return wrapped
    return decorate


def init_app(app):
    app.jinja_env.globals.update(
        can=can, is_staff=is_staff, MANAGE_PRODUCTS=MANAGE_PRODUCTS, SEE_PRODUCTS=SEE_PRODUCTS,
        MANAGE_STAFF=MANAGE_STAFF, MANAGE_STORE=MANAGE_STORE, SHOP=SHOP, SEE_ORDERS=SEE_ORDERS,
        PRICE_ORDERS=PRICE_ORDERS, VIEW_AS_ROLES=VIEW_AS_ROLES, ROLES=ROLES)
