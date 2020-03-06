"""Add network_interfaces_and_instances tables

Revision ID: 380dc4546d07
Revises: a61092f784b7
Create Date: 2020-03-05 16:31:50.809141

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '380dc4546d07'
down_revision = 'a61092f784b7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'instances',
        sa.Column('uuid', sa.String(36), primary_key=True),
        sa.Column('network_uuid', sa.String(36),
                  sa.ForeignKey('networks.uuid')),
        sa.Column('name', sa.String(255)),
        sa.Column('cpus', sa.Integer),
        sa.Column('memory', sa.Integer),
        sa.Column('disk_spec', sa.String(1024)),
        sa.Column('ssh_key', sa.String(512))
    )

    op.create_table(
        'network_interfaces',
        sa.Column('uuid', sa.String(36), primary_key=True),
        sa.Column('network_uuid', sa.String(36),
                  sa.ForeignKey('networks.uuid')),
        sa.Column('instance_uuid', sa.String(36),
                  sa.ForeignKey('instances.uuid')),
        sa.Column('macaddr', sa.String(17)),
        sa.Column('ipv4', sa.String(15))
    )
    op.create_index('nic_network_uuid_idx',
                    'network_interfaces', ['network_uuid'])
    op.create_index('nic_instance_uuid_idx',
                    'network_interfaces', ['instance_uuid'])


def downgrade():
    op.drop_table('network_interfaces')
    op.drop_table('instances')
