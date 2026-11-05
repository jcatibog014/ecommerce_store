"""Staff invite links. The owner makes one for a cashier or rider; opening it
lets that person make their own login with that role. A link works once,
for 7 days. Only a hash of it is kept."""
import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select

from .models import Invite

INVITE_LIFETIME = timedelta(days=7)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_invite(s, store, role, name, by, moment):
    """Returns (invite, token). Show the token now: the database only has its hash."""
    token = secrets.token_urlsafe(24)
    invite = Invite(store_id=store.id, role=role, name=name, token_hash=token_hash(token),
                    created_by_id=by.id if by else None, expires_at=moment + INVITE_LIFETIME, created_at=moment)
    s.add(invite)
    s.flush()
    return invite, token


def find_open_invite(s, token, moment):
    """The invite for this token if it can still be used, else None."""
    invite = s.scalar(select(Invite).where(Invite.token_hash == token_hash(token or "")))
    if invite is None or invite.used_at is not None or invite.expires_at <= moment:
        return None
    return invite


def status(invite, moment):
    if invite.used_at:
        return "used"
    return "expired" if invite.expires_at <= moment else "open"
