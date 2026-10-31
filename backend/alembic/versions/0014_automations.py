"""automations

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-31 21:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('automations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('trigger_type', sa.String(length=10), nullable=False),
    sa.Column('trigger_json', sa.Text(), nullable=False),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('action_json', sa.Text(), nullable=False),
    sa.Column('template', sa.String(length=40), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_run_at', sa.DateTime(), nullable=True),
    sa.Column('last_ok', sa.Boolean(), nullable=True),
    sa.Column('last_result', sa.String(length=300), nullable=False),
    sa.Column('run_count', sa.Integer(), nullable=False),
    sa.CheckConstraint("trigger_type in ('schedule','event')", name=op.f('ck_automations_trigger_type')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_automations_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_automations'))
    )
    op.create_table('automation_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('automation_id', sa.Integer(), nullable=False),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('ok', sa.Boolean(), nullable=False),
    sa.Column('detail', sa.String(length=500), nullable=False),
    sa.ForeignKeyConstraint(['automation_id'], ['automations.id'], name=op.f('fk_automation_runs_automation_id_automations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_automation_runs'))
    )
    op.create_index('ix_automation_runs_automation_id', 'automation_runs', ['automation_id'])


def downgrade() -> None:
    op.drop_table('automation_runs')
    op.drop_table('automations')
