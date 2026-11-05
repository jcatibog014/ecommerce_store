"""The owner's Insights page, and the service worker for the phone app."""
from flask import Blueprint, current_app, render_template, request
from flask import g

from .. import db, insights
from ..permissions import SEE_INSIGHTS, needs

bp = Blueprint("insights", __name__)


@bp.get("/manage/insights")
@needs(SEE_INSIGHTS, "Only the owner can see Insights.")
def index():
    key = request.args.get("range") if request.args.get("range") in insights.RANGES else "7"
    report = insights.report(db.session(), g.store, int(key))
    return render_template("manage/insights.html", report=report, range_key=key, ranges=insights.RANGES)


@bp.get("/sw.js")
def service_worker():
    """From the site's root, so it may look after every page (a worker only covers its own folder and below)."""
    response = current_app.send_static_file("sw.js")
    response.headers["Cache-Control"] = "no-cache"  # always check for a new version
    response.headers["Service-Worker-Allowed"] = "/"
    return response

