"""website changes ship through Vercel: allow the 'deploying' status (production build running)

Revision ID: 0016
Revises: 0015
"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

OLD = "status in ('planning','building','ready','merged','failed','rejected')"
NEW = "status in ('planning','building','ready','deploying','merged','failed','rejected')"


def upgrade() -> None:
    with op.batch_alter_table("website_changes") as b:
        b.drop_constraint("status", type_="check")
        b.create_check_constraint("status", NEW)


def downgrade() -> None:
    op.execute("UPDATE website_changes SET status='building' WHERE status='deploying'")
    with op.batch_alter_table("website_changes") as b:
        b.drop_constraint("status", type_="check")
        b.create_check_constraint("status", OLD)
