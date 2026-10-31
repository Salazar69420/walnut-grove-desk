"""call transcripts on demand, AI call summaries and intents, assistant memory per contact

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-31 11:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('phone_calls') as b:
        b.add_column(sa.Column('summary', sa.Text(), nullable=True))
        b.add_column(sa.Column('intent', sa.String(length=30), nullable=True))
        b.add_column(sa.Column('transcript_state', sa.String(length=10), nullable=False, server_default=''))
        b.add_column(sa.Column('transcript_tries', sa.Integer(), nullable=False, server_default='0'))
    with op.batch_alter_table('contacts') as b:
        b.add_column(sa.Column('ai_memory', sa.Text(), nullable=False, server_default=''))
        b.add_column(sa.Column('ai_memory_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('contacts') as b:
        b.drop_column('ai_memory_at')
        b.drop_column('ai_memory')
    with op.batch_alter_table('phone_calls') as b:
        b.drop_column('transcript_tries')
        b.drop_column('transcript_state')
        b.drop_column('intent')
        b.drop_column('summary')
