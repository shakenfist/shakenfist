"""Add nodes table.

Revision ID: 51dc0aa2792b
Revises: 
Create Date: 2020-01-12 09:56:18.469904

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "51dc0aa2792b"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'nodes',
        sa.Column('fqdn', sa.String(255), primary_key=True),
        sa.Column('lastseen', sa.DateTime),
    )


def downgrade():
    op.drop_table('nodes')
