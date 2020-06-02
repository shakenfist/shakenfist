"""add state to network interfaces

Revision ID: 4f34b488c7e7
Revises: 2b1062543040
Create Date: 2020-06-01 18:55:13.065842

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4f34b488c7e7'
down_revision = '2b1062543040'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('network_interfaces', sa.Column('state', sa.String(10)))
    op.add_column('network_interfaces', sa.Column(
        'state_updated', sa.DateTime))


def downgrade():
    op.drop_column('network_interfaces', 'state')
    op.drop_column('network_interfaces', 'state_updates')
