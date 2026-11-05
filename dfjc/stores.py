"""The store a page is about.

There is one real store, DFJC (made by "flask create-owner"). Every Try Demo
makes its own small store, so a visitor's changes never reach the real one.
Every query for products, categories or people starts from a store, and a
row from another store gives 404.
"""
from flask import abort
from sqlalchemy import select

from .models import Store

MAIN_STORE_NAME = "DFJC Store"
MAIN_STORE_TAGLINE = "Wholesale and retail. Sari-sari needs by the piece, the pack or the case."


def main_store(s):
    """The real store (the oldest one that isn't a demo), or None before it is made."""
    return s.scalar(select(Store).where(Store.is_demo.is_(False)).order_by(Store.id).limit(1))


def create_main_store(s):
    store = Store(name=MAIN_STORE_NAME, tagline=MAIN_STORE_TAGLINE)
    s.add(store)
    s.flush()
    return store


def get_in_store(s, model, row_id, store):
    """A row by id, but only from this store: anything else is "not found"."""
    row = s.get(model, row_id)
    if row is None or store is None or row.store_id != store.id:
        abort(404)
    return row
