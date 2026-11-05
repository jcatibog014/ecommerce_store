"""After an order is paid or confirmed: packing, pickup, the rider's trip, and the rider's cash.

Each step is orders.move() plus what has to happen with it, in one transaction:
  - start packing: the held pieces leave the shelf (stock.take_for_order);
  - picked up: a cash order's money is recorded as taken by the cashier;
  - delivered: a cash-on-delivery order's money is recorded as with the rider;
  - nobody home: back to Ready, so the cashier can send it again.

A rider hands their cash to the cashier with remit(): the payments are claimed
with a conditional UPDATE (... WHERE remittance_id IS NULL), so the same peso is
never handed over twice, even if two cashiers press Receive at once.
"""
from sqlalchemy import func, select, update

from . import orders
from .models import (
    COD, DELIVERED, GATEWAY_CASH, OUT_FOR_DELIVERY, PACKING, PAYMENT_PAID, PICKED_UP, READY, RIDER, Payment, Remittance,
    User,
)
from .orders import CASH_METHODS


class FulfilProblem(ValueError):
    pass


def start_packing(s, order, by, moment):
    return orders.move(s, order, PACKING, by, moment)


def packed(s, order, by, moment):
    return orders.move(s, order, READY, by, moment)


def record_cash(s, order, collected_by, moment):
    """The cash for a cash order changed hands. Online orders were paid already."""
    if order.payment_method not in CASH_METHODS or order.paid_payment is not None:
        return None
    payment = Payment(order_id=order.id, gateway=GATEWAY_CASH, status=PAYMENT_PAID, amount=order.total,
                      paid_with="cash", collected_by_id=collected_by.id, created_at=moment, paid_at=moment)
    s.add(payment)
    order.payments.append(payment)
    return payment


def hand_over(s, order, by, moment):
    """The shopper picked up their order at the counter (and paid, if it is cash)."""
    note = f"Cash {order.total / 100:,.2f} pesos received" if order.payment_method in CASH_METHODS else ""
    orders.move(s, order, PICKED_UP, by, moment, note=note)
    record_cash(s, order, by, moment)
    return order


def send_out(s, order, rider, by, moment):
    if rider is None or rider.role != RIDER or not rider.is_active or rider.store_id != order.store_id:
        raise FulfilProblem("Choose a rider from the list.")
    return orders.move(s, order, OUT_FOR_DELIVERY, by, moment, note=f"With {rider.first_name}", rider_id=rider.id)


def check_rider(order, by, role):
    if role == RIDER and order.rider_id != by.id:
        raise FulfilProblem("This delivery is with another rider.")


def delivered(s, order, by, role, moment):
    check_rider(order, by, role)
    collector = s.get(User, order.rider_id) if order.rider_id else by
    note = f"Cash {order.total / 100:,.2f} pesos collected" if order.payment_method == COD else ""
    orders.move(s, order, DELIVERED, by, moment, note=note)
    record_cash(s, order, collector, moment)
    return order


def not_home(s, order, by, role, moment, note):
    check_rider(order, by, role)
    return orders.move(s, order, READY, by, moment, note=note or "Nobody was home", rider_id=None)


# ---------- The rider's cash ----------

def cash_with(s, rider):
    """Cash-on-delivery payments this rider collected and hasn't handed over yet."""
    return s.scalars(select(Payment).where(Payment.collected_by_id == rider.id, Payment.gateway == GATEWAY_CASH,
                                           Payment.status == PAYMENT_PAID, Payment.remittance_id.is_(None))
                     .order_by(Payment.paid_at)).all()


def riders_with_cash(s, store):
    """[(rider, amount, payments)] for riders holding store cash."""
    riders = s.scalars(select(User).where(User.store_id == store.id, User.role == RIDER).order_by(User.name)).all()
    found = []
    for rider in riders:
        held = cash_with(s, rider)
        if held:
            found.append((rider, sum(payment.amount for payment in held), held))
    return found


def remit(s, store, rider, payment_ids, by, moment):
    """The cashier receives the cash for exactly these payments. The caller commits."""
    payment_ids = sorted(set(payment_ids))
    if not payment_ids:
        raise FulfilProblem("There is no cash to receive.")
    amount = s.scalar(select(func.sum(Payment.amount)).where(Payment.id.in_(payment_ids)))
    remittance = Remittance(store_id=store.id, rider_id=rider.id, received_by_id=by.id, amount=amount or 0,
                            created_at=moment)
    s.add(remittance)
    s.flush()
    claimed = s.execute(update(Payment)
                        .where(Payment.id.in_(payment_ids), Payment.collected_by_id == rider.id,
                               Payment.gateway == GATEWAY_CASH, Payment.remittance_id.is_(None))
                        .values(remittance_id=remittance.id).execution_options(synchronize_session=False)).rowcount
    if claimed != len(payment_ids):
        raise FulfilProblem("Some of this cash was already received a moment ago. Reload the page and check again.")
    return remittance


def remittances(s, store, limit=30):
    return s.scalars(select(Remittance).where(Remittance.store_id == store.id)
                     .order_by(Remittance.created_at.desc()).limit(limit)).all()
