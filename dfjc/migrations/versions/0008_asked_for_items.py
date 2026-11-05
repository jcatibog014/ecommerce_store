"""Asked-for items: "Not in the list? Ask for it" in the cart, and asked-for order lines.

An asked-for order line holds no stock (pieces 0), so the order lines' CHECK changes from
"pieces >= 1" to "pieces >= 1 OR is_request". Alembic doesn't notice a changed CHECK by
itself, so that part is written by hand.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-06 02:25:42.732340
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('cart_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('wanted', sa.String(length=80), nullable=False),
    sa.Column('unit_name', sa.String(length=40), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('added_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('quantity >= 1', name=op.f('ck_cart_requests_quantity_positive')),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], name=op.f('fk_cart_requests_product_id_products'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_cart_requests_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cart_requests'))
    )
    with op.batch_alter_table('cart_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_cart_requests_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('order_lines', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_request', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('request_status', sa.String(length=12), server_default='', nullable=False))
        batch_op.drop_constraint(op.f('ck_order_lines_quantity_positive'), type_='check')
        batch_op.create_check_constraint(op.f('ck_order_lines_quantity_positive'), 'quantity >= 1 AND (pieces >= 1 OR is_request)')


def downgrade():
    with op.batch_alter_table('order_lines', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_order_lines_quantity_positive'), type_='check')
        batch_op.create_check_constraint(op.f('ck_order_lines_quantity_positive'), 'quantity >= 1 AND pieces >= 1')
        batch_op.drop_column('request_status')
        batch_op.drop_column('is_request')

    with op.batch_alter_table('cart_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_cart_requests_user_id'))

    op.drop_table('cart_requests')
