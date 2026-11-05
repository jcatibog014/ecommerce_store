"""Sending email (password reset now; order updates from stage 6).

On the live site it goes through Gmail with an app password (MAIL_USERNAME
and MAIL_PASSWORD on Render). Without them, and always in tests, the email
is only kept in a list and written to the terminal, so a reset link can
still be copied from there on your own computer.
"""
import smtplib
from email.message import EmailMessage

from flask import current_app

GMAIL_HOST = "smtp.gmail.com"


def outbox(app=None):
    """Emails that weren't really sent (tests read this)."""
    return (app or current_app).extensions.setdefault("dfjc_outbox", [])


def send(to, subject, body):
    app = current_app
    message = EmailMessage()
    sender = app.config.get("MAIL_USERNAME") or "dfjc-store@localhost"
    message["From"] = f"DFJC Store <{sender}>"
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    if app.testing or not (app.config.get("MAIL_USERNAME") and app.config.get("MAIL_PASSWORD")):
        outbox(app).append(message)
        app.logger.warning("Email not sent (MAIL_USERNAME isn't set). To %s: %s\n%s", to, subject, body)
        return
    with smtplib.SMTP_SSL(GMAIL_HOST, 465, timeout=20) as smtp:
        smtp.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"])
        smtp.send_message(message)
