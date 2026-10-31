"""Phone calls: the TELUS recording id, so an owner can play the audio.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('phone_calls', schema=None) as batch_op:
        batch_op.add_column(sa.Column('recording_id', sa.String(length=24), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('phone_calls', schema=None) as batch_op:
        batch_op.drop_column('recording_id')
