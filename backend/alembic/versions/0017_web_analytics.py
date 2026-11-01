"""website traffic: a daily copy of Vercel Web Analytics totals

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("web_analytics_days",
                    sa.Column("day", sa.String(length=10), nullable=False),
                    sa.Column("pageviews", sa.Integer(), nullable=False),
                    sa.Column("visitors", sa.Integer(), nullable=False),
                    sa.Column("updated_at", sa.DateTime(), nullable=False),
                    sa.PrimaryKeyConstraint("day", name=op.f("pk_web_analytics_days")))


def downgrade() -> None:
    op.drop_table("web_analytics_days")
