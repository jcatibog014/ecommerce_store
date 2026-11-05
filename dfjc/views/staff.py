"""The owner's pages for people and the store: staff, invite links, store details.
Also the page an invite link opens (/join/<token>), where a cashier or rider makes their login."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import auth, db, forms, invites
from ..dates import utcnow
from ..models import CASHIER, OWNER, RIDER, STAFF_ROLES, Invite, Store, User
from ..permissions import MANAGE_STAFF, MANAGE_STORE, needs
from ..stores import get_in_store

bp = Blueprint("staff", __name__)


@bp.get("/manage/staff")
@needs(MANAGE_STAFF, "Only the owner can see the staff page.")
def index():
    s = db.session()
    people = s.scalars(select(User).where(User.store_id == g.store.id, User.role.in_(STAFF_ROLES))
                       .order_by(User.role, User.name)).all()
    links = s.scalars(select(Invite).where(Invite.store_id == g.store.id).order_by(Invite.created_at.desc())
                      .limit(20)).all()
    new_link = session.pop("new_invite_link", None)
    return render_template("manage/staff.html", people=people, links=links, new_link=new_link, moment=utcnow(),
                           invite_status=invites.status, owner=OWNER)


@bp.post("/manage/staff/invite")
@needs(MANAGE_STAFF, "Only the owner can invite staff.")
def invite():
    auth.not_for_demo("The demo can't make invite links. In your own store, send one to each cashier or rider.")
    role = request.form.get("role")
    if role not in (CASHIER, RIDER):
        abort(400, "Choose Cashier or Rider.")
    name, problem = forms.text(request.form, "name", 80)
    if problem:
        flash(problem, "error")
        return redirect(url_for("staff.index"))
    s = db.session()
    _, token = invites.create_invite(s, g.store, role, name, g.user, utcnow())
    s.commit()
    session["new_invite_link"] = url_for("staff.join", token=token, _external=True)
    return redirect(url_for("staff.index"))


@bp.post("/manage/staff/invites/<int:invite_id>/cancel")
@needs(MANAGE_STAFF, "Only the owner can change invites.")
def cancel_invite(invite_id):
    s = db.session()
    link = get_in_store(s, Invite, invite_id, g.store)
    if link.used_at is None:
        s.delete(link)
        s.commit()
        flash("That invite link was cancelled. It doesn't work any more.")
    return redirect(url_for("staff.index"))


@bp.post("/manage/staff/<int:user_id>/active")
@needs(MANAGE_STAFF, "Only the owner can change staff.")
def set_active(user_id):
    s = db.session()
    person = get_in_store(s, User, user_id, g.store)
    if person.role == OWNER or person.id == g.user.id:
        abort(400, "The owner's own login can't be switched off here.")
    person.is_active = request.form.get("active") == "1"
    if not person.is_active:
        person.session_version += 1  # logged out everywhere, straight away
    s.commit()
    flash(f"{person.name} can log in again." if person.is_active else f"{person.name} can't log in any more.")
    return redirect(url_for("staff.index"))


@bp.route("/manage/store", methods=["GET", "POST"])
@needs(MANAGE_STORE, "Only the owner can change the store's details.")
def store():
    s = db.session()
    errors = {}
    values = None
    if request.method == "POST":
        values, errors = {}, {}
        for name, limit, label in (("name", 80, "The store's name"), ("tagline", 160, ""), ("address", 300, ""),
                                   ("phone", 40, ""), ("hours", 300, ""), ("pickup_note", 500, "")):
            values[name], errors[name] = forms.text(request.form, name, limit, required=bool(label), label=label)
        errors = {key: value for key, value in errors.items() if value}
        if not errors:
            current = s.get(Store, g.store.id)
            for name, value in values.items():
                setattr(current, name, value)
            s.commit()
            flash("The store's details are saved. Shoppers see them on the home page.")
            return redirect(url_for("staff.store"))
    return render_template("manage/store.html", values=values or store_values(g.store), errors=errors), \
        400 if errors else 200


def store_values(current):
    return {name: getattr(current, name) for name in ("name", "tagline", "address", "phone", "hours", "pickup_note")}


@bp.route("/join/<token>", methods=["GET", "POST"])
def join(token):
    s = db.session()
    moment = utcnow()
    link = invites.find_open_invite(s, token, moment)
    if link is None:
        return render_template("join.html", link=None), 404
    if g.user is not None:
        return render_template("join.html", link=link, logged_in=True)
    errors = {}
    values = {"name": link.name}
    if request.method == "POST":
        values, errors = auth.account_form_problems(request.form)
        if not errors:
            store = s.get(Store, link.store_id)
            try:
                user = auth.create_user(s, store, values["name"], values["email"], request.form["password"],
                                        role=link.role, mobile=values["mobile"])
                # Only if nobody used the link a moment ago (two people opening it at once).
                claimed = s.execute(Invite.__table__.update()
                                    .where(Invite.id == link.id, Invite.used_at.is_(None))
                                    .values(used_at=moment, used_by_id=user.id)).rowcount
                if claimed != 1:
                    s.rollback()
                    return render_template("join.html", link=None), 404
                s.commit()
            except IntegrityError:
                s.rollback()
                errors["email"] = "That email already has an account here. Ask the owner for help."
            else:
                auth.log_in(user)
                flash(f"Welcome to {store.name}, {user.first_name}! You are a {link.role}.")
                return redirect(auth.home_for(user))
    return render_template("join.html", link=link, errors=errors, values=values), 400 if errors else 200
