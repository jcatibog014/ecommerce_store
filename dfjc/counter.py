"""The counter: walk-in sales, the cashier's shift, and the cash in the drawer.

sell() is ONE transaction: check the cash, take the stock, write the sale,
its lines and the stock history; or nothing at all. It takes stock with the
same guard as online orders, so the counter can never sell pieces that are
held for an online order:

    UPDATE products SET stock_pieces = stock_pieces - n
    WHERE id = ? AND stock_pieces - reserved_pieces >= n

The ticket (the sale being rung up) lives in the cashier's session until Pay.
Its key is UNIQUE on walkin_sales, so pressing Pay twice records one sale.

A shift: open with the starting cash, close with the count. Expected cash =
starting cash + counter sales + pickup cash this cashier took + rider cash
this cashier received, all during the shift. At most one shift per cashier
is open (a partial UNIQUE index), even if Open is pressed twice at once.

Voiding is per item: the cashier chooses which items (and how many of each) to take off a
sale. Each line's voided_quantity only grows with a guarded UPDATE (never past its quantity),
so pressing Void twice can't give back the same item twice. The rest of the sale stays.
"""
import secrets

from sqlalchemy import func, select, update

from . import catalog
from .models import (
    MIXED, MOVE_WALKIN, RETAIL, WHOLESALE, CashShift, Payment, Product, ProductUnit, Remittance, StockMove,
    WalkinLine, WalkinSale, WalkinVoid,
)

MAX_QUANTITY = 999
MAX_LINES = 40  # one ticket; the session cookie stays small


class CounterProblem(ValueError):
    pass


def new_key():
    return secrets.token_urlsafe(18)


# ---------- Shifts ----------

def open_shift(s, store, cashier, opening, moment):
    """The caller commits; a second open shift for the same cashier fails with IntegrityError."""
    shift = CashShift(store_id=store.id, cashier_id=cashier.id, opening_cash=opening, opened_at=moment)
    s.add(shift)
    s.flush()
    return shift


def current_shift(s, store, cashier):
    return s.scalar(select(CashShift).where(CashShift.store_id == store.id, CashShift.cashier_id == cashier.id,
                                            CashShift.closed_at.is_(None)))


def summary(s, shift, moment):
    """The money this shift should have in the drawer."""
    until = shift.closed_at or moment
    sales = s.scalar(select(func.coalesce(func.sum(WalkinSale.total - WalkinSale.voided_amount), 0)).where(
        WalkinSale.shift_id == shift.id, WalkinSale.voided_at.is_(None))) or 0
    sale_count = s.scalar(select(func.count()).select_from(WalkinSale).where(
        WalkinSale.shift_id == shift.id, WalkinSale.voided_at.is_(None)))
    pickup = s.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.gateway == "cash", Payment.collected_by_id == shift.cashier_id, Payment.remittance_id.is_(None),
        Payment.paid_at >= shift.opened_at, Payment.paid_at <= until)) or 0
    riders = s.scalar(select(func.coalesce(func.sum(Remittance.amount), 0)).where(
        Remittance.received_by_id == shift.cashier_id, Remittance.created_at >= shift.opened_at,
        Remittance.created_at <= until)) or 0
    return {"opening": shift.opening_cash, "sales": sales, "sale_count": sale_count, "pickup": pickup,
            "riders": riders, "expected": shift.opening_cash + sales + pickup + riders}


def close_shift(s, shift, counted, note, moment):
    expected = summary(s, shift, moment)["expected"]
    closed = s.execute(update(CashShift).where(CashShift.id == shift.id, CashShift.closed_at.is_(None))
                       .values(closed_at=moment, expected_cash=expected, counted_cash=counted, note=note[:300])
                       .execution_options(synchronize_session=False)).rowcount
    if closed != 1:
        raise CounterProblem("This shift was already closed.")
    s.refresh(shift)
    return shift


# ---------- Finding products at the counter ----------

def find(s, store, q):
    """Products for the search box: an exact barcode first, else every word in the name."""
    q = (q or "").strip()[:80]
    if not q:
        return [], None
    if q.isdigit():
        unit = s.scalar(select(ProductUnit).join(Product).where(
            Product.store_id == store.id, ProductUnit.barcode == q, ProductUnit.is_active.is_(True),
            Product.is_active.is_(True)))
        if unit is not None:
            return [unit.product], unit
    products = s.scalars(select(Product).where(Product.store_id == store.id, Product.is_active.is_(True),
                                               *catalog.search_condition(q))
                         .order_by(func.lower(Product.name)).limit(12)).all()
    return products, None


# ---------- The ticket ----------

def ticket_lines(s, store, ticket):
    """[{index, unit, quantity, price_type, unit_price, line_total}] for the units that are still sold."""
    lines = []
    for index, (unit_id, quantity, price_type) in enumerate(ticket):
        unit = s.get(ProductUnit, unit_id)
        if unit is None or not unit.is_active or unit.product.store_id != store.id or not unit.product.is_active:
            continue
        price = unit.wholesale_price if price_type == WHOLESALE else unit.retail_price
        lines.append({"index": index, "unit": unit, "quantity": quantity, "price_type": price_type,
                      "unit_price": price, "line_total": price * quantity})
    return lines


def add_line(ticket, unit_id, quantity, price_type):
    """Add to the ticket (merging the same unit at the same price). Returns the new ticket."""
    price_type = WHOLESALE if price_type == WHOLESALE else RETAIL
    ticket = [list(line) for line in ticket]
    for line in ticket:
        if line[0] == unit_id and line[2] == price_type:
            line[1] = min(MAX_QUANTITY, line[1] + quantity)
            return ticket
    if len(ticket) >= MAX_LINES:
        raise CounterProblem(f"A ticket can have {MAX_LINES} lines. Pay this one first.")
    return ticket + [[unit_id, min(MAX_QUANTITY, quantity), price_type]]


def kind_of(lines):
    kinds = {line["price_type"] for line in lines}
    return kinds.pop() if len(kinds) == 1 else MIXED


# ---------- Selling ----------

def sell(s, store, shift, cashier, ticket, cash_given, key, moment):
    """Ring up the ticket. Returns (sale, made_now). The caller commits, or rolls back on any error."""
    existing = s.scalar(select(WalkinSale).where(WalkinSale.ticket_key == key))
    if existing is not None:
        return existing, False
    if shift is None or shift.closed_at is not None:
        raise CounterProblem("Open a shift first.")
    lines = ticket_lines(s, store, ticket)
    if not lines:
        raise CounterProblem("The ticket is empty.")
    total = sum(line["line_total"] for line in lines)
    if cash_given is None or cash_given < total:
        raise CounterProblem(f"The cash given must be at least the total, {total / 100:,.2f} pesos.")
    sale = WalkinSale(store_id=store.id, shift_id=shift.id, cashier_id=cashier.id, price_type=kind_of(lines),
                      total=total, cash_given=cash_given, change=cash_given - total, ticket_key=key,
                      created_at=moment)
    for line in lines:
        unit = line["unit"]
        sale.lines.append(WalkinLine(
            product_id=unit.product_id, unit_id=unit.id, product_name=unit.product.name, unit_name=unit.name,
            pieces_per_unit=unit.pieces_per_unit, quantity=line["quantity"],
            pieces=line["quantity"] * unit.pieces_per_unit, price_type=line["price_type"],
            unit_price=line["unit_price"], line_total=line["line_total"]))
    s.add(sale)
    s.flush()  # a second Pay with the same key fails here (UNIQUE)
    pieces, names = {}, {}
    for line in sale.lines:
        pieces[line.product_id] = pieces.get(line.product_id, 0) + line.pieces
        names[line.product_id] = line.product_name
    for product_id in sorted(pieces):
        after = s.execute(update(Product)
                          .where(Product.id == product_id, Product.store_id == store.id,
                                 Product.stock_pieces - Product.reserved_pieces >= pieces[product_id])
                          .values(stock_pieces=Product.stock_pieces - pieces[product_id])
                          .returning(Product.stock_pieces).execution_options(synchronize_session=False)).scalar()
        if after is None:
            free = s.scalar(select(Product.stock_pieces - Product.reserved_pieces).where(Product.id == product_id))
            raise CounterProblem(f"Only {max(0, free or 0):,} pieces of {names[product_id]} can be sold "
                                 "(the rest are held for online orders). Lower the amount.")
        s.add(StockMove(store_id=store.id, product_id=product_id, change=-pieces[product_id], stock_after=after,
                        reason=MOVE_WALKIN, note=sale.receipt_no, by_id=cashier.id, created_at=moment))
    return sale, True


def void(s, sale, by, moment, reason, quantities):
    """Take items off a sale from the open shift. quantities is {line id: how many to void}.
    Those items go back in stock and stop counting; the rest of the sale stays. Returns the
    WalkinVoid (its amount is the cash to give back). The caller commits, or rolls back on error."""
    if sale.shift.closed_at is not None:
        raise CounterProblem("This sale's shift is closed, so it can't be voided.")
    lines = {line.id: line for line in sale.lines}
    chosen = {line_id: count for line_id, count in quantities.items() if line_id in lines and count > 0}
    if not chosen:
        raise CounterProblem("Choose the items to void, and how many of each.")
    amount, items, pieces = 0, [], {}
    for line_id in sorted(chosen):
        line, count = lines[line_id], chosen[line_id]
        # Guarded: never more than the line still has, even if Void is pressed twice at once.
        changed = s.execute(update(WalkinLine).where(WalkinLine.id == line_id,
                                                     WalkinLine.voided_quantity + count <= WalkinLine.quantity)
                            .values(voided_quantity=WalkinLine.voided_quantity + count)
                            .execution_options(synchronize_session=False)).rowcount
        if changed != 1:
            raise CounterProblem(f"Only {line.kept_quantity:,} of {line.product_name} ({line.unit_name}) "
                                 "is left on this sale to void.")
        amount += count * line.unit_price
        items.append(f"{count} x {line.product_name} ({line.unit_name})")
        if line.product_id is not None:
            pieces[line.product_id] = pieces.get(line.product_id, 0) + count * line.pieces_per_unit
    s.execute(update(WalkinSale).where(WalkinSale.id == sale.id)
              .values(voided_amount=WalkinSale.voided_amount + amount)
              .execution_options(synchronize_session=False))
    for product_id in sorted(pieces):
        after = s.execute(update(Product).where(Product.id == product_id)
                          .values(stock_pieces=Product.stock_pieces + pieces[product_id])
                          .returning(Product.stock_pieces).execution_options(synchronize_session=False)).scalar()
        s.add(StockMove(store_id=sale.store_id, product_id=product_id, change=pieces[product_id], stock_after=after,
                        reason=MOVE_WALKIN, note=f"Voided on {sale.receipt_no}: {reason}"[:200], by_id=by.id,
                        created_at=moment))
    record = WalkinVoid(sale_id=sale.id, items=", ".join(items)[:500], amount=amount, reason=reason[:200],
                        by_id=by.id, created_at=moment)
    s.add(record)
    s.flush()
    s.refresh(sale)
    for line in sale.lines:
        s.refresh(line)
    if all(line.kept_quantity == 0 for line in sale.lines):
        # Everything is voided: the sale no longer counts at all.
        s.execute(update(WalkinSale).where(WalkinSale.id == sale.id, WalkinSale.voided_at.is_(None))
                  .values(voided_at=moment).execution_options(synchronize_session=False))
        s.refresh(sale)
    return record


def shifts(s, store, cashier=None, limit=50):
    query = select(CashShift).where(CashShift.store_id == store.id)
    if cashier is not None:
        query = query.where(CashShift.cashier_id == cashier.id)
    return s.scalars(query.order_by(CashShift.opened_at.desc()).limit(limit)).all()

