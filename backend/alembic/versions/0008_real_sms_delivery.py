"""Real texting: message delivery states queued/sent/failed, the sender's job id and failure code, outbox states.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('send_job_id', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('delivery_code', sa.String(length=24), nullable=True))
        batch_op.drop_constraint(op.f('ck_messages_delivery_state'), type_='check')
        batch_op.create_check_constraint(op.f('ck_messages_delivery_state'), "delivery_state in ('received','dry_run','scheduled_dry_run','queued','sent','failed')")
    with op.batch_alter_table('outbox', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_outbox_status'), type_='check')
        batch_op.create_check_constraint(op.f('ck_outbox_status'), "status in ('would_send','queued','sent','failed')")


def downgrade() -> None:
    with op.batch_alter_table('outbox', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_outbox_status'), type_='check')
        batch_op.create_check_constraint(op.f('ck_outbox_status'), "status in ('would_send')")
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_messages_delivery_state'), type_='check')
        batch_op.create_check_constraint(op.f('ck_messages_delivery_state'), "delivery_state in ('received','dry_run','scheduled_dry_run')")
        batch_op.drop_column('delivery_code')
        batch_op.drop_column('send_job_id')
