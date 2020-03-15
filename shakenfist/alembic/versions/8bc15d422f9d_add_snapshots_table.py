"""Add snapshots table

Revision ID: 8bc15d422f9d
Revises: 380dc4546d07
Create Date: 2020-03-12 20:27:44.557788

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8bc15d422f9d'
down_revision = '380dc4546d07'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'snapshots',
        sa.Column('uuid', sa.String(36), primary_key=True),
        sa.Column('device', sa.String(10), primary_key=True),
        sa.Column('instance_uuid', sa.String(36),
                  sa.ForeignKey('instances.uuid')),
        sa.Column('created', sa.DateTime),
    )


def downgrade():
    drop.drop_table('snapshots')
