"""sms booking offers and products

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-31 08:20:58.691307
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('products',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('price_cents', sa.Integer(), nullable=True),
    sa.Column('unit', sa.String(length=40), nullable=False),
    sa.Column('pitch', sa.String(length=300), nullable=False),
    sa.Column('triggers', sa.String(length=400), nullable=False),
    sa.Column('species', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("status in ('pending','approved','retired')", name=op.f('ck_products_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_products'))
    )
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.create_index('ix_products_status', ['status'], unique=False)

    op.create_table('sms_offers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=False),
    sa.Column('visit_type_id', sa.Integer(), nullable=True),
    sa.Column('slots_json', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.CheckConstraint("status in ('offered','requested','expired','declined')", name=op.f('ck_sms_offers_status')),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_sms_offers_contact_id_contacts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['visit_type_id'], ['visit_types.id'], name=op.f('fk_sms_offers_visit_type_id_visit_types'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sms_offers'))
    )
    with op.batch_alter_table('sms_offers', schema=None) as batch_op:
        batch_op.create_index('ix_sms_offers_contact_id', ['contact_id'], unique=False)

    op.create_table('upsell_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('contact_id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=True),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_upsell_log_contact_id_contacts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], name=op.f('fk_upsell_log_product_id_products'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_upsell_log'))
    )
    with op.batch_alter_table('upsell_log', schema=None) as batch_op:
        batch_op.create_index('ix_upsell_log_contact_id', ['contact_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('upsell_log', schema=None) as batch_op:
        batch_op.drop_index('ix_upsell_log_contact_id')

    op.drop_table('upsell_log')
    with op.batch_alter_table('sms_offers', schema=None) as batch_op:
        batch_op.drop_index('ix_sms_offers_contact_id')

    op.drop_table('sms_offers')
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_index('ix_products_status')

    op.drop_table('products')
