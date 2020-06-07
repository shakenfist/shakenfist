"""Add network device model to network_interfaces

Revision ID: 2ad57a85d2d8
Revises: e3217c1906e8
Create Date: 2020-06-05 11:42:43.538285

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2ad57a85d2d8'
down_revision = 'e3217c1906e8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('network_interfaces', sa.Column('model', sa.String(20)))


def downgrade():
    op.drop_column('network_interfaces', 'model')
