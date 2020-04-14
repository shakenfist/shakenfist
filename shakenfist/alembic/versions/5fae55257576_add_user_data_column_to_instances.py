"""Add user data column to instances

Revision ID: 5fae55257576
Revises: ebd3d001f221
Create Date: 2020-04-12 17:10:27.816202

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5fae55257576'
down_revision = 'ebd3d001f221'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('instances', sa.Column('user_data', sa.String(21845)))


def downgrade():
    op.drop_column('instances', 'user_data')
