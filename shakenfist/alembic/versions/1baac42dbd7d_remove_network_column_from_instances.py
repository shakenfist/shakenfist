"""Remove network column from instances

Revision ID: 1baac42dbd7d
Revises: 7a3e0c7935c2
Create Date: 2020-04-04 12:56:44.184833

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1baac42dbd7d'
down_revision = '7a3e0c7935c2'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('instances_ibfk_1', 'instances', type_='foreignkey')
    op.drop_column('instances', 'network_uuid')


def downgrade():
    op.add_column('instances', sa.Column('network_uuid', sa.String))
