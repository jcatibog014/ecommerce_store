"""Signing up, logging in, password reset by email, form protection and the owner command."""
import re
from sqlalchemy import select

from dfjc import auth, db, mailer
from dfjc.dates import utcnow
from dfjc.models import OWNER, Store, User

from .conftest import PASSWORD, get, is_logged_in, log_in_as, post

SIGNUP = {"name": "  Maria   Santos ", "email": "Maria@Example.com", "mobile": "+63 917 123 4567",
          "password": "kape at pandesal", "confirm_password": "kape at pandesal"}


# ---------- Sign up ----------

def test_sign_up_makes_a_shopper_and_logs_in(app, client, store_id):
    response = post(client, "/signup", SIGNUP)
    assert response.status_code == 302 and response.location == "/"
    with app.app_context():
        user = db.session().scalar(select(User).where(User.email == "maria@example.com"))
        assert (user.name, user.mobile, user.role, user.store_id) == ("Maria Santos", "09171234567", "shopper", store_id)
        assert user.password_hash != SIGNUP["password"]  # only a hash
    assert is_logged_in(client)
    assert "Welcome to DFJC Store, Maria!" in client.get("/").get_data(as_text=True)


def test_sign_up_checks_every_field(client, store_id):
    response = post(client, "/signup", {"name": "", "email": "not-an-email", "mobile": "12345",
                                        "password": "short", "confirm_password": "short"})
    page = response.get_data(as_text=True)
    assert response.status_code == 400
    for message in ("Your name is needed.", "Use an email address like", "Use a mobile number like",
                    "Use at least 8 characters."):
        assert message in page


def test_the_same_email_cant_sign_up_twice(client, store_id, shopper_id):
    response = post(client, "/signup", {**SIGNUP, "email": "JUAN@example.com"})
    assert response.status_code == 400
    assert "That email already has an account" in response.get_data(as_text=True)


def test_robots_filling_the_hidden_field_get_nothing(app, client, store_id):
    response = post(client, "/signup", {**SIGNUP, "website": "spam.example"})
    assert response.status_code == 302
    with app.app_context():
        assert db.session().scalar(select(User)) is None


def test_sign_up_waits_for_the_store(client):
    assert post(client, "/signup", SIGNUP).status_code == 503


# ---------- Log in and out ----------

def test_log_in_with_email_any_case(client, shopper_id):
    response = post(client, "/login", {"email": " JUAN@example.com ".strip(), "password": PASSWORD})
    assert response.status_code == 302 and response.location == "/"
    assert is_logged_in(client)


def test_staff_land_on_their_work(client, owner_id):
    response = post(client, "/login", {"email": "owner@example.com", "password": PASSWORD})
    assert response.location == "/manage/products"


def test_wrong_password_never_says_which_part_was_wrong(client, shopper_id):
    wrong_password = post(client, "/login", {"email": "juan@example.com", "password": "nope"})
    no_account = post(client, "/login", {"email": "nobody@example.com", "password": "nope"})
    assert wrong_password.status_code == no_account.status_code == 401
    assert auth.WRONG_LOGIN in wrong_password.get_data(as_text=True)
    assert auth.WRONG_LOGIN in no_account.get_data(as_text=True)


def test_five_wrong_passwords_lock_the_account(app, client, shopper_id):
    for _ in range(auth.MAX_FAILED_LOGINS):
        post(client, "/login", {"email": "juan@example.com", "password": "nope"})
    # Even the right password waits now.
    response = post(client, "/login", {"email": "juan@example.com", "password": PASSWORD})
    assert auth.LOCKED in response.get_data(as_text=True)
    assert get(app, User, shopper_id).locked_until > utcnow()


def test_log_in_goes_back_only_to_this_site(client, shopper_id):
    response = post(client, "/login", {"email": "juan@example.com", "password": PASSWORD, "next": "//evil.example"})
    assert response.location == "/"
    post(client, "/logout")
    response = post(client, "/login", {"email": "juan@example.com", "password": PASSWORD, "next": "/products?q=rice"})
    assert response.location == "/products?q=rice"


def test_log_out(client, shopper):
    response = post(client, "/logout")
    assert response.status_code == 302
    assert not is_logged_in(client)


def test_a_switched_off_staff_member_is_logged_out(app, client, cashier_id):
    log_in_as(client, cashier_id)
    with app.app_context():
        db.session().get(User, cashier_id).is_active = False
        db.session().commit()
    assert not is_logged_in(client)
    response = post(client, "/login", {"email": "cashier@example.com", "password": PASSWORD})
    assert response.status_code == 401


def test_every_form_needs_the_csrf_token(client, shopper_id):
    response = client.post("/login", data={"email": "juan@example.com", "password": PASSWORD})
    assert response.status_code == 400
    assert "This form has expired" in response.get_data(as_text=True)


# ---------- Password reset ----------

def reset_link(app):
    body = mailer.outbox(app)[-1].get_content()
    return re.search(r"http://localhost(/reset/\S+)", body).group(1)


def test_forgot_password_emails_a_link_that_works_once(app, client, shopper_id):
    response = post(client, "/forgot", {"email": "juan@example.com"})
    assert response.status_code == 302
    assert mailer.outbox(app)[-1]["To"] == "juan@example.com"
    link = reset_link(app)
    assert client.get(link).status_code == 200
    response = post(client, link, {"password": "a new password", "confirm_password": "a new password"})
    assert response.status_code == 302 and is_logged_in(client)
    # Used once: the password changed, so the same link is dead now.
    assert client.get(link).status_code == 302
    post(client, "/logout")
    assert post(client, "/login", {"email": "juan@example.com", "password": "a new password"}).status_code == 302


def test_forgot_password_says_the_same_for_unknown_emails(app, client, shopper_id):
    known = post(client, "/forgot", {"email": "juan@example.com"}, follow_redirects=True).get_data(as_text=True)
    unknown = post(client, "/forgot", {"email": "nobody@example.com"}, follow_redirects=True).get_data(as_text=True)
    assert auth.RESET_SENT in known and auth.RESET_SENT in unknown
    assert len(mailer.outbox(app)) == 1


def test_only_one_reset_email_every_two_minutes(app, client, shopper_id):
    post(client, "/forgot", {"email": "juan@example.com"})
    post(client, "/forgot", {"email": "juan@example.com"})
    assert len(mailer.outbox(app)) == 1


def test_an_old_or_changed_reset_link_fails(app, client, shopper_id):
    with app.app_context():
        user = db.session().get(User, shopper_id)
        token = auth.reset_token(user)
        assert auth.user_from_reset_token(db.session(), token + "x") is None
        user.session_version += 1  # the password changed some other way
        db.session().commit()
        assert auth.user_from_reset_token(db.session(), token) is None


def test_the_reset_page_is_never_cached(app, client, shopper_id):
    post(client, "/forgot", {"email": "juan@example.com"})
    response = client.get(reset_link(app))
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


# ---------- Checking what people type ----------

def test_mobile_numbers_are_cleaned():
    assert auth.clean_mobile("+63 917 123 4567") == "09171234567"
    assert auth.clean_mobile("0917-123-4567") == "09171234567"
    assert auth.clean_mobile("639171234567") == "09171234567"
    assert auth.mobile_problem("09171234567") is None
    assert auth.mobile_problem("0917123") is not None


# ---------- The owner command ----------

def test_create_owner_makes_the_store_and_the_owner(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-owner", "--name", "Dina Flores", "--email", "Dina@Example.com"],
                           input="secret password\nsecret password\n")
    assert result.exit_code == 0, result.output
    with app.app_context():
        s = db.session()
        store = s.scalar(select(Store))
        owner = s.scalar(select(User))
        assert store.name == "DFJC Store" and not store.is_demo
        assert (owner.email, owner.role, owner.store_id) == ("dina@example.com", OWNER, store.id)
    again = runner.invoke(args=["create-owner", "--name", "Dina", "--email", "dina@example.com"],
                          input="secret password\nsecret password\n")
    assert again.exit_code != 0 and "already has an account" in again.output


def test_set_password_command(app, shopper_id):
    result = app.test_cli_runner().invoke(args=["set-password", "juan@example.com"],
                                          input="brand new pass\nbrand new pass\n")
    assert result.exit_code == 0, result.output
    assert get(app, User, shopper_id).session_version == 2


def test_the_live_site_needs_its_secrets(monkeypatch, tmp_path):
    import pytest

    from dfjc import create_app
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app({"DATABASE": str(tmp_path / "x.sqlite")})

