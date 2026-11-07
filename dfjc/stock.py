"""Every change to stock, with a stock_moves row that says why, how many and who.

Stock changes are plain guarded UPDATEs, never "read, add, write back":

    UPDATE products SET stock_pieces = stock_pieces + 24 WHERE id = 7 RETURNING stock_pieces

Two people restocking at once both add their 24; nobody's change is lost.
Packing takes the held pieces off the shelf:

    UPDATE products SET stock_pieces = stock_pieces - n, reserved_pieces = reserved_pieces - n
    WHERE id = 7 AND reserved_pieces >= n AND stock_pieces >= n

A stock count sets the number the staff counted, but only if the stock is
still what they saw when they started counting (WHERE stock_pieces = <seen>);
if a sale happened meanwhile, they are asked to count again.

These UPDATEs don't touch products.version, so they never make someone's open
product form out of date. The caller commits.
"""
from sqlalchemy import func, select, update

from .models import MOVE_COUNT, MOVE_ORDER, MOVE_RESTOCK, MOVE_START, Product, StockMove

MAX_PIECES = 1_000_000


class StockProblem(ValueError):
    pass


class StockChanged(StockProblem):
    """The stock changed between showing the page and saving."""


def record(s, product, change, stock_after, reason, by, moment, note="", order_id=None):
    move = StockMove(store_id=product.store_id, product_id=product.id, change=change, stock_after=stock_after,
                     reason=reason, note=note[:200], order_id=order_id, by_id=by.id if by else None,
                     created_at=moment)
    s.add(move)
    return move


def starting_stock(s, product, by, moment, reason=MOVE_START, note=""):
    if product.stock_pieces:
        record(s, product, product.stock_pieces, product.stock_pieces, reason, by, moment, note)


def restock(s, product, pieces, by, moment, note=""):
    if not 1 <= pieces <= MAX_PIECES:
        raise StockProblem(f"Add from 1 to {MAX_PIECES:,} pieces.")
    after = s.execute(update(Product).where(Product.id == product.id)
                      .values(stock_pieces=Product.stock_pieces + pieces)
                      .returning(Product.stock_pieces).execution_options(synchronize_session=False)).scalar_one()
    return record(s, product, pieces, after, MOVE_RESTOCK, by, moment, note)


def count(s, product, counted, seen, by, moment, note=""):
    """Set the stock to what was counted on the shelf. seen: the stock shown when counting began."""
    if not 0 <= counted <= MAX_PIECES:
        raise StockProblem(f"Use a number from 0 to {MAX_PIECES:,}.")
    after = s.execute(update(Product)
                      .where(Product.id == product.id, Product.stock_pieces == seen,
                             Product.reserved_pieces <= counted)
                      .values(stock_pieces=counted)
                      .returning(Product.stock_pieces).execution_options(synchronize_session=False)).scalar()
    if after is None:
        now = s.execute(select(Product.stock_pieces, Product.reserved_pieces).where(Product.id == product.id)).one()
        if now.stock_pieces != seen:
            raise StockChanged(f"The stock changed while you were counting (it is {now.stock_pieces:,} now, "
                               f"it was {seen:,}). Please count again.")
        raise StockProblem(f"{now.reserved_pieces:,} pieces are held for orders, so at least that many are "
                           "on the shelf. Check the count again.")
    if counted == seen:
        return None
    return record(s, product, counted - seen, counted, MOVE_COUNT, by, moment, note)


def take_for_order(s, order, by, moment):
    """Packing starts: the order's held pieces leave the shelf for real."""
    from .orders import pieces_by_product
    for product_id, pieces in sorted(pieces_by_product(order).items()):
        after = s.execute(update(Product)
                          .where(Product.id == product_id, Product.reserved_pieces >= pieces,
                                 Product.stock_pieces >= pieces)
                          .values(stock_pieces=Product.stock_pieces - pieces,
                                  reserved_pieces=Product.reserved_pieces - pieces)
                          .returning(Product.stock_pieces).execution_options(synchronize_session=False)).scalar()
        if after is None:
            raise StockChanged("The stock of an item in this order was changed by hand, so it can't be packed. "
                               "Check the product's stock first.")
        product = s.get(Product, product_id)
        record(s, product, -pieces, after, MOVE_ORDER, by, moment, order_id=order.id, note=order.ref)


HISTORY_PAGE = 50


def history_query(store, product_id=None, reason=None, q=""):
    """Stock moves of a store; q keeps the moves of products whose name has every word."""
    from .catalog import search_condition
    query = select(StockMove).where(StockMove.store_id == store.id)
    if product_id:
        query = query.where(StockMove.product_id == product_id)
    if reason:
        query = query.where(StockMove.reason == reason)
    if q:
        query = query.where(StockMove.product_id.in_(select(Product.id).where(Product.store_id == store.id,
                                                                              *search_condition(q))))
    return query


def history(s, store, product_id=None, reason=None, limit=HISTORY_PAGE, q="", offset=0):
    query = history_query(store, product_id, reason, q)
    return s.scalars(query.order_by(StockMove.created_at.desc(), StockMove.id.desc())
                     .offset(offset).limit(limit)).all()


def history_count(s, store, reason=None, q=""):
    return s.scalar(select(func.count()).select_from(history_query(store, reason=reason, q=q).subquery()))
