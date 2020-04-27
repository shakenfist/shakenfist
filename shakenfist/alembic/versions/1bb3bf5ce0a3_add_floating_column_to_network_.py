"""Add floating column to network interfaces table.

Revision ID: 1bb3bf5ce0a3
Revises: 3799bcddf2d8
Create Date: 2020-04-27 13:33:40.131285

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1bb3bf5ce0a3'
down_revision = '3799bcddf2d8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('network_interfaces', sa.Column('floating', sa.String(15)))


def downgrade():
    op.drop_column('network_interfaces', 'floating')
