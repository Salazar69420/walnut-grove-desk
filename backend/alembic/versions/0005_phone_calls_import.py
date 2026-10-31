"""Phone calls: allow imported real calls (source import, direction unknown) and a de-duplication key.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('phone_calls', schema=None) as batch_op:
        batch_op.add_column(sa.Column('external_ref', sa.String(length=40), nullable=True))
        batch_op.create_index('uq_phone_calls_external_ref', ['external_ref'], unique=True)
        batch_op.drop_constraint(op.f('ck_phone_calls_direction'), type_='check')
        batch_op.drop_constraint(op.f('ck_phone_calls_source'), type_='check')
        batch_op.create_check_constraint(op.f('ck_phone_calls_direction'), "direction in ('inbound','outbound','unknown')")
        batch_op.create_check_constraint(op.f('ck_phone_calls_source'), "source in ('demo','telus','retell','import')")


def downgrade() -> None:
    with op.batch_alter_table('phone_calls', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_phone_calls_source'), type_='check')
        batch_op.drop_constraint(op.f('ck_phone_calls_direction'), type_='check')
        batch_op.create_check_constraint(op.f('ck_phone_calls_direction'), "direction in ('inbound','outbound')")
        batch_op.create_check_constraint(op.f('ck_phone_calls_source'), "source in ('demo','telus','retell')")
        batch_op.drop_index('uq_phone_calls_external_ref')
        batch_op.drop_column('external_ref')
