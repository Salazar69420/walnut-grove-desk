"""Clinic CRM phases 1 and 2: contacts, pets, messages, notes, tasks, calendar, notifications, access log.

Also lets the outbox carry email (still only would_send).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-31 03:43:45.878239
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('access_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('contact_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=8), nullable=False),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=False),
    sa.Column('detail', sa.String(length=200), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("action in ('view','export','erase','send')", name=op.f('ck_access_log_action')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_access_log'))
    )
    with op.batch_alter_table('access_log', schema=None) as batch_op:
        batch_op.create_index('ix_access_log_at', ['at'], unique=False)
        batch_op.create_index('ix_access_log_contact_id', ['contact_id'], unique=False)

    op.create_table('contacts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('display_name', sa.String(length=120), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=True),
    sa.Column('email_consent', sa.String(length=14), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=True),
    sa.Column('sms_consent', sa.String(length=14), nullable=False),
    sa.Column('consent_source', sa.String(length=200), nullable=False),
    sa.Column('consent_at', sa.DateTime(), nullable=True),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.Column('owner_user_id', sa.Integer(), nullable=True),
    sa.Column('ai_enabled', sa.Boolean(), nullable=False),
    sa.Column('preference_notes', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_active_at', sa.DateTime(), nullable=False),
    sa.Column('last_read_at', sa.DateTime(), nullable=True),
    sa.Column('erased_at', sa.DateTime(), nullable=True),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("email_consent in ('full','transactional','none','unsubscribed')", name=op.f('ck_contacts_email_consent')),
    sa.CheckConstraint("kind in ('inquiry','patient')", name=op.f('ck_contacts_kind')),
    sa.CheckConstraint("sms_consent in ('full','transactional','none','unsubscribed')", name=op.f('ck_contacts_sms_consent')),
    sa.CheckConstraint("source in ('phone','walk_in','website','referral','after_hours','import','other')", name=op.f('ck_contacts_source')),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_contacts_owner_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_contacts'))
    )
    with op.batch_alter_table('contacts', schema=None) as batch_op:
        batch_op.create_index('ix_contacts_last_active_at', ['last_active_at'], unique=False)
        batch_op.create_index('ix_contacts_owner_user_id', ['owner_user_id'], unique=False)
        batch_op.create_index('uq_contacts_email', ['email'], unique=True)
        batch_op.create_index('uq_contacts_phone', ['phone'], unique=True)

    op.create_table('notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=14), nullable=False),
    sa.Column('text', sa.String(length=300), nullable=False),
    sa.Column('link', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('read_at', sa.DateTime(), nullable=True),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("kind in ('mention','task_assigned','reminder')", name=op.f('ck_notifications_kind')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notifications_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications'))
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index('ix_notifications_user_id_created_at', ['user_id', 'created_at'], unique=False)

    op.create_table('saved_views',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('screen', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('filters_json', sa.Text(), nullable=False),
    sa.Column('columns_json', sa.Text(), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_saved_views_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_saved_views'))
    )
    with op.batch_alter_table('saved_views', schema=None) as batch_op:
        batch_op.create_index('ix_saved_views_user_id_screen', ['user_id', 'screen'], unique=False)

    op.create_table('contact_links',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=False),
    sa.Column('related_contact_id', sa.Integer(), nullable=False),
    sa.Column('relationship', sa.String(length=12), nullable=False),
    sa.Column('note', sa.String(length=300), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("relationship in ('friend','spouse','partner','parent','child','neighbour','family','caregiver','other')", name=op.f('ck_contact_links_relationship')),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_contact_links_contact_id_contacts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['related_contact_id'], ['contacts.id'], name=op.f('fk_contact_links_related_contact_id_contacts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_contact_links'))
    )
    with op.batch_alter_table('contact_links', schema=None) as batch_op:
        batch_op.create_index('ix_contact_links_contact_id', ['contact_id'], unique=False)
        batch_op.create_index('ix_contact_links_related_contact_id', ['related_contact_id'], unique=False)

    op.create_table('contact_note_history',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=False),
    sa.Column('old_text', sa.Text(), nullable=False),
    sa.Column('new_text', sa.Text(), nullable=False),
    sa.Column('edited_by', sa.Integer(), nullable=True),
    sa.Column('edited_at', sa.DateTime(), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_contact_note_history_contact_id_contacts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['edited_by'], ['users.id'], name=op.f('fk_contact_note_history_edited_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_contact_note_history'))
    )
    with op.batch_alter_table('contact_note_history', schema=None) as batch_op:
        batch_op.create_index('ix_contact_note_history_contact_id', ['contact_id'], unique=False)

    op.create_table('messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=False),
    sa.Column('channel', sa.String(length=8), nullable=False),
    sa.Column('direction', sa.String(length=4), nullable=False),
    sa.Column('sender_kind', sa.String(length=8), nullable=False),
    sa.Column('sender_user_id', sa.Integer(), nullable=True),
    sa.Column('subject', sa.String(length=150), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('delivery_state', sa.String(length=20), nullable=False),
    sa.Column('scheduled_for', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("channel in ('sms','email','call')", name=op.f('ck_messages_channel')),
    sa.CheckConstraint("delivery_state in ('received','dry_run','scheduled_dry_run')", name=op.f('ck_messages_delivery_state')),
    sa.CheckConstraint("direction in ('in','out')", name=op.f('ck_messages_direction')),
    sa.CheckConstraint("sender_kind in ('client','staff','ai')", name=op.f('ck_messages_sender_kind')),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_messages_contact_id_contacts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['sender_user_id'], ['users.id'], name=op.f('fk_messages_sender_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_messages'))
    )
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.create_index('ix_messages_contact_id_created_at', ['contact_id', 'created_at'], unique=False)

    op.create_table('notes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=False),
    sa.Column('author_id', sa.Integer(), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_notes_author_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_notes_contact_id_contacts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notes'))
    )
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.create_index('ix_notes_contact_id_created_at', ['contact_id', 'created_at'], unique=False)

    op.create_table('pets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('species', sa.String(length=40), nullable=False),
    sa.Column('breed', sa.String(length=80), nullable=False),
    sa.Column('sex', sa.String(length=12), nullable=False),
    sa.Column('birth_date', sa.Date(), nullable=True),
    sa.Column('weight_kg', sa.Float(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("status in ('active','deceased','moved')", name=op.f('ck_pets_status')),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_pets_contact_id_contacts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pets'))
    )
    with op.batch_alter_table('pets', schema=None) as batch_op:
        batch_op.create_index('ix_pets_contact_id', ['contact_id'], unique=False)

    op.create_table('reminders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=True),
    sa.Column('due_at', sa.DateTime(), nullable=False),
    sa.Column('text', sa.String(length=200), nullable=False),
    sa.Column('done_at', sa.DateTime(), nullable=True),
    sa.Column('notified_at', sa.DateTime(), nullable=True),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_reminders_contact_id_contacts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_reminders_owner_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reminders'))
    )
    with op.batch_alter_table('reminders', schema=None) as batch_op:
        batch_op.create_index('ix_reminders_contact_id', ['contact_id'], unique=False)
        batch_op.create_index('ix_reminders_owner_id', ['owner_id'], unique=False)

    op.create_table('tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('classification', sa.String(length=10), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=True),
    sa.Column('conversation_id', sa.Integer(), nullable=True),
    sa.Column('assigned_to', sa.Integer(), nullable=True),
    sa.Column('priority', sa.String(length=8), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('due_at', sa.DateTime(), nullable=True),
    sa.Column('raised_by', sa.String(length=8), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('done_at', sa.DateTime(), nullable=True),
    sa.Column('done_by', sa.Integer(), nullable=True),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("classification in ('inquiry','patient')", name=op.f('ck_tasks_classification')),
    sa.CheckConstraint("kind in ('unknown_knowledge','escalation','callback','refill','booking','complaint','emergency_followup','results','other')", name=op.f('ck_tasks_kind')),
    sa.CheckConstraint("priority in ('high','normal','low')", name=op.f('ck_tasks_priority')),
    sa.CheckConstraint("raised_by in ('ai','human','rule')", name=op.f('ck_tasks_raised_by')),
    sa.CheckConstraint("status in ('open','in_progress','done')", name=op.f('ck_tasks_status')),
    sa.ForeignKeyConstraint(['assigned_to'], ['users.id'], name=op.f('fk_tasks_assigned_to_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_tasks_contact_id_contacts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_tasks_conversation_id_conversations'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['done_by'], ['users.id'], name=op.f('fk_tasks_done_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tasks'))
    )
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.create_index('ix_tasks_assigned_to', ['assigned_to'], unique=False)
        batch_op.create_index('ix_tasks_contact_id', ['contact_id'], unique=False)
        batch_op.create_index('ix_tasks_status', ['status'], unique=False)

    op.create_table('attachments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=True),
    sa.Column('note_id', sa.Integer(), nullable=True),
    sa.Column('filename', sa.String(length=160), nullable=False),
    sa.Column('mime', sa.String(length=60), nullable=False),
    sa.Column('bytes', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('stored_name', sa.String(length=64), nullable=False),
    sa.Column('uploaded_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_attachments_contact_id_contacts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], name=op.f('fk_attachments_message_id_messages'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['note_id'], ['notes.id'], name=op.f('fk_attachments_note_id_notes'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], name=op.f('fk_attachments_uploaded_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_attachments')),
    sa.UniqueConstraint('stored_name', name=op.f('uq_attachments_stored_name'))
    )
    with op.batch_alter_table('attachments', schema=None) as batch_op:
        batch_op.create_index('ix_attachments_contact_id', ['contact_id'], unique=False)
        batch_op.create_index('ix_attachments_message_id', ['message_id'], unique=False)
        batch_op.create_index('ix_attachments_note_id', ['note_id'], unique=False)

    op.create_table('calendar_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=14), nullable=False),
    sa.Column('classification', sa.String(length=10), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=True),
    sa.Column('pet_id', sa.Integer(), nullable=True),
    sa.Column('staff_user_id', sa.Integer(), nullable=True),
    sa.Column('starts_at', sa.DateTime(), nullable=False),
    sa.Column('ends_at', sa.DateTime(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('source', sa.String(length=8), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("classification in ('inquiry','patient','none')", name=op.f('ck_calendar_events_classification')),
    sa.CheckConstraint("kind in ('checkup','vaccine','surgery','dental','consult','inquiry_call','staff','reminder','other')", name=op.f('ck_calendar_events_kind')),
    sa.CheckConstraint("source in ('import','app')", name=op.f('ck_calendar_events_source')),
    sa.CheckConstraint("status in ('booked','requested','done','cancelled')", name=op.f('ck_calendar_events_status')),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_calendar_events_contact_id_contacts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['pet_id'], ['pets.id'], name=op.f('fk_calendar_events_pet_id_pets'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['staff_user_id'], ['users.id'], name=op.f('fk_calendar_events_staff_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_calendar_events'))
    )
    with op.batch_alter_table('calendar_events', schema=None) as batch_op:
        batch_op.create_index('ix_calendar_events_contact_id', ['contact_id'], unique=False)
        batch_op.create_index('ix_calendar_events_starts_at', ['starts_at'], unique=False)

    op.create_table('note_mentions',
    sa.Column('note_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['note_id'], ['notes.id'], name=op.f('fk_note_mentions_note_id_notes'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_note_mentions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('note_id', 'user_id', name=op.f('pk_note_mentions'))
    )
    op.create_table('visits',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=False),
    sa.Column('pet_id', sa.Integer(), nullable=True),
    sa.Column('visited_at', sa.DateTime(), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=8), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("kind in ('checkup','vaccine','surgery','dental','sick','emergency','other')", name=op.f('ck_visits_kind')),
    sa.CheckConstraint("source in ('import','app')", name=op.f('ck_visits_source')),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_visits_contact_id_contacts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['pet_id'], ['pets.id'], name=op.f('fk_visits_pet_id_pets'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_visits'))
    )
    with op.batch_alter_table('visits', schema=None) as batch_op:
        batch_op.create_index('ix_visits_contact_id', ['contact_id'], unique=False)
        batch_op.create_index('ix_visits_pet_id', ['pet_id'], unique=False)
        batch_op.create_index('ix_visits_visited_at', ['visited_at'], unique=False)

    with op.batch_alter_table('outbox', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_outbox_channel'), type_='check')
        batch_op.create_check_constraint(op.f('ck_outbox_channel'), "channel in ('sms','voice','email')")




def downgrade() -> None:
    op.execute("DELETE FROM outbox WHERE channel = 'email'")
    with op.batch_alter_table('outbox', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_outbox_channel'), type_='check')
        batch_op.create_check_constraint(op.f('ck_outbox_channel'), "channel in ('sms','voice')")

    with op.batch_alter_table('visits', schema=None) as batch_op:
        batch_op.drop_index('ix_visits_visited_at')
        batch_op.drop_index('ix_visits_pet_id')
        batch_op.drop_index('ix_visits_contact_id')

    op.drop_table('visits')
    op.drop_table('note_mentions')
    with op.batch_alter_table('calendar_events', schema=None) as batch_op:
        batch_op.drop_index('ix_calendar_events_starts_at')
        batch_op.drop_index('ix_calendar_events_contact_id')

    op.drop_table('calendar_events')
    with op.batch_alter_table('attachments', schema=None) as batch_op:
        batch_op.drop_index('ix_attachments_note_id')
        batch_op.drop_index('ix_attachments_message_id')
        batch_op.drop_index('ix_attachments_contact_id')

    op.drop_table('attachments')
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_index('ix_tasks_status')
        batch_op.drop_index('ix_tasks_contact_id')
        batch_op.drop_index('ix_tasks_assigned_to')

    op.drop_table('tasks')
    with op.batch_alter_table('reminders', schema=None) as batch_op:
        batch_op.drop_index('ix_reminders_owner_id')
        batch_op.drop_index('ix_reminders_contact_id')

    op.drop_table('reminders')
    with op.batch_alter_table('pets', schema=None) as batch_op:
        batch_op.drop_index('ix_pets_contact_id')

    op.drop_table('pets')
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.drop_index('ix_notes_contact_id_created_at')

    op.drop_table('notes')
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index('ix_messages_contact_id_created_at')

    op.drop_table('messages')
    with op.batch_alter_table('contact_note_history', schema=None) as batch_op:
        batch_op.drop_index('ix_contact_note_history_contact_id')

    op.drop_table('contact_note_history')
    with op.batch_alter_table('contact_links', schema=None) as batch_op:
        batch_op.drop_index('ix_contact_links_related_contact_id')
        batch_op.drop_index('ix_contact_links_contact_id')

    op.drop_table('contact_links')
    with op.batch_alter_table('saved_views', schema=None) as batch_op:
        batch_op.drop_index('ix_saved_views_user_id_screen')

    op.drop_table('saved_views')
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index('ix_notifications_user_id_created_at')

    op.drop_table('notifications')
    with op.batch_alter_table('contacts', schema=None) as batch_op:
        batch_op.drop_index('uq_contacts_phone')
        batch_op.drop_index('uq_contacts_email')
        batch_op.drop_index('ix_contacts_owner_user_id')
        batch_op.drop_index('ix_contacts_last_active_at')

    op.drop_table('contacts')
    with op.batch_alter_table('access_log', schema=None) as batch_op:
        batch_op.drop_index('ix_access_log_contact_id')
        batch_op.drop_index('ix_access_log_at')

    op.drop_table('access_log')
