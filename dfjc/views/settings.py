"""Settings: every preference and account action lives here, never in the menu.

Each form sends an "action" field, and each action is one small function.
The demo sees every form, switched off; the server refuses its changes anyway.
"""
from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .. import auth, db
from ..auth import login_required

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


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    errors = {}
    if request.method == "POST":
        auth.not_for_demo("The demo can't change its account. Everything else is yours to try.")
        action = ACTIONS.get(request.form.get("action"))
        if action:
            response = action(errors)
            if response:
                return response
    return render_template("settings.html", errors=errors), 400 if errors else 200
