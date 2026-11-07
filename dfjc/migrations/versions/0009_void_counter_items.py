"""Void counter items one by one: a sale keeps the items that weren't voided.

walkin_lines.voided_quantity counts the voided part of each line, walkin_sales.voided_amount
the money given back, and walkin_voids keeps each void (items, amount, reason, who, when).
Sales voided whole before this move into walkin_voids, and void_reason goes.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-07 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('walkin_voids',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sale_id', sa.Integer(), nullable=False),
    sa.Column('items', sa.String(length=500), nullable=False),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=200), nullable=False),
    sa.Column('by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('amount >= 0', name=op.f('ck_walkin_voids_amount_not_negative')),
    sa.ForeignKeyConstraint(['by_id'], ['users.id'], name=op.f('fk_walkin_voids_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['sale_id'], ['walkin_sales.id'], name=op.f('fk_walkin_voids_sale_id_walkin_sales'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_walkin_voids'))
    )
    with op.batch_alter_table('walkin_voids', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_walkin_voids_sale_id'), ['sale_id'], unique=False)

    with op.batch_alter_table('walkin_lines', schema=None) as batch_op:
        batch_op.add_column(sa.Column('voided_quantity', sa.Integer(), server_default='0', nullable=False))
        batch_op.create_check_constraint(op.f('ck_walkin_lines_voided_in_range'), 'voided_quantity >= 0 AND voided_quantity <= quantity')

    with op.batch_alter_table('walkin_sales', schema=None) as batch_op:
        batch_op.add_column(sa.Column('voided_amount', sa.Integer(), server_default='0', nullable=False))

    # Sales voided whole before: every item voided, the whole total given back.
    op.execute("UPDATE walkin_lines SET voided_quantity = quantity "
               "WHERE sale_id IN (SELECT id FROM walkin_sales WHERE voided_at IS NOT NULL)")
    op.execute("UPDATE walkin_sales SET voided_amount = total WHERE voided_at IS NOT NULL")
    op.execute("INSERT INTO walkin_voids (sale_id, items, amount, reason, by_id, created_at) "
               "SELECT id, 'Every item', total, void_reason, cashier_id, voided_at FROM walkin_sales "
               "WHERE voided_at IS NOT NULL")

    with op.batch_alter_table('walkin_sales', schema=None) as batch_op:
        batch_op.create_check_constraint(op.f('ck_walkin_sales_voided_in_range'), 'voided_amount >= 0 AND voided_amount <= total')
        batch_op.drop_column('void_reason')


def downgrade():
    with op.batch_alter_table('walkin_sales', schema=None) as batch_op:
        batch_op.add_column(sa.Column('void_reason', sa.String(length=200), server_default='', nullable=False))
        batch_op.drop_constraint(op.f('ck_walkin_sales_voided_in_range'), type_='check')
    op.execute("UPDATE walkin_sales SET void_reason = COALESCE((SELECT reason FROM walkin_voids "
               "WHERE walkin_voids.sale_id = walkin_sales.id ORDER BY id DESC LIMIT 1), '')")
    with op.batch_alter_table('walkin_sales', schema=None) as batch_op:
        batch_op.drop_column('voided_amount')

    with op.batch_alter_table('walkin_lines', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_walkin_lines_voided_in_range'), type_='check')
        batch_op.drop_column('voided_quantity')

    with op.batch_alter_table('walkin_voids', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_walkin_voids_sale_id'))

    op.drop_table('walkin_voids')
