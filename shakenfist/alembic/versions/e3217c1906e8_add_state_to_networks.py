"""add state to networks

Revision ID: e3217c1906e8
Revises: 4f34b488c7e7
Create Date: 2020-06-01 18:56:10.744416

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e3217c1906e8'
down_revision = '4f34b488c7e7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('networks', sa.Column('state', sa.String(10)))
    op.add_column('networks', sa.Column(
        'state_updated', sa.DateTime))


def downgrade():
    op.drop_column('networks', 'state')
    op.drop_column('networks', 'state_updates')
