"""Staff invite links, switching staff off, the store's details, and Settings."""
import re
from datetime import timedelta

from sqlalchemy import select

from dfjc import db, invites
from dfjc.dates import utcnow
from dfjc.models import Invite, Store, User

from .conftest import PASSWORD, get, is_logged_in, log_in_as, post

JOIN = {"name": "Carlo Reyes", "email": "carlo@example.com", "mobile": "09181112222",
        "password": "night shift", "confirm_password": "night shift"}


def invite_link(owner, role="cashier", name="Carlo"):
    post(owner, "/manage/staff/invite", {"role": role, "name": name})
    page = owner.get("/manage/staff").get_data(as_text=True)
    return re.search(r'value="http://localhost(/join/[^"]+)"', page).group(1)


def test_an_invite_link_makes_a_cashier(app, client, owner):
    link = invite_link(owner)
    post(owner, "/logout")
    assert "You were invited to be a <strong>cashier</strong>" in client.get(link).get_data(as_text=True)
    response = post(client, link, JOIN)
    assert response.status_code == 302 and response.location == "/manage/products"
    with app.app_context():
        user = db.session().scalar(select(User).where(User.email == "carlo@example.com"))
        invite = db.session().scalar(select(Invite))
    assert user.role == "cashier" and invite.used_by_id == user.id
    # Once only.
    post(client, "/logout")
    assert client.get(link).status_code == 404


def test_the_link_is_shown_once_and_only_its_hash_is_kept(app, owner):
    link = invite_link(owner)
    token = link.rsplit("/", 1)[1]
    with app.app_context():
        invite = db.session().scalar(select(Invite))
    assert invite.token_hash == invites.token_hash(token) and token not in invite.token_hash
    assert "/join/" not in owner.get("/manage/staff").get_data(as_text=True)


def test_an_old_or_cancelled_link_fails(app, client, owner):
    link = invite_link(owner)
    with app.app_context():
        invite = db.session().scalar(select(Invite))
        invite.expires_at = utcnow() - timedelta(minutes=1)
        db.session().commit()
    post(owner, "/logout")
    assert client.get(link).status_code == 404

    log_in_as(client, invite.created_by_id)
    second = invite_link(client, "rider")
    with app.app_context():
        open_one = db.session().scalar(select(Invite).where(Invite.role == "rider"))
    post(client, f"/manage/staff/invites/{open_one.id}/cancel")
    post(client, "/logout")
    assert client.get(second).status_code == 404


def test_an_invite_opened_while_logged_in_asks_to_log_out(client, owner, shopper_id):
    link = invite_link(owner)
    log_in_as(client, shopper_id)
    assert "Log out first" in client.get(link).get_data(as_text=True)


def test_only_the_owner_invites(client, cashier_id):
    log_in_as(client, cashier_id)
    assert post(client, "/manage/staff/invite", {"role": "cashier"}).status_code == 403


def test_an_invite_is_only_for_cashiers_and_riders(owner):
    assert post(owner, "/manage/staff/invite", {"role": "owner"}).status_code == 400


def test_switching_staff_off_logs_them_out(app, client, owner_id, cashier_id):
    other = app.test_client()
    log_in_as(other, cashier_id)
    log_in_as(client, owner_id)
    post(client, f"/manage/staff/{cashier_id}/active", {"active": "0"})
    assert not get(app, User, cashier_id).is_active
    assert not is_logged_in(other)
    post(client, f"/manage/staff/{cashier_id}/active", {"active": "1"})
    assert post(other, "/login", {"email": "cashier@example.com", "password": PASSWORD}).status_code == 302


def test_the_owner_cant_switch_themself_off(owner, owner_id):
    assert post(owner, f"/manage/staff/{owner_id}/active", {"active": "0"}).status_code == 400


def test_the_staff_page_lists_people_and_links(owner, cashier_id, rider_id):
    page = owner.get("/manage/staff").get_data(as_text=True)
    assert "Lorna Cruz" in page and "Jun Dizon" in page and "Dina Flores" in page
    assert "pill-cashier" in page and "pill-rider" in page


def test_store_details(app, owner, store_id):
    response = post(owner, "/manage/store", {"name": "DFJC Store", "tagline": "Wholesale and retail",
                                            "address": "Poblacion", "phone": "0917 555 0123",
                                            "hours": "Every day, 6 AM to 9 PM", "pickup_note": "At the counter"})
    assert response.status_code == 302
    store = get(app, Store, store_id)
    assert (store.address, store.hours) == ("Poblacion", "Every day, 6 AM to 9 PM")
    home = owner.get("/").get_data(as_text=True)
    assert "Every day, 6 AM to 9 PM" in home and "Poblacion" in home
    assert post(owner, "/manage/store", {"name": ""}).status_code == 400


# ---------- Settings ----------

def test_settings_change_details_and_password(app, shopper, shopper_id):
    assert shopper.get("/settings/").status_code == 200
    post(shopper, "/settings/", {"action": "details", "name": "Juan D. Cruz", "mobile": "0918 765 4321"})
    user = get(app, User, shopper_id)
    assert (user.name, user.mobile) == ("Juan D. Cruz", "09187654321")
    wrong = post(shopper, "/settings/", {"action": "password", "current_password": "nope",
                                         "new_password": "something new", "confirm_password": "something new"})
    assert wrong.status_code == 400
    post(shopper, "/settings/", {"action": "password", "current_password": PASSWORD,
                                 "new_password": "something new", "confirm_password": "something new"})
    assert get(app, User, shopper_id).session_version == 2
    assert is_logged_in(shopper)  # this device stays logged in


def test_settings_has_the_theme_and_log_out(shopper):
    page = shopper.get("/settings/").get_data(as_text=True)
    assert "data-theme-settings" in page and 'action="/logout"' in page
