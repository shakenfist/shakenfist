"""Add order to networkinterfaces.

Revision ID: 6423651f41b1
Revises: 1baac42dbd7d
Create Date: 2020-04-04 12:57:43.336424

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6423651f41b1'
down_revision = '1baac42dbd7d'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('network_interfaces', sa.Column('order', sa.Integer))


def downgrade():
    op.drop_column('instances', 'order')
