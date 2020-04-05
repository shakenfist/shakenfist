"""Add console port column to instances

Revision ID: 7a3e0c7935c2
Revises: a0d4334c57bd
Create Date: 2020-04-01 11:29:28.958814

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7a3e0c7935c2'
down_revision = 'a0d4334c57bd'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('instances', sa.Column('console_port', sa.Integer))


def downgrade():
    op.drop_column('instances', 'console_port')
