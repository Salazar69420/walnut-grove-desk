"""creative studio: images and short videos for campaigns

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-01 02:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('creatives',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=6), nullable=False),
    sa.Column('format', sa.String(length=40), nullable=False),
    sa.Column('title', sa.String(length=160), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('messages_json', sa.Text(), nullable=False),
    sa.Column('qa_json', sa.Text(), nullable=False),
    sa.Column('question_json', sa.Text(), nullable=False),
    sa.Column('plan_json', sa.Text(), nullable=False),
    sa.Column('prompt', sa.Text(), nullable=False),
    sa.Column('caption', sa.Text(), nullable=False),
    sa.Column('sms_text', sa.String(length=320), nullable=False),
    sa.Column('options_json', sa.Text(), nullable=False),
    sa.Column('reference_url', sa.String(length=600), nullable=False),
    sa.Column('task_url', sa.String(length=600), nullable=False),
    sa.Column('output_url', sa.String(length=800), nullable=False),
    sa.Column('file_name', sa.String(length=120), nullable=False),
    sa.Column('public_token', sa.String(length=40), nullable=True),
    sa.Column('cost_usd', sa.Float(), nullable=False),
    sa.Column('llm_cost_usd', sa.Float(), nullable=False),
    sa.Column('error', sa.String(length=300), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('generated_at', sa.DateTime(), nullable=True),
    sa.Column('done_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("kind in ('image','video')", name=op.f('ck_creatives_kind')),
    sa.CheckConstraint("status in ('interview','review','generating','done','failed')", name=op.f('ck_creatives_status')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_creatives_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_creatives'))
    )
    op.create_index('ix_creatives_status', 'creatives', ['status'])
    op.create_index('uq_creatives_public_token', 'creatives', ['public_token'], unique=True)
    with op.batch_alter_table('campaigns') as b:
        b.add_column(sa.Column('creative_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('campaigns') as b:
        b.drop_column('creative_id')
    op.drop_table('creatives')
