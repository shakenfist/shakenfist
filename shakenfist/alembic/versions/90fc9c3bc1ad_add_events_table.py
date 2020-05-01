"""Add events table.

Revision ID: 90fc9c3bc1ad
Revises: 1bb3bf5ce0a3
Create Date: 2020-05-01 08:37:04.503943

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '90fc9c3bc1ad'
down_revision = '1bb3bf5ce0a3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'events',
        sa.Column('timestamp', sa.DateTime),
        sa.Column('object_type', sa.String(10)),
        sa.Column('object_uuid', sa.String(36)),
        sa.Column('fqdn', sa.String(255)),
        sa.Column('operation', sa.String(255)),
        sa.Column('phase', sa.String(10)),
        sa.Column('duration', sa.Float),
        sa.Column('message', sa.String(1024)),
    )
    op.create_index('timestamp_idx', 'events', ['timestamp'])
    op.create_index('object_type_idx', 'events', ['object_type'])
    op.create_index('object_uuid_idx', 'events', ['object_uuid'])
    op.create_index('fqdn_idx', 'events', ['fqdn'])
    op.create_index('operation_idx', 'events', ['operation'])


def downgrade():
    op.drop_table('events')
