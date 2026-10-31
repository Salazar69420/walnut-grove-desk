"""Messages: link a call line in a contact's timeline to its phone call (for transcript and recording buttons).

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('phone_call_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_messages_phone_call_id', ['phone_call_id'], unique=False)
    # Existing call lines were written with the call's start time, so the pair (contact, time) finds the call.
    op.execute(
        "UPDATE messages SET phone_call_id = (SELECT p.id FROM phone_calls p WHERE p.contact_id = messages.contact_id "
        "AND p.started_at = messages.created_at LIMIT 1) WHERE channel = 'call' AND phone_call_id IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index('ix_messages_phone_call_id')
        batch_op.drop_column('phone_call_id')
