"""Add IP column to nodes.

Revision ID: 37e174f84517
Revises: 51dc0aa2792b
Create Date: 2020-01-12 20:30:50.028199

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "37e174f84517"
down_revision = "51dc0aa2792b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('nodes', sa.Column('ip', sa.String(100)))


def downgrade():
    op.drop_column('nodes', 'ip')
