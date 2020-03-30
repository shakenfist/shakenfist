"""Add node column to instances

Revision ID: a0d4334c57bd
Revises: 8bc15d422f9d
Create Date: 2020-03-30 07:29:50.100752

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0d4334c57bd'
down_revision = '8bc15d422f9d'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('instances', sa.Column('node', sa.String(255)))


def downgrade():
    op.drop_column('instances', 'node')
