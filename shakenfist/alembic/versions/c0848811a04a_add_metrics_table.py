"""add metrics table

Revision ID: c0848811a04a
Revises: 06eea5cb2293
Create Date: 2020-05-30 10:12:49.594493

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c0848811a04a'
down_revision = '06eea5cb2293'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'node_metrics',
        sa.Column('fqdn', sa.String(255), primary_key=True),
        sa.Column('metric', sa.String(255), primary_key=True),
        sa.Column('value', sa.String(20)),
        sa.Column('timestamp', sa.DateTime),
    )


def downgrade():
    op.drop_table('node_metrics')
