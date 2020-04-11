"""Add vdi port column to instances

Revision ID: ebd3d001f221
Revises: 6423651f41b1
Create Date: 2020-04-11 12:02:08.038624

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ebd3d001f221'
down_revision = '6423651f41b1'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('instances', sa.Column('vdi_port', sa.Integer))


def downgrade():
    op.drop_column('instances', 'vdi_port')
