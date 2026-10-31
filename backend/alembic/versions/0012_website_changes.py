"""website changes made by the deployment agent

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-31 19:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('website_changes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('audit_id', sa.Integer(), nullable=True),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('created_by', sa.String(length=120), nullable=False),
    sa.Column('branch', sa.String(length=120), nullable=False),
    sa.Column('head_sha', sa.String(length=64), nullable=False),
    sa.Column('pr_number', sa.Integer(), nullable=True),
    sa.Column('pr_url', sa.String(length=300), nullable=False),
    sa.Column('preview_url', sa.String(length=300), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('diff', sa.Text(), nullable=False),
    sa.Column('issue_codes', sa.String(length=600), nullable=False),
    sa.Column('error', sa.String(length=300), nullable=False),
    sa.Column('merged_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("status in ('planning','building','ready','merged','failed','rejected')", name=op.f('ck_website_changes_status')),
    sa.CheckConstraint("mode in ('auto','approve')", name=op.f('ck_website_changes_mode')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_website_changes'))
    )


def downgrade() -> None:
    op.drop_table('website_changes')
