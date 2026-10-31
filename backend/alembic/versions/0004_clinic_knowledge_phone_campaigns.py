"""Knowledge and fees, phone call log, campaigns (plan phases 4, 5 and 7).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31 04:41:20.794237
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('campaigns',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('channel', sa.String(length=5), nullable=False),
    sa.Column('purpose', sa.String(length=5), nullable=False),
    sa.Column('status', sa.String(length=18), nullable=False),
    sa.Column('segment_json', sa.Text(), nullable=False),
    sa.Column('subject', sa.String(length=150), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('approved_by', sa.Integer(), nullable=True),
    sa.Column('approved_at', sa.DateTime(), nullable=True),
    sa.Column('ran_at', sa.DateTime(), nullable=True),
    sa.Column('counts_json', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("channel in ('sms','email')", name=op.f('ck_campaigns_channel')),
    sa.CheckConstraint("purpose in ('care','promo')", name=op.f('ck_campaigns_purpose')),
    sa.CheckConstraint("status in ('draft','awaiting_approval','approved','done_dry_run','cancelled')", name=op.f('ck_campaigns_status')),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], name=op.f('fk_campaigns_approved_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_campaigns_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_campaigns'))
    )
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.create_index('ix_campaigns_status', ['status'], unique=False)

    op.create_table('fees',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('service', sa.String(length=120), nullable=False),
    sa.Column('price_cents', sa.Integer(), nullable=True),
    sa.Column('price_prefix', sa.String(length=12), nullable=False),
    sa.Column('unit', sa.String(length=40), nullable=False),
    sa.Column('note', sa.String(length=300), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=True),
    sa.Column('effective_to', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('proposed_by', sa.String(length=6), nullable=False),
    sa.Column('decided_by', sa.Integer(), nullable=True),
    sa.Column('decided_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("status in ('pending','approved','retired')", name=op.f('ck_fees_status')),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_fees_decided_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fees'))
    )
    with op.batch_alter_table('fees', schema=None) as batch_op:
        batch_op.create_index('ix_fees_status', ['status'], unique=False)

    op.create_table('knowledge_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('category', sa.String(length=24), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('proposed_by', sa.String(length=6), nullable=False),
    sa.Column('source_kind', sa.String(length=14), nullable=False),
    sa.Column('source_ref', sa.String(length=80), nullable=False),
    sa.Column('source_quote', sa.String(length=300), nullable=False),
    sa.Column('confidence', sa.Integer(), nullable=True),
    sa.Column('supersedes_id', sa.Integer(), nullable=True),
    sa.Column('previous_body', sa.Text(), nullable=False),
    sa.Column('conflicts', sa.Boolean(), nullable=False),
    sa.Column('proposed_user_id', sa.Integer(), nullable=True),
    sa.Column('proposed_at', sa.DateTime(), nullable=False),
    sa.Column('decided_by', sa.Integer(), nullable=True),
    sa.Column('decided_at', sa.DateTime(), nullable=True),
    sa.Column('decline_reason', sa.String(length=200), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("category in ('clinic_info','fees','patient_knowledge','patient_communication','inquiry_communication','laws_regulations','specials')", name=op.f('ck_knowledge_entries_category')),
    sa.CheckConstraint("proposed_by in ('human','ai','import')", name=op.f('ck_knowledge_entries_proposed_by')),
    sa.CheckConstraint("source_kind in ('manual','conversation','call','answer_sheet')", name=op.f('ck_knowledge_entries_source_kind')),
    sa.CheckConstraint("status in ('pending','approved','declined','retired')", name=op.f('ck_knowledge_entries_status')),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_knowledge_entries_decided_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['proposed_user_id'], ['users.id'], name=op.f('fk_knowledge_entries_proposed_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['supersedes_id'], ['knowledge_entries.id'], name=op.f('fk_knowledge_entries_supersedes_id_knowledge_entries'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_entries'))
    )
    with op.batch_alter_table('knowledge_entries', schema=None) as batch_op:
        batch_op.create_index('ix_knowledge_entries_category', ['category'], unique=False)
        batch_op.create_index('ix_knowledge_entries_status', ['status'], unique=False)

    op.create_table('campaign_recipients',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('campaign_id', sa.Integer(), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(length=12), nullable=False),
    sa.Column('skipped_reason', sa.String(length=24), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=True),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("state in ('dry_run_sent','skipped')", name=op.f('ck_campaign_recipients_state')),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], name=op.f('fk_campaign_recipients_campaign_id_campaigns'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_campaign_recipients_contact_id_contacts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_campaign_recipients'))
    )
    with op.batch_alter_table('campaign_recipients', schema=None) as batch_op:
        batch_op.create_index('ix_campaign_recipients_campaign_id', ['campaign_id'], unique=False)
        batch_op.create_index('ix_campaign_recipients_contact_id', ['contact_id'], unique=False)

    op.create_table('phone_calls',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('direction', sa.String(length=8), nullable=False),
    sa.Column('handled_by', sa.String(length=6), nullable=False),
    sa.Column('agent_label', sa.String(length=80), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=True),
    sa.Column('phone', sa.String(length=20), nullable=True),
    sa.Column('duration_seconds', sa.Integer(), nullable=False),
    sa.Column('result', sa.String(length=18), nullable=False),
    sa.Column('call_type', sa.String(length=8), nullable=False),
    sa.Column('short_note', sa.String(length=240), nullable=False),
    sa.Column('transcript', sa.Text(), nullable=True),
    sa.Column('source', sa.String(length=8), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("call_type in ('inquiry','patient','unknown')", name=op.f('ck_phone_calls_call_type')),
    sa.CheckConstraint("direction in ('inbound','outbound')", name=op.f('ck_phone_calls_direction')),
    sa.CheckConstraint("handled_by in ('human','ai','none')", name=op.f('ck_phone_calls_handled_by')),
    sa.CheckConstraint("result in ('answered','booked','callback_task','voicemail','missed','emergency_routed','info_given')", name=op.f('ck_phone_calls_result')),
    sa.CheckConstraint("source in ('demo','telus','retell')", name=op.f('ck_phone_calls_source')),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_phone_calls_contact_id_contacts'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_phone_calls'))
    )
    with op.batch_alter_table('phone_calls', schema=None) as batch_op:
        batch_op.create_index('ix_phone_calls_contact_id', ['contact_id'], unique=False)
        batch_op.create_index('ix_phone_calls_started_at', ['started_at'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('phone_calls', schema=None) as batch_op:
        batch_op.drop_index('ix_phone_calls_started_at')
        batch_op.drop_index('ix_phone_calls_contact_id')

    op.drop_table('phone_calls')
    with op.batch_alter_table('campaign_recipients', schema=None) as batch_op:
        batch_op.drop_index('ix_campaign_recipients_contact_id')
        batch_op.drop_index('ix_campaign_recipients_campaign_id')

    op.drop_table('campaign_recipients')
    with op.batch_alter_table('knowledge_entries', schema=None) as batch_op:
        batch_op.drop_index('ix_knowledge_entries_status')
        batch_op.drop_index('ix_knowledge_entries_category')

    op.drop_table('knowledge_entries')
    with op.batch_alter_table('fees', schema=None) as batch_op:
        batch_op.drop_index('ix_fees_status')

    op.drop_table('fees')
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.drop_index('ix_campaigns_status')

    op.drop_table('campaigns')
