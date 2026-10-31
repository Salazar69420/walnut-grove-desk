"""website audits (the clinic website's pages and SEO issues)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-31 14:30:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('website_audits',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('base_url', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('started_by', sa.String(length=120), nullable=False),
    sa.Column('pages_json', sa.Text(), nullable=False),
    sa.Column('issues_json', sa.Text(), nullable=False),
    sa.Column('summary_json', sa.Text(), nullable=False),
    sa.Column('ai_plan', sa.Text(), nullable=False),
    sa.Column('error', sa.String(length=200), nullable=False),
    sa.CheckConstraint("status in ('running','done','failed')", name=op.f('ck_website_audits_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_website_audits'))
    )


def downgrade() -> None:
    op.drop_table('website_audits')
