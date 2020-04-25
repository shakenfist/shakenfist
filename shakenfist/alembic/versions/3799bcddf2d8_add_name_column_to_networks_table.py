"""Add name column to networks table.

Revision ID: 3799bcddf2d8
Revises: b92eadab8189
Create Date: 2020-04-25 13:30:29.662950

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3799bcddf2d8'
down_revision = 'b92eadab8189'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('networks', sa.Column('name', sa.String(255)))


def downgrade():
    op.drop_column('networks', 'name')
