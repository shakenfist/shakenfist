"""Expand ssh key column for instances table

Revision ID: f888c0f66cdc
Revises: 5fae55257576
Create Date: 2020-04-13 11:36:28.717922

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f888c0f66cdc'
down_revision = '5fae55257576'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('instances', 'ssh_key',
                    existing_type=sa.VARCHAR(length=512),
                    type_=sa.String(length=2048))


def downgrade():
    op.alter_column('instances', 'ssh_key',
                    existing_type=sa.VARCHAR(length=2048),
                    type_=sa.String(length=512))
