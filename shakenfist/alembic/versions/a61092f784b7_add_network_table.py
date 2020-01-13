"""Add network table.

Revision ID: a61092f784b7
Revises: 37e174f84517
Create Date: 2020-01-13 07:31:21.905820

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a61092f784b7"
down_revision = "37e174f84517"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'networks',
        sa.Column('uuid', sa.String(36), primary_key=True),
        sa.Column('vxid', sa.Integer),
        sa.Column('netblock', sa.String(100)),
        sa.Column('provide_dhcp', sa.Boolean),
        sa.Column('provide_nat', sa.Boolean),
        sa.Column('owner', sa.String(36)),
    )


def downgrade():
    op.drop_table('networks')
