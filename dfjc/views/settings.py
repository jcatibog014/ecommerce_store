"""Settings: every preference and account action lives here, never in the menu.
The owner also keeps the store's details here (name, address, hours...).

Each form sends an "action" field, and each action is one small function.
The demo sees every form, switched off; the server refuses its changes anyway.
"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .. import auth, db, forms
from ..auth import login_required
from ..models import Store
from ..permissions import MANAGE_STORE, can

bp = Blueprint("settings", __name__, url_prefix="/settings")


def back():
    return redirect(url_for("settings.index"))


def change_details(errors):
    name = " ".join((request.form.get("name") or "").split())
    mobile = auth.clean_mobile(request.form.get("mobile"))
    for key, problem in (("name", auth.name_problem(name)), ("mobile", auth.mobile_problem(mobile))):
        if problem:
            errors[key] = problem
    if errors:
        return None
    g.user.name, g.user.mobile = name, mobile
    db.session().commit()
    flash("Your details are saved.")
    return back()


def change_password(errors):
    new = request.form.get("new_password", "")
    if not check_password_hash(g.user.password_hash, request.form.get("current_password", "")):
        errors["current_password"] = "That isn't your current password."
    problem = auth.password_problem(new, request.form.get("confirm_password", ""))
    if problem:
        errors["new_password"] = problem
    if errors:
        return None
    auth.set_password(g.user, new)
    db.session().commit()
    # This device stays logged in; every other one is logged out.
    session["session_version"] = g.user.session_version
    flash("Your password has been changed.")
    return back()


def change_emails(errors):
    g.user.order_emails = request.form.get("order_emails") == "1"
    db.session().commit()
    flash("You'll get an email when your order changes." if g.user.order_emails
          else "No more order emails. You can still see everything in My Orders.")
    return back()


ACTIONS = {"details": change_details, "password": change_password, "emails": change_emails}

# The store's details: (form field, Store column, longest, required label). "store_name", not
# "name", so its error never lands on the person's own Name field.
STORE_FIELDS = (("store_name", "name", 80, "The store's name"), ("tagline", "tagline", 160, ""),
                ("address", "address", 300, ""), ("phone", "phone", 40, ""), ("hours", "hours", 300, ""),
                ("pickup_note", "pickup_note", 500, ""))


def store_values(store):
    return {field: getattr(store, column) for field, column, _, _ in STORE_FIELDS}


def change_store(errors, values):
    """The owner's store details. A demo store is the visitor's own, so the demo may change it."""
    if not can(MANAGE_STORE):
        abort(403, "Only the owner can change the store's details.")
    for field, _, limit, label in STORE_FIELDS:
        values[field], problem = forms.text(request.form, field, limit, required=bool(label), label=label)
        if problem:
            errors[field] = problem
    if errors:
        return None
    current = db.session().get(Store, g.store.id)
    for field, column, _, _ in STORE_FIELDS:
        setattr(current, column, values[field])
    db.session().commit()
    flash("The store's details are saved. Shoppers see them on the home page.")
    return redirect(url_for("settings.index", _anchor="store"))


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    errors = {}
    store = store_values(g.store) if can(MANAGE_STORE) else None
    if request.method == "POST":
        if request.form.get("action") == "store":
            store = {}
            response = change_store(errors, store)
            if response:
                return response
        else:
            auth.not_for_demo("The demo can't change its account. Everything else is yours to try.")
            action = ACTIONS.get(request.form.get("action"))
            if action:
                response = action(errors)
                if response:
                    return response
    return render_template("settings.html", errors=errors, store=store), 400 if errors else 200
