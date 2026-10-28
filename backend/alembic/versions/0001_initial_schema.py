"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-28 02:24:43.444470
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('call_analytics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=40), nullable=False),
    sa.Column('metric', sa.String(length=60), nullable=False),
    sa.Column('dim1', sa.String(length=60), nullable=False),
    sa.Column('dim2', sa.String(length=60), nullable=False),
    sa.Column('value', sa.Float(), nullable=False),
    sa.Column('n', sa.Integer(), nullable=False),
    sa.Column('loaded_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_call_analytics'))
    )
    with op.batch_alter_table('call_analytics', schema=None) as batch_op:
        batch_op.create_index('ix_call_analytics_metric', ['metric'], unique=False)

    op.create_table('call_log_demo',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.Column('weekday', sa.Integer(), nullable=False),
    sa.Column('hour', sa.Integer(), nullable=False),
    sa.Column('answered', sa.Boolean(), nullable=False),
    sa.Column('rang_again_within_hour', sa.Boolean(), nullable=False),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_call_log_demo'))
    )
    op.create_table('events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.Column('tool', sa.String(length=40), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('actor_name', sa.String(length=120), nullable=False),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('entity_type', sa.String(length=40), nullable=False),
    sa.Column('entity_id', sa.String(length=40), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('data_json', sa.Text(), nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_events'))
    )
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.create_index('ix_events_entity', ['entity_type', 'entity_id'], unique=False)
        batch_op.create_index('ix_events_tool', ['tool'], unique=False)
        batch_op.create_index('ix_events_ts', ['ts'], unique=False)

    op.create_table('hospitals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('place', sa.String(length=120), nullable=False),
    sa.Column('phone', sa.String(length=40), nullable=False),
    sa.Column('address', sa.String(length=300), nullable=False),
    sa.Column('hours_claim', sa.String(length=300), nullable=False),
    sa.Column('confirmed', sa.Boolean(), nullable=False),
    sa.Column('confirmed_by', sa.String(length=120), nullable=True),
    sa.Column('is_placeholder', sa.Boolean(), nullable=False),
    sa.Column('sort', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_hospitals'))
    )
    op.create_table('imported_calls',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('call_id', sa.String(length=80), nullable=False),
    sa.Column('timestamp', sa.DateTime(), nullable=True),
    sa.Column('caller_type', sa.String(length=40), nullable=False),
    sa.Column('intent', sa.String(length=60), nullable=False),
    sa.Column('outcome', sa.String(length=60), nullable=False),
    sa.Column('urgency', sa.String(length=20), nullable=False),
    sa.Column('red_flags', sa.String(length=300), nullable=False),
    sa.Column('promised_followup', sa.Boolean(), nullable=False),
    sa.Column('contains_private_content', sa.Boolean(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=40), nullable=False),
    sa.Column('imported_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_imported_calls')),
    sa.UniqueConstraint('call_id', name=op.f('uq_imported_calls_call_id'))
    )
    op.create_table('lab_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('pet_label', sa.String(length=120), nullable=False),
    sa.Column('owner_label', sa.String(length=120), nullable=False),
    sa.Column('test', sa.String(length=120), nullable=False),
    sa.Column('received_at', sa.DateTime(), nullable=False),
    sa.Column('stage', sa.Integer(), nullable=False),
    sa.Column('reviewed_by', sa.String(length=120), nullable=True),
    sa.Column('told_by', sa.String(length=120), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('stage between 0 and 2', name=op.f('ck_lab_results_stage')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_lab_results'))
    )
    op.create_table('login_attempts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=False),
    sa.Column('username', sa.String(length=120), nullable=False),
    sa.Column('success', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_login_attempts'))
    )
    with op.batch_alter_table('login_attempts', schema=None) as batch_op:
        batch_op.create_index('ix_login_attempts_ip_ts', ['ip', 'ts'], unique=False)
        batch_op.create_index('ix_login_attempts_username_ts', ['username', 'ts'], unique=False)

    op.create_table('outbox',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('channel', sa.String(length=8), nullable=False),
    sa.Column('to_label', sa.String(length=160), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('source_type', sa.String(length=40), nullable=False),
    sa.Column('source_id', sa.String(length=40), nullable=False),
    sa.Column('created_by', sa.String(length=120), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("channel in ('sms','voice')", name=op.f('ck_outbox_channel')),
    sa.CheckConstraint("status in ('would_send')", name=op.f('ck_outbox_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outbox'))
    )
    op.create_table('pims_appointments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=60), nullable=False),
    sa.Column('start', sa.DateTime(), nullable=False),
    sa.Column('client_external_id', sa.String(length=60), nullable=False),
    sa.Column('patient_external_id', sa.String(length=60), nullable=False),
    sa.Column('provider', sa.String(length=120), nullable=False),
    sa.Column('visit_type', sa.String(length=120), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('synced_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pims_appointments')),
    sa.UniqueConstraint('external_id', name=op.f('uq_pims_appointments_external_id'))
    )
    op.create_table('pims_clients',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=60), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('last_visit', sa.DateTime(), nullable=True),
    sa.Column('recall_overdue', sa.Boolean(), nullable=False),
    sa.Column('synced_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pims_clients')),
    sa.UniqueConstraint('external_id', name=op.f('uq_pims_clients_external_id'))
    )
    op.create_table('pims_lab_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=60), nullable=False),
    sa.Column('patient_external_id', sa.String(length=60), nullable=False),
    sa.Column('test', sa.String(length=120), nullable=False),
    sa.Column('resulted_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('synced_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pims_lab_results')),
    sa.UniqueConstraint('external_id', name=op.f('uq_pims_lab_results_external_id'))
    )
    op.create_table('pims_patients',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=60), nullable=False),
    sa.Column('client_external_id', sa.String(length=60), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('species', sa.String(length=40), nullable=False),
    sa.Column('breed', sa.String(length=80), nullable=False),
    sa.Column('last_exam', sa.DateTime(), nullable=True),
    sa.Column('synced_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pims_patients')),
    sa.UniqueConstraint('external_id', name=op.f('uq_pims_patients_external_id'))
    )
    op.create_table('settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value_json', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('updated_by', sa.String(length=120), nullable=False),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_settings'))
    )
    op.create_table('suggestions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('rule_key', sa.String(length=40), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('why', sa.Text(), nullable=False),
    sa.Column('evidence_json', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('decided_by', sa.String(length=120), nullable=True),
    sa.Column('decided_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("status in ('open','approved','dismissed')", name=op.f('ck_suggestions_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_suggestions'))
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=True),
    sa.Column('display_name', sa.String(length=120), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('initials', sa.String(length=4), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('is_admin', sa.Boolean(), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('must_change_password', sa.Boolean(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('failed_count', sa.Integer(), nullable=False),
    sa.Column('locked_until', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_login_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("role in ('owner','desk','nurse')", name=op.f('ck_users_role')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('username', name=op.f('uq_users_username'))
    )
    op.create_table('visit_types',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('minutes', sa.Integer(), nullable=False),
    sa.Column('bookable', sa.Boolean(), nullable=False),
    sa.Column('sort', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_visit_types'))
    )
    op.create_table('conversations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('channel', sa.String(length=8), nullable=False),
    sa.Column('client_label', sa.String(length=120), nullable=False),
    sa.Column('pet_label', sa.String(length=120), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('preferred_slot', sa.String(length=80), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('messages_json', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.Column('is_test', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("channel in ('text','call')", name=op.f('ck_conversations_channel')),
    sa.CheckConstraint("kind in ('booking','emergency','callback')", name=op.f('ck_conversations_kind')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_conversations_created_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversations'))
    )
    op.create_table('drafts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=80), nullable=False),
    sa.Column('to_label', sa.String(length=160), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('channel', sa.String(length=8), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('created_by_tool', sa.String(length=40), nullable=False),
    sa.Column('edited_by', sa.String(length=120), nullable=True),
    sa.Column('approved_by', sa.String(length=120), nullable=True),
    sa.Column('approved_at', sa.DateTime(), nullable=True),
    sa.Column('suggestion_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("status in ('pending','sent')", name=op.f('ck_drafts_status')),
    sa.ForeignKeyConstraint(['suggestion_id'], ['suggestions.id'], name=op.f('fk_drafts_suggestion_id_suggestions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_drafts'))
    )
    op.create_table('open_slots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('start', sa.DateTime(), nullable=False),
    sa.Column('visit_type_id', sa.Integer(), nullable=True),
    sa.Column('provider', sa.String(length=120), nullable=False),
    sa.Column('taken', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['visit_type_id'], ['visit_types.id'], name=op.f('fk_open_slots_visit_type_id_visit_types')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_open_slots'))
    )
    op.create_table('sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('csrf_token', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=False),
    sa.Column('user_agent', sa.String(length=300), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sessions')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_sessions_token_hash'))
    )
    with op.batch_alter_table('sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sessions_user_id'), ['user_id'], unique=False)

    op.create_table('shifts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('weekday', sa.Integer(), nullable=False),
    sa.Column('person_label', sa.String(length=120), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('role', sa.String(length=8), nullable=False),
    sa.Column('start_min', sa.Integer(), nullable=False),
    sa.Column('end_min', sa.Integer(), nullable=False),
    sa.Column('proposed', sa.Boolean(), nullable=False),
    sa.Column('suggestion_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("role in ('desk','nurse','doc','open')", name=op.f('ck_shifts_role')),
    sa.CheckConstraint('weekday between 0 and 6', name=op.f('ck_shifts_weekday')),
    sa.ForeignKeyConstraint(['suggestion_id'], ['suggestions.id'], name=op.f('fk_shifts_suggestion_id_suggestions')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_shifts_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shifts'))
    )
    op.create_table('appointment_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=False),
    sa.Column('visit_type_id', sa.Integer(), nullable=True),
    sa.Column('slot_start', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("status in ('requested','confirmed','entered','called')", name=op.f('ck_appointment_requests_status')),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_appointment_requests_conversation_id_conversations')),
    sa.ForeignKeyConstraint(['visit_type_id'], ['visit_types.id'], name=op.f('fk_appointment_requests_visit_type_id_visit_types')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_appointment_requests'))
    )
    with op.batch_alter_table('appointment_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_appointment_requests_conversation_id'), ['conversation_id'], unique=False)

    op.create_table('callbacks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_label', sa.String(length=120), nullable=False),
    sa.Column('pet_label', sa.String(length=120), nullable=False),
    sa.Column('what', sa.Text(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('via', sa.String(length=20), nullable=False),
    sa.Column('received_at', sa.DateTime(), nullable=False),
    sa.Column('priority', sa.String(length=8), nullable=False),
    sa.Column('red_flag', sa.Boolean(), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=True),
    sa.Column('owner_name', sa.String(length=120), nullable=True),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=True),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("priority in ('high','normal')", name=op.f('ck_callbacks_priority')),
    sa.CheckConstraint("status in ('new','claimed','done')", name=op.f('ck_callbacks_status')),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_callbacks_conversation_id_conversations')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_callbacks_owner_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_callbacks'))
    )
    with op.batch_alter_table('callbacks', schema=None) as batch_op:
        batch_op.create_index('ix_callbacks_status', ['status'], unique=False)

    op.create_table('emergency_routings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=False),
    sa.Column('hospital_id', sa.Integer(), nullable=True),
    sa.Column('matched_terms', sa.Text(), nullable=False),
    sa.Column('layer', sa.String(length=8), nullable=False),
    sa.Column('followed_up_by', sa.String(length=120), nullable=True),
    sa.Column('followed_up_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_emergency_routings_conversation_id_conversations')),
    sa.ForeignKeyConstraint(['hospital_id'], ['hospitals.id'], name=op.f('fk_emergency_routings_hospital_id_hospitals')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_emergency_routings'))
    )
    with op.batch_alter_table('emergency_routings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_emergency_routings_conversation_id'), ['conversation_id'], unique=False)

    op.create_table('sim_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('channel', sa.String(length=8), nullable=False),
    sa.Column('node', sa.String(length=40), nullable=False),
    sa.Column('picks_json', sa.Text(), nullable=False),
    sa.Column('messages_json', sa.Text(), nullable=False),
    sa.Column('done', sa.Boolean(), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_sim_sessions_conversation_id_conversations')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sim_sessions_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sim_sessions'))
    )
    with op.batch_alter_table('sim_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sim_sessions_user_id'), ['user_id'], unique=False)


    # The shared record is append-only: nothing may change or remove an event.
    op.execute(
        "CREATE TRIGGER events_no_update BEFORE UPDATE ON events "
        "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER events_no_delete BEFORE DELETE ON events "
        "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END"
    )
    # Every UI query and metric reads imported calls through this view only.
    op.execute("CREATE VIEW visible_calls AS SELECT * FROM imported_calls WHERE contains_private_content = 0")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS visible_calls")
    op.execute("DROP TRIGGER IF EXISTS events_no_update")
    op.execute("DROP TRIGGER IF EXISTS events_no_delete")
    with op.batch_alter_table('sim_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sim_sessions_user_id'))

    op.drop_table('sim_sessions')
    with op.batch_alter_table('emergency_routings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_emergency_routings_conversation_id'))

    op.drop_table('emergency_routings')
    with op.batch_alter_table('callbacks', schema=None) as batch_op:
        batch_op.drop_index('ix_callbacks_status')

    op.drop_table('callbacks')
    with op.batch_alter_table('appointment_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_appointment_requests_conversation_id'))

    op.drop_table('appointment_requests')
    op.drop_table('shifts')
    with op.batch_alter_table('sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sessions_user_id'))

    op.drop_table('sessions')
    op.drop_table('open_slots')
    op.drop_table('drafts')
    op.drop_table('conversations')
    op.drop_table('visit_types')
    op.drop_table('users')
    op.drop_table('suggestions')
    op.drop_table('settings')
    op.drop_table('pims_patients')
    op.drop_table('pims_lab_results')
    op.drop_table('pims_clients')
    op.drop_table('pims_appointments')
    op.drop_table('outbox')
    with op.batch_alter_table('login_attempts', schema=None) as batch_op:
        batch_op.drop_index('ix_login_attempts_username_ts')
        batch_op.drop_index('ix_login_attempts_ip_ts')

    op.drop_table('login_attempts')
    op.drop_table('lab_results')
    op.drop_table('imported_calls')
    op.drop_table('hospitals')
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.drop_index('ix_events_ts')
        batch_op.drop_index('ix_events_tool')
        batch_op.drop_index('ix_events_entity')

    op.drop_table('events')
    op.drop_table('call_log_demo')
    with op.batch_alter_table('call_analytics', schema=None) as batch_op:
        batch_op.drop_index('ix_call_analytics_metric')

    op.drop_table('call_analytics')
