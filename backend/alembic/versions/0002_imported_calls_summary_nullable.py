"""imported_calls.summary may be NULL (private rows keep no summary)

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS visible_calls")
    with op.batch_alter_table("imported_calls", schema=None) as batch_op:
        batch_op.alter_column("summary", existing_type=sa.Text(), nullable=True)
    op.execute("CREATE VIEW visible_calls AS SELECT * FROM imported_calls WHERE contains_private_content = 0")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS visible_calls")
    op.execute("UPDATE imported_calls SET summary = '' WHERE summary IS NULL")
    with op.batch_alter_table("imported_calls", schema=None) as batch_op:
        batch_op.alter_column("summary", existing_type=sa.Text(), nullable=False)
    op.execute("CREATE VIEW visible_calls AS SELECT * FROM imported_calls WHERE contains_private_content = 0")
