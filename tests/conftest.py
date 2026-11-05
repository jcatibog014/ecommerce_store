import os
import socket

import pytest

from dfjc import auth, create_app, db, stores
from dfjc.dates import utcnow
from dfjc.models import OWNER, Category, Product, ProductUnit, User

# Tests use a fresh SQLite file per test. To check everything on PostgreSQL
# too, set TEST_DATABASE_URL to an EMPTY Neon branch (its tables are dropped
# and made again for every test, so never point it at the live database).
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

CSRF = "test-csrf-token"
PASSWORD = "correct horse"


@pytest.fixture(autouse=True)
def stay_offline(monkeypatch):
    """Tests never go online (email and PayMongo are faked). Any real network
    connection fails loudly. A Neon test run needs the network, so it's skipped then."""
    if TEST_DATABASE_URL:
        return

    def refuse(*args, **kwargs):
        raise RuntimeError("A test tried to go online. Fake the network call instead.")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "DATABASE": str(tmp_path / "test.sqlite"),
        "DATABASE_URL": TEST_DATABASE_URL,
    })
    with app.app_context():
        if TEST_DATABASE_URL:
            db.drop_tables()
        db.create_tables()
    return app


@pytest.fixture
def store_id(app):
    """The real store (DFJC), as "flask create-owner" makes it."""
    with app.app_context():
        s = db.session()
        store = stores.create_main_store(s)
        s.commit()
        return store.id


@pytest.fixture
def client(app):
    client = app.test_client()
    # Every POST needs the session's CSRF token. Put a known one in the session.
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return client


def post(client, url, data=None, **kwargs):
    """POST a form with the right CSRF token, like a real page would."""
    # Logging in or out starts a new session (and a new token), so set it every time.
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return client.post(url, data={"csrf_token": CSRF, **(data or {})}, **kwargs)


def make_user(app, store_id, email, role="shopper", name=None, **fields):
    """Put a user straight into the database. Returns the id."""
    from dfjc.models import Store
    with app.app_context():
        s = db.session()
        store = s.get(Store, store_id)
        user = auth.create_user(s, store, name or email.split("@")[0].title(), email, PASSWORD, role=role,
                                mobile="09171234567")
        for name_, value in fields.items():
            setattr(user, name_, value)
        s.commit()
        return user.id


def log_in_as(client, user_id):
    """Skip the login form: put the user straight into the session."""
    with client.application.app_context():
        version = db.session().get(User, user_id).session_version
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["session_version"] = version
        session["csrf"] = CSRF
    return client


def is_logged_in(client):
    """load_user() runs on every request and drops a session that is no longer
    valid, so ask after a request what is left in the cookie."""
    client.get("/login")
    with client.session_transaction() as session:
        return "user_id" in session


def add_product(app, store_id, name="Bottled water 500 mL", units=(("Bottle", 1, 1500, 1300),), stock=100,
                category=None, **fields):
    """Put a product straight into the database. units: (name, pieces, retail, wholesale) in centavos.
    category: a name (made if missing). Returns the id."""
    from sqlalchemy import select
    with app.app_context():
        s = db.session()
        category_id = None
        if category:
            found = s.scalar(select(Category).where(Category.store_id == store_id, Category.name == category))
            if found is None:
                found = Category(store_id=store_id, name=category, icon="bottle")
                s.add(found)
                s.flush()
            category_id = found.id
        moment = fields.pop("created_at", utcnow())
        product = Product(store_id=store_id, name=name, stock_pieces=stock, category_id=category_id,
                          created_at=moment, updated_at=moment, **fields)
        for unit_name, pieces, retail, wholesale in units:
            product.units.append(ProductUnit(name=unit_name, pieces_per_unit=pieces, retail_price=retail,
                                             wholesale_price=wholesale))
        s.add(product)
        s.commit()
        return product.id


def get(app, model, row_id):
    with app.app_context():
        return db.session().get(model, row_id)


@pytest.fixture
def owner_id(app, store_id):
    return make_user(app, store_id, "owner@example.com", role=OWNER, name="Dina Flores")


@pytest.fixture
def cashier_id(app, store_id):
    return make_user(app, store_id, "cashier@example.com", role="cashier", name="Lorna Cruz")


@pytest.fixture
def rider_id(app, store_id):
    return make_user(app, store_id, "rider@example.com", role="rider", name="Jun Dizon")


@pytest.fixture
def shopper_id(app, store_id):
    return make_user(app, store_id, "juan@example.com", name="Juan dela Cruz")


@pytest.fixture
def owner(client, owner_id):
    return log_in_as(client, owner_id)


@pytest.fixture
def shopper(client, shopper_id):
    return log_in_as(client, shopper_id)
