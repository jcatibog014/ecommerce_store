"""Signing up, logging in and out, password reset by email, the demo, and form protection.

Shoppers must have an account to order (the owner's choice). They sign up with
their name, email and mobile number and log in with their email. Staff
(cashiers and riders) join with an invite link from the owner (invites.py).
The owner is made on the command line: flask --app dfjc create-owner.

Passwords are stored only as hashes. The login cookie holds the user's id and
their session_version; load_user() checks both on every request, so an old
cookie is logged out straight away.
"""
import functools
import hmac
import re
import secrets
from datetime import timedelta

import click
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from flask.cli import with_appcontext
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, mailer, permissions, stores
from .dates import utcnow
from .models import CASHIER, OWNER, SHOPPER, PaymentEvent, Store, User

bp = Blueprint("auth", __name__)

NAME_MAX_LENGTH = 80
EMAIL_MAX_LENGTH = 254
PASSWORD_MIN_LENGTH = 8
# Good enough to catch typos; the reset email is the real test of an address.
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
# A Philippine mobile number: 09171234567, or +63 917 123 4567.
MOBILE_PATTERN = re.compile(r"09\d{9}")
# After 5 wrong passwords in a row, the account waits 15 minutes.
MAX_FAILED_LOGINS = 5
LOCK_TIME = timedelta(minutes=15)
RESET_LIFETIME = timedelta(hours=1)
RESET_EVERY = timedelta(minutes=2)  # at most one reset email per account this often

DEMO_LIFETIME = timedelta(hours=2)
MAX_DEMOS = 300  # demo stores alive at once, so a script can't fill the database

WRONG_LOGIN = "Wrong email or password."
LOCKED = "Too many failed attempts. Please wait 15 minutes and try again."
RESET_SENT = "If that email has an account, we sent it a link to set a new password. It works for 1 hour."


# ---------- Checking what people type ----------

def clean_email(text):
    return (text or "").strip().lower()


def email_problem(email):
    if not email:
        return "Your email is needed."
    if len(email) > EMAIL_MAX_LENGTH or not EMAIL_PATTERN.fullmatch(email):
        return "Use an email address like juan@example.com."
    return None


def clean_mobile(text):
    """"+63 917 123 4567", "0917-123-4567" -> "09171234567". Anything else stays as typed."""
    digits = re.sub(r"[\s()-]", "", text or "")
    if digits.startswith("+63"):
        digits = "0" + digits[3:]
    elif digits.startswith("63") and len(digits) == 12:
        digits = "0" + digits[2:]
    return digits


def mobile_problem(mobile, required=True):
    if not mobile:
        return "Your mobile number is needed, so the store can call you about an order." if required else None
    if not MOBILE_PATTERN.fullmatch(mobile):
        return "Use a mobile number like 0917 123 4567."
    return None


def name_problem(name):
    if not name:
        return "Your name is needed."
    if len(name) > NAME_MAX_LENGTH:
        return f"Use at most {NAME_MAX_LENGTH} characters."
    return None


def password_problem(password, confirm):
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Use at least {PASSWORD_MIN_LENGTH} characters."
    if password != confirm:
        return "The two passwords are not the same."
    return None


def account_form_problems(form):
    """Read the name, email, mobile and password fields of a sign-up form.
    Returns (values, errors)."""
    values = {
        "name": " ".join((form.get("name") or "").split()),
        "email": clean_email(form.get("email")),
        "mobile": clean_mobile(form.get("mobile")),
    }
    errors = {}
    for key, problem in (("name", name_problem(values["name"])), ("email", email_problem(values["email"])),
                         ("mobile", mobile_problem(values["mobile"])),
                         ("password", password_problem(form.get("password", ""), form.get("confirm_password", "")))):
        if problem:
            errors[key] = problem
    return values, errors


# ---------- Users in the database ----------

def find_user(s, store, email):
    if store is None:
        return None
    return s.scalar(select(User).where(User.store_id == store.id, User.email == clean_email(email)))


def create_user(s, store, name, email, password, role=SHOPPER, mobile=""):
    """Add a user to store. The caller commits (and catches IntegrityError for a used email)."""
    user = User(store_id=store.id, name=name, email=clean_email(email), mobile=mobile, role=role,
                password_hash=generate_password_hash(password))
    user.store = store
    s.add(user)
    s.flush()
    return user


def set_password(user, password):
    user.password_hash = generate_password_hash(password)
    user.failed_logins = 0
    user.locked_until = None
    user.session_version += 1  # every other device is logged out, and old reset links die


def check_login(s, store, email, password, moment):
    """Return (user, None) if the password is right, else (None, message).
    The message never says whether the email has an account."""
    user = find_user(s, store, email)
    if user is None or not user.is_active:
        return None, WRONG_LOGIN
    if user.locked_until and user.locked_until > moment:
        return None, LOCKED
    if not check_password_hash(user.password_hash, password or ""):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.failed_logins, user.locked_until = 0, moment + LOCK_TIME
        s.commit()
        return None, WRONG_LOGIN
    user.failed_logins, user.locked_until = 0, None
    s.commit()
    return user, None


def home_for(user):
    """Where someone lands after logging in: staff at their work, shoppers at the shop."""
    if user is not None and permissions.role_of(user) in (OWNER, CASHIER):
        return url_for("staff_orders.index")
    return url_for("shop.home")


def safe_next(target, default=None):
    """Only go back to a page on this site (after logging in or saving a form).

    "/products/7" is fine. "https://evil.example" and "//evil.example" are not:
    a fake link could send people there after a real login. Browsers drop tabs
    and newlines and read a backslash as a slash, so those are out too.
    """
    if (target and target.startswith("/") and not target.startswith("//")
            and "\\" not in target and not any(ord(c) < 32 for c in target)):
        return target
    return default or url_for("shop.home")


def log_in(user):
    session.clear()  # a new session for the new user (old cookies can't be reused)
    session.permanent = True  # stay logged in for PERMANENT_SESSION_LIFETIME
    session["user_id"] = user.id
    session["session_version"] = user.session_version


# ---------- Reset links ----------

def reset_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="dfjc-password-reset")


def reset_token(user):
    """A signed link token. It holds the session_version, so it stops working
    once the password changes (it can only be used once)."""
    return reset_serializer().dumps({"u": user.id, "v": user.session_version})


def user_from_reset_token(s, token):
    try:
        data = reset_serializer().loads(token, max_age=int(RESET_LIFETIME.total_seconds()))
    except (BadSignature, SignatureExpired):
        return None
    user = s.get(User, data.get("u")) if isinstance(data, dict) else None
    if user is None or user.session_version != data.get("v") or user.is_demo or not user.is_active:
        return None
    return user


# ---------- Every request ----------

@bp.before_app_request
def load_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id is not None:
        user = db.session().get(User, user_id)
        if user is None or session.get("session_version") != user.session_version or not user.is_active:
            # Keep the CSRF token: only the login part of this cookie is out of date.
            session.pop("user_id", None)
            session.pop("session_version", None)
        else:
            g.user = user
    # Which store this page is about: your own (a demo has its own), else DFJC.
    g.store = g.user.store if g.user else stores.main_store(db.session())
    g.role = permissions.role_of(g.user)


def csrf_token():
    """A secret per session, put into every form as a hidden field.

    Another website could make your browser send a form to this site (your
    login cookie goes with it). It can't read this secret, so its form fails.
    """
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@bp.before_app_request
def check_csrf():
    if request.method != "POST" or request.endpoint in current_app.config.get("CSRF_EXEMPT", ()):
        return
    sent = request.form.get("csrf_token", "")
    expected = session.get("csrf", "")
    # compare_digest takes the same time for every wrong guess.
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, "This form has expired. Please go back, reload the page and try again.")


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            # Only a GET can be repeated after logging in.
            next_page = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("auth.login", next=next_page))
        return view(*args, **kwargs)
    return wrapped


def not_for_demo(message="The demo can't change this."):
    if g.user.is_demo:
        abort(403, message)


def need_store():
    """Sign-up and login need the real store to exist (flask create-owner makes it)."""
    if g.store is None:
        abort(503, "The store isn't set up yet. The owner runs: flask --app dfjc create-owner")


# ---------- Pages ----------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(home_for(g.user))
    next_page = request.values.get("next", "")
    error = None
    email = ""
    if request.method == "POST":
        need_store()
        email = request.form.get("email", "")[:EMAIL_MAX_LENGTH]
        user, error = check_login(db.session(), g.store, email, request.form.get("password", ""), utcnow())
        if user:
            log_in(user)
            return redirect(safe_next(next_page, home_for(user)))
    status = 401 if error else 200
    return render_template("login.html", error=error, email=email, next=next_page), status


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if g.user is not None:
        return redirect(home_for(g.user))
    next_page = request.values.get("next", "")
    errors = {}
    values = {}
    if request.method == "POST":
        need_store()
        # A hidden field people never see. Only robots fill it in, and they get
        # sent away like a success, so they learn nothing from it.
        if request.form.get("website"):
            return redirect(url_for("auth.login"))
        values, errors = account_form_problems(request.form)
        if not errors:
            s = db.session()
            try:
                user = create_user(s, g.store, values["name"], values["email"], request.form["password"],
                                   mobile=values["mobile"])
                s.commit()
            except IntegrityError:
                s.rollback()
                errors["email"] = "That email already has an account. Log in, or reset your password."
            else:
                log_in(user)
                flash(f"Welcome to {g.store.name}, {user.first_name}! Your account is ready.")
                return redirect(safe_next(next_page))
    return render_template("signup.html", errors=errors, values=values, next=next_page), 400 if errors else 200


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    """Ask for a reset link. The answer is the same whether or not the email has an account."""
    if request.method == "POST":
        need_store()
        s = db.session()
        moment = utcnow()
        user = find_user(s, g.store, request.form.get("email", "")[:EMAIL_MAX_LENGTH])
        if user and user.is_active and not user.is_demo and (
                user.reset_sent_at is None or user.reset_sent_at <= moment - RESET_EVERY):
            user.reset_sent_at = moment
            s.commit()
            link = url_for("auth.reset", token=reset_token(user), _external=True)
            mailer.send(user.email, f"Set a new password for {g.store.name}",
                        f"Hello {user.first_name},\n\nSomeone asked to set a new password for your "
                        f"{g.store.name} account. To do it, open this link within 1 hour:\n\n{link}\n\n"
                        "If it wasn't you, ignore this email: your password stays the same.\n")
        flash(RESET_SENT)
        return redirect(url_for("auth.login"))
    return render_template("forgot.html")


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    s = db.session()
    user = user_from_reset_token(s, token)
    if user is None:
        flash("That link is too old or was already used. Ask for a new one.", "error")
        return redirect(url_for("auth.forgot"))
    errors = {}
    if request.method == "POST":
        new = request.form.get("password", "")
        problem = password_problem(new, request.form.get("confirm_password", ""))
        if problem:
            errors["password"] = problem
        else:
            set_password(user, new)
            s.commit()
            log_in(user)
            flash("Your new password is set, and you are logged in.")
            return redirect(home_for(user))
    response = render_template("reset.html", errors=errors, user=user)
    # The address holds a secret, so never send it on to other sites or keep it in a cache.
    return response, 400 if errors else 200, {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@bp.post("/demo")
def demo():
    """A fresh sample store for this visitor alone, deleted after DEMO_LIFETIME.
    Nobody sees anyone else's changes, and the real store is never touched."""
    s = db.session()
    moment = utcnow()
    if g.user is not None and g.user.is_demo:
        return redirect(url_for("shop.home"))  # this browser already has one
    # Old demos go first. Deleting the store deletes everything in it (ON DELETE CASCADE).
    s.execute(delete(Store).where(Store.is_demo.is_(True), Store.demo_expires_at < moment)
              .execution_options(synchronize_session=False))
    # The Sandbox's practice events don't belong to a store; old ones go too.
    s.execute(delete(PaymentEvent).where(PaymentEvent.session_id.startswith("sbx_"),
                                         PaymentEvent.received_at < moment - DEMO_LIFETIME - DEMO_LIFETIME)
              .execution_options(synchronize_session=False))
    s.commit()
    if s.scalar(select(func.count()).select_from(Store).where(Store.is_demo.is_(True))) >= MAX_DEMOS:
        flash("The demo is very busy right now. Please try again in a little while.", "error")
        return redirect(url_for("auth.login"))
    from . import seed
    user = seed.make_demo(s, moment)
    s.commit()
    log_in(user)
    session["view_as"] = SHOPPER  # start where a customer starts
    flash(f"Welcome to the demo! You are {user.name}. Use View As at the top to be the cashier, rider or owner.")
    return redirect(url_for("shop.home"))


@bp.post("/demo/view-as")
@login_required
def view_as():
    if not g.user.is_demo:
        abort(403, "View As is only for the demo.")
    role = request.form.get("role", "")
    if role not in permissions.VIEW_AS_ROLES:
        abort(400, "Choose a role from the list.")
    session["view_as"] = role
    g.role = role
    return redirect(home_for(g.user))


@bp.post("/logout")
def logout():
    session.clear()
    flash("You have logged out.")
    return redirect(url_for("shop.home"))


# ---------- Command line ----------

def ask_password():
    password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    if len(password) < PASSWORD_MIN_LENGTH:
        raise click.ClickException(f"Use at least {PASSWORD_MIN_LENGTH} characters.")
    return password


@click.command("create-owner")
@with_appcontext
@click.option("--name", prompt="Your name")
@click.option("--email", prompt="Your email")
def create_owner_command(name, email):
    """Make the store (the first time) and its owner's login."""
    s = db.session()
    email = clean_email(email)
    problem = name_problem(name.strip()) or email_problem(email)
    if problem:
        raise click.ClickException(problem)
    store = stores.main_store(s) or stores.create_main_store(s)
    if find_user(s, store, email):
        raise click.ClickException(f"{email} already has an account. Use set-password to change the password.")
    create_user(s, store, name.strip(), email, ask_password(), role=OWNER)
    s.commit()
    click.echo(f"Made the owner {email} for {store.name}. Log in at /login.")


@click.command("set-password")
@with_appcontext
@click.argument("email")
def set_password_command(email):
    """Change someone's password (and unlock the account). Also ends their sessions."""
    s = db.session()
    user = find_user(s, stores.main_store(s), email)
    if user is None:
        raise click.ClickException(f"There is no account for {email}.")
    set_password(user, ask_password())
    s.commit()
    click.echo("The password is changed.")


def init_app(app):
    app.jinja_env.globals["csrf_token"] = csrf_token
    for command in (create_owner_command, set_password_command):
        app.cli.add_command(command)
