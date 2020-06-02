"""allow longer phase names for events

Revision ID: 68a7b2a83f35
Revises: c0848811a04a
Create Date: 2020-05-30 17:07:26.906647

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '68a7b2a83f35'
down_revision = 'c0848811a04a'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('events', 'phase',
                    existing_type=sa.VARCHAR(length=10),
                    type_=sa.String(length=50))


def downgrade():
    op.alter_column('events', 'phase',
                    existing_type=sa.VARCHAR(length=50),
                    type_=sa.String(length=10))
