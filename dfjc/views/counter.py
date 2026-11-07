"""The counter screen: open a shift, ring up a walk-in sale, receipts, void, and closing the shift.
The rules are in counter.py; the ticket being rung up lives in the session until Pay."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import counter, db, forms
from ..counter import CounterProblem
from ..dates import utcnow
from ..models import OWNER, RETAIL, WHOLESALE, CashShift, ProductUnit, WalkinSale
from ..permissions import COUNTER, needs
from ..stores import get_in_store

bp = Blueprint("counter", __name__, url_prefix="/counter")


def ticket():
    return session.get("ticket", [])


def save_ticket(lines):
    session["ticket"] = lines


def ticket_key():
    if "ticket_key" not in session:
        session["ticket_key"] = counter.new_key()
    return session["ticket_key"]


def here():
    return redirect(url_for("counter.index", q=request.form.get("q") or None))


@bp.get("")
@needs(COUNTER, "Only a cashier or the owner can use the counter.")
def index():
    s = db.session()
    shift = counter.current_shift(s, g.store, g.user)
    if shift is None:
        return render_template("manage/counter_open.html", recent=counter.shifts(s, g.store, g.user, limit=5))
    q = request.args.get("q", "")
    found, exact = counter.find(s, g.store, q)
    if exact is not None:
        # A scanned barcode: straight onto the ticket, one piece at retail.
        try:
            save_ticket(counter.add_line(ticket(), exact.id, 1, RETAIL))
            session["just_added"] = counter.line_index(ticket(), exact.id, RETAIL)
            flash(f"Added {exact.product.name} ({exact.name}).")
        except CounterProblem as problem:
            flash(str(problem), "error")
        return redirect(url_for("counter.index"))
    lines = counter.ticket_lines(s, g.store, ticket())
    recent = s.scalars(select(WalkinSale).where(WalkinSale.shift_id == shift.id)
                       .order_by(WalkinSale.id.desc()).limit(8)).all()
    return render_template("manage/counter.html", shift=shift, q=q, found=found, lines=lines,
                           total=sum(line["line_total"] for line in lines), key=ticket_key(), recent=recent,
                           summary=counter.summary(s, shift, utcnow()), just_added=session.pop("just_added", None))


@bp.post("/open")
@needs(COUNTER)
def open_shift():
    s = db.session()
    opening, problem = forms.price(request.form, "opening_cash")
    if problem:
        flash("Type the cash in the drawer now, like 1000 or 1000.50.", "error")
        return redirect(url_for("counter.index"))
    try:
        counter.open_shift(s, g.store, g.user, opening, utcnow())
        s.commit()
    except IntegrityError:
        s.rollback()  # already open (Open pressed twice): use that one
    flash(f"Your shift is open with {opening / 100:,.2f} pesos in the drawer.")
    return redirect(url_for("counter.index"))


@bp.post("/add")
@needs(COUNTER)
def add():
    s = db.session()
    unit = s.get(ProductUnit, request.form.get("unit_id", type=int) or 0)
    if unit is None or unit.product.store_id != g.store.id:
        abort(404)
    quantity, problem = forms.whole_number(request.form, "quantity", 1, counter.MAX_QUANTITY, default=1)
    if problem:
        flash(problem, "error")
        return here()
    try:
        save_ticket(counter.add_line(ticket(), unit.id, quantity, request.form.get("price_type")))
        # The line lights up once, so the cashier sees it went on (and doesn't scan it twice).
        session["just_added"] = counter.line_index(ticket(), unit.id, request.form.get("price_type"))
    except CounterProblem as trouble:
        flash(str(trouble), "error")
    return here()


@bp.post("/line/<int:index>")
@needs(COUNTER)
def line(index):
    lines = [list(item) for item in ticket()]
    if not 0 <= index < len(lines):
        return here()
    action = request.form.get("action")
    if action == "remove":
        lines.pop(index)
    elif action in (RETAIL, WHOLESALE):
        lines[index][2] = action
    else:
        quantity, problem = forms.whole_number(request.form, "quantity", 0, counter.MAX_QUANTITY)
        if problem:
            flash(problem, "error")
            return here()
        if quantity == 0:
            lines.pop(index)
        else:
            lines[index][1] = quantity
    save_ticket(lines)
    return here()


@bp.post("/all")
@needs(COUNTER)
def set_all():
    kind = WHOLESALE if request.form.get("price_type") == WHOLESALE else RETAIL
    save_ticket([[unit_id, quantity, kind] for unit_id, quantity, _ in ticket()])
    return here()


@bp.post("/clear")
@needs(COUNTER)
def clear():
    save_ticket([])
    return here()


@bp.post("/pay")
@needs(COUNTER)
def pay():
    s = db.session()
    shift = counter.current_shift(s, g.store, g.user)
    cash_given, problem = forms.price(request.form, "cash_given")
    key = request.form.get("ticket_key", "")
    if problem:
        flash("Type the cash the customer gave, like 500.", "error")
        return redirect(url_for("counter.index"))
    try:
        sale, _ = counter.sell(s, g.store, shift, g.user, ticket(), cash_given, key, utcnow())
        s.commit()
    except CounterProblem as trouble:
        s.rollback()
        flash(str(trouble), "error")
        return redirect(url_for("counter.index"))
    except IntegrityError:
        s.rollback()  # Pay pressed twice at the same moment: the other press made the sale
        sale = s.scalar(select(WalkinSale).where(WalkinSale.ticket_key == key))
        if sale is None:
            raise
    save_ticket([])
    session["ticket_key"] = counter.new_key()
    flash(f"Sold. Change: {sale.change / 100:,.2f} pesos.")
    return redirect(url_for("counter.receipt", sale_id=sale.id))


@bp.get("/sales/<int:sale_id>")
@needs(COUNTER)
def receipt(sale_id):
    sale = get_in_store(db.session(), WalkinSale, sale_id, g.store)
    return render_template("manage/receipt.html", sale=sale)


@bp.post("/sales/<int:sale_id>/void")
@needs(COUNTER)
def void(sale_id):
    s = db.session()
    sale = get_in_store(s, WalkinSale, sale_id, g.store)
    reason, _ = forms.text(request.form, "reason", 200)
    if not reason:
        flash("Say why these items are voided.", "error")
        return redirect(url_for("counter.receipt", sale_id=sale.id))
    # One number per item on the sale: how many of it to void (0 keeps it). Void all: everything left.
    quantities = {}
    for line in sale.lines:
        if request.form.get("all") == "1":
            quantities[line.id] = line.kept_quantity
            continue
        count = request.form.get(f"void_{line.id}", type=int) or 0
        if count < 0:
            count = 0
        quantities[line.id] = min(count, counter.MAX_QUANTITY)
    try:
        record = counter.void(s, sale, g.user, utcnow(), reason, quantities)
        s.commit()
    except CounterProblem as trouble:
        s.rollback()
        flash(str(trouble), "error")
        return redirect(url_for("counter.receipt", sale_id=sale.id))
    rest = ("Nothing is left on it." if sale.voided_at else
            f"The rest of the sale stays: {sale.kept_total / 100:,.2f} pesos.")
    flash(f"Voided {record.items}. Back in stock; give back {record.amount / 100:,.2f} pesos. {rest}")
    return redirect(url_for("counter.receipt", sale_id=sale.id))


@bp.route("/shift", methods=["GET", "POST"])
@needs(COUNTER)
def shift_page():
    s = db.session()
    shift = counter.current_shift(s, g.store, g.user)
    if shift is None:
        return redirect(url_for("counter.index"))
    if request.method == "POST":
        counted, problem = forms.price(request.form, "counted_cash")
        note, _ = forms.text(request.form, "note", 300)
        if problem:
            flash("Type the cash you counted in the drawer.", "error")
            return redirect(url_for("counter.shift_page"))
        try:
            counter.close_shift(s, shift, counted, note, utcnow())
            s.commit()
        except CounterProblem as trouble:
            s.rollback()
            flash(str(trouble), "error")
            return redirect(url_for("counter.shifts_page"))
        difference = shift.difference
        words = "The drawer matches." if difference == 0 else (
            f"The drawer has {abs(difference) / 100:,.2f} pesos {'more' if difference > 0 else 'less'} than expected.")
        flash(f"Your shift is closed. {words}", "message" if difference == 0 else "error")
        return redirect(url_for("counter.shifts_page"))
    return render_template("manage/shift.html", shift=shift, summary=counter.summary(s, shift, utcnow()))


@bp.get("/shifts")
@needs(COUNTER)
def shifts_page():
    s = db.session()
    found = counter.shifts(s, g.store, None if g.role == OWNER else g.user)
    sums = {shift.id: counter.summary(s, shift, utcnow()) for shift in found}
    return render_template("manage/shifts.html", shifts=found, sums=sums, everyone=g.role == OWNER,
                           CashShift=CashShift)
