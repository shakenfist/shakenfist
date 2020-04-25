"""Add ipmanager column to networks table.

Revision ID: b92eadab8189
Revises: f888c0f66cdc
Create Date: 2020-04-24 09:28:44.174347

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b92eadab8189'
down_revision = 'f888c0f66cdc'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('networks', sa.Column('ipmanager', sa.BLOB))
    op.add_column('networks', sa.Column('floating_gateway', sa.String(15)))


def downgrade():
    op.drop_column('networks', 'floating_gateway')
    op.drop_column('networks', 'ipmanager')
