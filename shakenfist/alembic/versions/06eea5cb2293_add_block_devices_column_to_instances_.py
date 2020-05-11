"""Add block_devices column to instances table.

Revision ID: 06eea5cb2293
Revises: 90fc9c3bc1ad
Create Date: 2020-05-04 14:54:17.236124

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '06eea5cb2293'
down_revision = '90fc9c3bc1ad'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('instances', sa.Column('block_devices', sa.BLOB))


def downgrade():
    op.drop_column('instances', 'block_devices')
