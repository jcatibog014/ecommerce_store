"""The owner's Insights: what sold, where and how it was paid, over the last 7, 30 or 90 days.

A "sale" is money the store keeps:
  - an online order that was priced and not cancelled or refunded (counted on the
    day it was placed, at its final total);
  - a counter sale that wasn't voided.
Days are the store's own calendar days (Asia/Manila).

The chart geometry (bar heights, gridlines) is worked out here, so the page can
draw plain SVG with sizes as attributes (the page's CSP allows no inline styles).
"""
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select

from .dates import day_start_utc, local_today, to_local
from .models import (
    AWAITING_PAYMENT, CANCELLED, CASH_PICKUP, COD, PLACED, REFUNDED, RETAIL, WHOLESALE, Order, OrderLine, Product,
    WalkinLine, WalkinSale,
)

RANGES = {"7": "Last 7 Days", "30": "Last 30 Days", "90": "Last 90 Days"}
NOT_SALES = (PLACED, AWAITING_PAYMENT, CANCELLED, REFUNDED)

# The sales-per-day chart, in its own SVG units (it scales to the page width).
WIDTH, HEIGHT = 720, 240
LEFT, BOTTOM, TOP = 64, 28, 12


def window(days, today=None):
    """(first day, last day, start moment, end moment) for the last `days` days, today included."""
    today = today or local_today()
    first = today - timedelta(days=days - 1)
    return first, today, day_start_utc(first), day_start_utc(today + timedelta(days=1))


def online_orders(s, store, start, end):
    return s.scalars(select(Order).where(Order.store_id == store.id, Order.placed_at >= start, Order.placed_at < end,
                                         Order.status.not_in(NOT_SALES))).all()


def counter_sales(s, store, start, end):
    return s.scalars(select(WalkinSale).where(WalkinSale.store_id == store.id, WalkinSale.created_at >= start,
                                              WalkinSale.created_at < end, WalkinSale.voided_at.is_(None))).all()


def totals(orders, sales):
    online = sum(order.total for order in orders)
    counter = sum(sale.kept_total for sale in sales)
    count = len(orders) + len(sales)
    return {"online": online, "counter": counter, "total": online + counter, "count": count,
            "average": round((online + counter) / count) if count else 0}


def change(now, before):
    """+12 (percent) against the period before, or None when there was nothing before."""
    if not before:
        return None
    return round((now - before) * 100 / before)


def nice_ceiling(value):
    """A round top for the y axis: 0 -> 1,000 pesos; 3,400 -> 4,000; 12,600 -> 15,000."""
    if value <= 0:
        return 100000
    step = 10 ** (len(str(int(value))) - 1)
    for multiple in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if value <= multiple * step:
            return int(multiple * step)
    return int(10 * step)


def per_day(orders, sales, first, days):
    rows = {first + timedelta(days=offset): {"online": 0, "counter": 0} for offset in range(days)}
    for order in orders:
        day = to_local(order.placed_at).date()
        if day in rows:
            rows[day]["online"] += order.total
    for sale in sales:
        day = to_local(sale.created_at).date()
        if day in rows:
            rows[day]["counter"] += sale.kept_total
    return [{"day": day, **values, "total": values["online"] + values["counter"]} for day, values in rows.items()]


def bar_top(x, y, width, height, radius=4):
    """A bar with rounded top corners only (the data end), as an SVG path."""
    radius = min(radius, width / 2, height)
    return (f"M{x:.1f},{y + height:.1f} V{y + radius:.1f} Q{x:.1f},{y:.1f} {x + radius:.1f},{y:.1f} "
            f"H{x + width - radius:.1f} Q{x + width:.1f},{y:.1f} {x + width:.1f},{y + radius:.1f} "
            f"V{y + height:.1f} Z")


def day_chart(rows):
    """Stacked bars (counter below, online on top) with a 2-unit surface gap between them."""
    top = nice_ceiling(max((row["total"] for row in rows), default=0))
    plot_width, plot_height = WIDTH - LEFT - 8, HEIGHT - BOTTOM - TOP
    slot = plot_width / len(rows)
    bar = max(4.0, min(36.0, slot * 0.62))
    label_every = 1 if len(rows) <= 10 else 5 if len(rows) <= 31 else 14
    bars = []
    for index, row in enumerate(rows):
        x = LEFT + index * slot + (slot - bar) / 2
        counter_h = plot_height * row["counter"] / top
        online_h = plot_height * row["online"] / top
        base = TOP + plot_height
        counter_path = bar_top(x, base - counter_h, bar, counter_h) if counter_h >= 1 else ""
        gap = 2 if counter_h >= 1 else 0
        online_path = bar_top(x, base - counter_h - gap - online_h, bar, online_h) if online_h >= 1 else ""
        bars.append({**row, "x": x, "center": x + bar / 2, "counter_path": counter_path, "online_path": online_path,
                     "hit_x": LEFT + index * slot, "hit_w": slot,
                     "label": f"{row['day']:%b} {row['day'].day}" if index % label_every == 0 or
                     index == len(rows) - 1 else ""})
    grid = [{"y": TOP + plot_height - plot_height * part / 4, "value": top * part // 4} for part in range(5)]
    return {"bars": bars, "grid": grid, "width": WIDTH, "height": HEIGHT, "left": LEFT, "top": TOP,
            "base": TOP + plot_height, "right": WIDTH - 8}


def best_sellers(s, store, orders, sales, limit=10):
    """[(name, pieces, amount)] by amount, online and counter together."""
    pieces, amount = defaultdict(int), defaultdict(int)
    order_ids = [order.id for order in orders]
    if order_ids:
        for line in s.scalars(select(OrderLine).where(OrderLine.order_id.in_(order_ids))):
            pieces[line.product_name] += line.pieces
            amount[line.product_name] += line.line_total
    sale_ids = [sale.id for sale in sales]
    if sale_ids:
        for line in s.scalars(select(WalkinLine).where(WalkinLine.sale_id.in_(sale_ids))):
            if line.kept_quantity:  # voided items weren't sold
                pieces[line.product_name] += line.kept_pieces
                amount[line.product_name] += line.kept_total
    ranked = sorted(amount, key=lambda name: (-amount[name], name))[:limit]
    return [(name, pieces[name], amount[name]) for name in ranked]


def price_types(s, orders, sales):
    """{"retail": amount, "wholesale": amount} of the items sold (before delivery and vouchers)."""
    found = {RETAIL: 0, WHOLESALE: 0}
    order_ids = [order.id for order in orders]
    if order_ids:
        for line in s.scalars(select(OrderLine).where(OrderLine.order_id.in_(order_ids))):
            found[line.price_type] += line.line_total
    sale_ids = [sale.id for sale in sales]
    if sale_ids:
        for line in s.scalars(select(WalkinLine).where(WalkinLine.sale_id.in_(sale_ids))):
            found[line.price_type] += line.kept_total
    return found


def payment_split(orders, sales):
    """[(label, amount)], biggest first."""
    split = {"Online (GCash, Maya, card)": 0, "Cash at pickup": 0, "Cash on delivery": 0, "Counter (cash)": 0}
    for order in orders:
        label = ("Cash at pickup" if order.payment_method == CASH_PICKUP else
                 "Cash on delivery" if order.payment_method == COD else "Online (GCash, Maya, card)")
        split[label] += order.total
    split["Counter (cash)"] = sum(sale.kept_total for sale in sales)
    return sorted(split.items(), key=lambda item: -item[1])


def bars(items, value_index=1):
    """Widths (percent of the biggest) for a list of horizontal bars."""
    biggest = max((item[value_index] for item in items), default=0) or 1
    return [(item, round(item[value_index] * 100 / biggest, 1)) for item in items]


def low_stock(s, store, limit=8):
    found = s.scalars(select(Product).where(Product.store_id == store.id, Product.is_active.is_(True),
                                            Product.low_stock_pieces > 0,
                                            Product.stock_pieces - Product.reserved_pieces <= Product.low_stock_pieces)
                      .order_by(Product.stock_pieces - Product.reserved_pieces)).all()
    return found[:limit]


def report(s, store, days, today=None):
    first, last, start, end = window(days, today)
    orders, sales = online_orders(s, store, start, end), counter_sales(s, store, start, end)
    before_start = start - timedelta(days=days)
    before = totals(online_orders(s, store, before_start, start), counter_sales(s, store, before_start, start))
    now = totals(orders, sales)
    rows = per_day(orders, sales, first, days)
    kinds = price_types(s, orders, sales)
    items = kinds[RETAIL] + kinds[WHOLESALE]
    return {
        "first": first, "last": last, "days": days, "now": now, "before": before,
        "change": change(now["total"], before["total"]),
        "online_share": round(now["online"] * 100 / now["total"]) if now["total"] else 0,
        "rows": rows, "chart": day_chart(rows),
        "best": bars(best_sellers(s, store, orders, sales), value_index=2),
        "kinds": kinds, "wholesale_share": round(kinds[WHOLESALE] * 100 / items) if items else 0,
        "payments": bars(payment_split(orders, sales)),
        "low": low_stock(s, store),
    }
