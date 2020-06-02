"""add state to instances

Revision ID: 2b1062543040
Revises: 68a7b2a83f35
Create Date: 2020-06-01 17:14:56.462055

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2b1062543040'
down_revision = '68a7b2a83f35'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('instances', sa.Column('state', sa.String(10)))
    op.add_column('instances', sa.Column('state_updated', sa.DateTime))


def downgrade():
    op.drop_column('instances', 'state')
    op.drop_column('instances', 'state_updates')
