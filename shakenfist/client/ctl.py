# Copyright 2020 Michael Still
import importlib
import json
import logging
import os
import uuid as uuid_module
from dataclasses import dataclass
from dataclasses import field
from typing import Optional

import click
from shakenfist_utilities import logs  # noreorder


LOG = logs.setup_console(__name__)


@dataclass
class MigrationStats:
    """Track statistics during migration operations."""
    migrated: int = 0
    skipped: int = 0
    errors: int = 0
    categories: dict = field(default_factory=dict)
    progress_interval: int = 100

    def add_category(self, name: str):
        """Add a category to track separately."""
        self.categories[name] = 0

    def record_migrated(self, category: Optional[str] = None):
        """Record a successful migration."""
        self.migrated += 1
        if category and category in self.categories:
            self.categories[category] += 1

    def record_skipped(self):
        """Record a skipped item (already exists)."""
        self.skipped += 1

    def record_error(self, message: str):
        """Record an error."""
        self.errors += 1
        click.echo(f'  {message}')

    @property
    def total_processed(self) -> int:
        """Total items processed (migrated + skipped + errors)."""
        return self.migrated + self.skipped + self.errors

    def should_show_progress(self) -> bool:
        """Check if we should show progress update."""
        return (self.total_processed > 0 and
                self.total_processed % self.progress_interval == 0)

    def show_progress(self, object_type: str = 'items'):
        """Show progress if interval reached."""
        if self.should_show_progress():
            click.echo(f'  ... {self.total_processed} {object_type} processed')

    def print_summary(self):
        """Print migration summary."""
        if self.categories:
            click.echo('\n--- Migration Summary ---')
            for name, count in self.categories.items():
                click.echo(f'  {name}: {count}')
            total = sum(self.categories.values())
            click.echo(f'\nTotal: {total}')
        else:
            click.echo(f'\nTotal: {self.migrated} migrated')
        click.echo(f'Skipped (already exist): {self.skipped}')
        click.echo(f'Errors: {self.errors}')


def parse_uuid(uuid_str: str, description: str = 'UUID') -> Optional[uuid_module.UUID]:
    """Parse a UUID string, returning None if invalid."""
    try:
        return uuid_module.UUID(uuid_str)
    except (ValueError, AttributeError):
        return None


def migration_precheck(dry_run: bool) -> bool:
    """Common pre-migration setup. Returns True if setup succeeded."""
    if not dry_run:
        click.echo('Ensuring MariaDB schema exists...')
        mariadb.ensure_schema()
    return True


def migration_postcheck(dry_run: bool):
    """Common post-migration message."""
    if dry_run:
        click.echo('\nThis was a dry run. No changes were made.')
    else:
        click.echo('\nMigration complete. You can now start Shaken Fist services.')


# Utilities not started by systemd need to load /etc/sf/config to ensure
# that they are correctly configured. Environment variables set before
# running the utility take precedence over values in the config file.
if os.path.exists('/etc/sf/config'):
    with open('/etc/sf/config') as f:
        for line in f.readlines():
            line = line.rstrip()

            if line.startswith('#'):
                continue
            if line == '':
                continue

            key, value = line.split('=')
            value = value.strip('\'"')

            if key not in os.environ:
                os.environ[key] = value

# We skip verifying the auth seed config setting here because we might be
# bootstrapping it.
sf_config = importlib.import_module('shakenfist.config')
sf_config.verify_config(skip_auth_seed=True)
config = sf_config.config

# These imports _must_ occur after the extra config setup has run.
from shakenfist import etcd                                # noqa
from shakenfist import mariadb                             # noqa
from shakenfist.namespace import Namespace                 # noqa
from shakenfist.node import Node                           # noqa
from shakenfist.schema.object_state import State           # noqa
from shakenfist.schema.object_types import ObjectType      # noqa


@click.group()
@click.option('--verbose/--no-verbose', default=False)
@click.pass_context
def cli(ctx, verbose=None):
    if verbose:
        LOG.setLevel(logging.DEBUG)


@click.command()
@click.argument('keyname')
@click.argument('key')
def bootstrap_system_key(keyname, key):
    click.echo('Creating key %s' % keyname)
    ns = Namespace.new('system')
    ns.add_key(keyname, key)
    click.echo('Done')


@click.command()
def show_etcd_config():
    value = etcd.get_etcd_client().get('/sf/config', metadata=True)
    if value is None or len(value) == 0:
        click.echo('{}')
    else:
        click.echo(json.dumps(json.loads(
            value[0][0]), indent=4, sort_keys=True))


@click.command()
@click.argument('flag')
@click.argument('value')
def set_etcd_config(flag, value):
    client = etcd.get_etcd_client()
    config = {}
    current_config = client.get('/sf/config', metadata=True)
    if current_config is None or len(current_config) == 0:
        config = {}
    else:
        config = json.loads(current_config[0][0])

    # Convert values if possible
    if value in ['t', 'true', 'True']:
        value = True
    elif value in ['f', 'false', 'False']:
        value = False
    else:
        try:
            if value.find('.') != -1:
                value = float(value)
            else:
                value = int(value)
        except ValueError:
            pass

    click.echo(f'Setting {flag} to {type(value)}({value})')
    config[flag] = value
    client.put('/sf/config', json.dumps(config, indent=4, sort_keys=True))


@click.command()
def verify_config():
    sf_config.verify_config()
    click.echo('Configuration is ok')


@click.command()
def ensure_mariadb_schema():
    """Ensure the MariaDB schema exists and is up to date.

    This command should be run on a database node (etcd_master) before
    initializing any nodes. It creates the required MariaDB tables if
    they don't already exist, and applies any pending schema migrations.
    Only nodes with direct MariaDB access (MARIADB_HOST configured) can
    run this command.
    """
    if not config.MARIADB_HOST:
        raise click.ClickException(
            'This command requires MARIADB_HOST to be configured. '
            'It should only be run on database nodes (etcd_master).')

    results = mariadb.ensure_schema()

    for r in results:
        if r['migrated']:
            if r['start_version'] <= 0:
                click.echo(f"Created table '{r['table']}' at version "
                           f"{r['end_version']}")
            else:
                click.echo(f"Migrated table '{r['table']}' from version "
                           f"{r['start_version']} to {r['end_version']}")
        else:
            click.echo(f"Table '{r['table']}' is up to date "
                       f"(version {r['end_version']})")

    click.echo('MariaDB schema verified.')


@click.command()
@click.option('--node-name', default=None,
              help='Node name to initialize (defaults to NODE_NAME from config)')
@click.option('--node-mesh-ip', default=None,
              help='Node mesh IP (defaults to NODE_MESH_IP from config)')
def initialise_node(node_name, node_mesh_ip):
    """Initialize a node in the database.

    When run without arguments, initializes the local node using NODE_NAME
    and NODE_MESH_IP from the configuration. When run with --node-name and
    --node-mesh-ip, can initialize any node (useful for bootstrapping from
    the etcd_master with direct database access).
    """
    node_name = node_name or config.NODE_NAME
    node_mesh_ip = node_mesh_ip or config.NODE_MESH_IP

    click.echo(f'Initializing node "{node_name}" with mesh IP '
               f'{node_mesh_ip}...')
    n = Node.new(node_name, node_mesh_ip)
    click.echo(f'Node "{node_name}" is now in state {n.state.value}.')


@click.command()
@click.argument('daemon', nargs=-1)
@click.option('--node-name', default=None,
              help='Node name to register daemons on (defaults to NODE_NAME)')
def register_daemon(daemon, node_name):
    """Register one or more daemons on a node.

    When run without --node-name, registers daemons on the local node.
    When run with --node-name, can register daemons on any node (useful
    for bootstrapping from the etcd_master with direct database access).
    """
    node_name = node_name or config.NODE_NAME
    n = Node.from_db(node_name)
    if n is None:
        raise click.ClickException(
            f'Node "{node_name}" not found in database. '
            f'Run "sf-ctl initialise-node" first to create the node.')
    for d in daemon:
        click.echo(f'Registering {d} on node "{node_name}"...')
        n.register_daemon(d)
        click.echo(f'Daemon is now in state {n.get_daemon_state(d).value}.')
    click.echo(f'Node "{node_name}" is now in state {n.state.value}.')


@click.command()
@click.argument('daemon', nargs=-1)
def deregister_daemon(daemon):
    n = Node.from_db(config.NODE_NAME)
    for d in daemon:
        click.echo(f'Deregistering {d} on node...')
        n.deregister_daemon(d)
    click.echo(f'Node is now in state {n.state.value}.')


@click.command()
@click.argument('daemon')
def stop(daemon):
    click.echo(
        f'Gracefully stopping Shaken Fist {daemon} daemon on this node...')
    n = Node.from_db(config.NODE_NAME)

    # If we were missing, we're not any more
    if n.state.value == Node.STATE_MISSING:
        n.state = Node.STATE_DEGRADED

    n.set_daemon_state(daemon, Node.DAEMON_STATE_STOPPING)


# All object types that have state stored in etcd. This includes both regular
# objects (instances, networks, etc.) and cluster operations (node_blob_op, etc.)
OBJECT_TYPES_WITH_STATE = [
    # Regular objects
    'agentoperation',
    'artifact',
    'blob',
    'dhcp',
    'instance',
    'interface',
    'ipam',
    'namespace',
    'network',
    'node',
    'upload',
    # Cluster operations (from CLUSTER_OPERATIONS enum)
    'artifact_fetch_op',
    'imgcache_op',
    'net_iface_ip_op',
    'net_iface_op',
    'net_ip_op',
    'net_macaddr_ip_op',
    'net_op',
    'node_aop_op',
    'node_blob_op',
    'node_inst_net_iface_op',
    'node_inst_netdesc_op',
    'node_inst_op',
    'node_inst_snap_op',
    'node_net_op',
]


@click.command()
@click.option('--dry-run', is_flag=True, default=False,
              help='Show what would be migrated without making changes')
def migrate_state_to_mariadb(dry_run):
    """Migrate all object state from etcd to MariaDB.

    This command should be run once during an upgrade to move state data
    from etcd attributes to the MariaDB object_states table. All Shaken Fist
    services should be stopped before running this command.

    After migration, the state entries are removed from etcd.
    """
    migration_precheck(dry_run)
    stats = MigrationStats()

    for object_type in OBJECT_TYPES_WITH_STATE:
        click.echo(f'\nMigrating {object_type} objects...')
        type_migrated = 0
        type_skipped = 0

        for objkey, _ in etcd.get_all(object_type, None):
            objuuid = objkey.split('/')[-1]
            state_data = etcd.get(f'attribute/{object_type}', objuuid, 'state')
            if not state_data:
                type_skipped += 1
                continue

            if dry_run:
                click.echo(f'  Would migrate {objuuid}: {state_data.get("value")}')
            else:
                state = State(**state_data)
                mariadb.set_state(ObjectType(object_type), objuuid, state)
                etcd.delete(f'attribute/{object_type}', objuuid, 'state')

            type_migrated += 1
            if type_migrated % 100 == 0:
                click.echo(f'  ... {type_migrated} objects processed')

        click.echo(f'  {object_type}: {type_migrated} migrated, '
                   f'{type_skipped} skipped')
        stats.migrated += type_migrated
        stats.skipped += type_skipped

    click.echo(f'\nTotal: {stats.migrated} objects migrated, '
               f'{stats.skipped} objects skipped (no state)')
    migration_postcheck(dry_run)


@click.command()
@click.option('--dry-run', is_flag=True, default=False,
              help='Show what would be migrated without making changes')
def migrate_ipam_to_mariadb(dry_run):
    """Migrate all IPAM reservations from etcd to MariaDB.

    This command should be run once during an upgrade to move IPAM reservation
    data from etcd to the MariaDB ipam_reservations table. All Shaken Fist
    services should be stopped before running this command.

    After migration, the reservation entries are removed from etcd.
    """
    from shakenfist.schema.ipam_reservation import IPAMReservation

    migration_precheck(dry_run)
    stats = MigrationStats()

    click.echo('\nScanning for IPAM reservations in etcd...')

    for key, data in etcd.get_prefix_raw('/sf/ipam_reservations/'):
        parts = key.split('/')
        if len(parts) < 5:
            stats.record_error(f'Skipping invalid key: {key}')
            continue

        ipam_uuid = parts[3]
        address = parts[4]

        if dry_run:
            res_type = data.get('type', 'unknown')
            click.echo(f'  Would migrate {ipam_uuid}/{address}: {res_type}')
            stats.record_migrated()
            continue

        try:
            reservation = IPAMReservation.from_legacy_dict(ipam_uuid, data)
            success = mariadb._direct_reserve_address(reservation)
            if success:
                etcd.delete_raw(key)
                stats.record_migrated()
            else:
                click.echo(f'  Skipping {ipam_uuid}/{address}: already in MariaDB')
                etcd.delete_raw(key)
                stats.record_skipped()
        except Exception as e:
            stats.record_error(f'Error migrating {ipam_uuid}/{address}: {e}')

        stats.show_progress('reservations')

    stats.print_summary()
    migration_postcheck(dry_run)


@click.command()
@click.option('--dry-run', is_flag=True, default=False,
              help='Show what would be migrated without making changes')
def migrate_floating_network_uuid(dry_run):
    """Migrate the floating network from legacy UUID to well-known UUID.

    The floating network previously used the string "floating" as its UUID,
    which is not a valid UUID4. This command migrates it to use the well-known
    UUID f10a7f10-a7f1-4a7f-a10a-7f10a7f10a7f (containing "F10A7" = FLOAT).

    This command should be run once during an upgrade. All Shaken Fist
    services should be stopped before running this command.
    """
    from shakenfist.constants import FLOATING_NETWORK_UUID

    # Check if legacy floating network exists
    legacy_network = etcd.get('network', None, 'floating')
    if not legacy_network:
        click.echo('No legacy floating network found (UUID "floating").')
        click.echo('Either already migrated or never created.')
        return

    # Check if new UUID already exists
    new_network = etcd.get('network', None, FLOATING_NETWORK_UUID)
    if new_network:
        click.echo(f'Network with new UUID {FLOATING_NETWORK_UUID} already exists.')
        click.echo('Cannot migrate - would overwrite existing network.')
        return

    click.echo('Found legacy floating network with UUID "floating"')
    click.echo(f'Will migrate to UUID: {FLOATING_NETWORK_UUID}')

    if dry_run:
        click.echo('\nDry run - no changes made.')
        return

    # Migrate the network object
    click.echo('\nMigrating network object...')
    legacy_network['uuid'] = FLOATING_NETWORK_UUID
    etcd.put('network', None, FLOATING_NETWORK_UUID, legacy_network)
    etcd.delete('network', None, 'floating')
    click.echo('  Network object migrated.')

    # Migrate the IPAM object
    click.echo('Migrating IPAM object...')
    legacy_ipam = etcd.get('ipam', None, 'floating')
    if legacy_ipam:
        legacy_ipam['uuid'] = FLOATING_NETWORK_UUID
        etcd.put('ipam', None, FLOATING_NETWORK_UUID, legacy_ipam)
        etcd.delete('ipam', None, 'floating')
        click.echo('  IPAM object migrated.')
    else:
        click.echo('  No legacy IPAM object found.')

    # Migrate network state in MariaDB if it exists
    click.echo('Migrating state in MariaDB...')
    try:
        state_data = mariadb.get_state(ObjectType.NETWORK, 'floating')
        if state_data:
            mariadb.set_state(
                ObjectType.NETWORK, str(FLOATING_NETWORK_UUID), state_data)
            mariadb.delete_state(ObjectType.NETWORK, 'floating')
            click.echo('  Network state migrated.')
        else:
            click.echo('  No network state found in MariaDB.')
    except Exception as e:
        click.echo(f'  Could not migrate MariaDB state: {e}')

    # Migrate IPAM state in MariaDB if it exists
    try:
        ipam_state = mariadb.get_state(ObjectType.IPAM, 'floating')
        if ipam_state:
            mariadb.set_state(
                ObjectType.IPAM, str(FLOATING_NETWORK_UUID), ipam_state)
            mariadb.delete_state(ObjectType.IPAM, 'floating')
            click.echo('  IPAM state migrated.')
        else:
            click.echo('  No IPAM state found in MariaDB.')
    except Exception as e:
        click.echo(f'  Could not migrate IPAM MariaDB state: {e}')

    click.echo('\nMigration complete.')
    click.echo('You can now start Shaken Fist services.')


@click.command()
@click.option('--dry-run', is_flag=True, default=False,
              help='Show what would be migrated without making changes')
def migrate_uploads_to_mariadb(dry_run):
    """Migrate all upload objects from etcd to MariaDB.

    This command should be run once during an upgrade to move upload static
    values from etcd to the MariaDB uploads table. All Shaken Fist services
    should be stopped before running this command.

    After migration, the upload entries are removed from etcd.
    """
    from shakenfist.upload import Upload

    migration_precheck(dry_run)
    stats = MigrationStats()

    click.echo('\nScanning for upload objects in etcd...')

    for objkey, data in etcd.get_all('upload', None):
        upload_uuid = objkey.split('/')[-1]

        if dry_run:
            click.echo(f'  Would migrate {upload_uuid}: node={data.get("node")}')
            stats.record_migrated()
            continue

        try:
            upload_uuid_obj = parse_uuid(upload_uuid, 'upload UUID')
            if not upload_uuid_obj:
                stats.record_error(f'Invalid UUID: {upload_uuid}')
                continue

            success = mariadb.create_upload(
                upload_uuid_obj,
                data['node'],
                data['created_at'],
                data.get('version', Upload.current_version)
            )
            if success:
                etcd.delete('upload', None, upload_uuid)
                stats.record_migrated()
            else:
                click.echo(f'  Skipping {upload_uuid}: already in MariaDB')
                etcd.delete('upload', None, upload_uuid)
                stats.record_skipped()
        except Exception as e:
            stats.record_error(f'Error migrating {upload_uuid}: {e}')

        stats.show_progress('uploads')

    stats.print_summary()
    migration_postcheck(dry_run)


@cli.command(name='migrate-dnsmasq-to-mariadb')
@click.option('--dry-run', is_flag=True, default=False,
              help='Show what would be migrated without making changes')
def migrate_dnsmasq_to_mariadb(dry_run):
    """Migrate all DnsMasq objects from etcd to MariaDB.

    This command should be run once during an upgrade to move DnsMasq static
    values from etcd to the MariaDB dnsmasq table. All Shaken Fist services
    should be stopped before running this command.

    After migration, the DnsMasq entries are removed from etcd.
    """
    from shakenfist.managed_executables.dnsmasq import DnsMasq
    from shakenfist.schema.dnsmasq import DnsMasqData

    migration_precheck(dry_run)
    stats = MigrationStats()

    click.echo('\nScanning for DnsMasq objects in etcd...')

    # DnsMasq uses ObjectType.DHCP for historical reasons
    for objkey, data in etcd.get_all('dhcp', None):
        dnsmasq_uuid = objkey.split('/')[-1]

        # Apply upgrades to legacy data
        version = data.get('version', DnsMasq.initial_version)
        while version < DnsMasq.current_version:
            step_name = f'_upgrade_step_{version}_to_{version + 1}'
            step_func = getattr(DnsMasq, step_name, None)
            if step_func:
                step_func(data)
            version += 1
            data['version'] = version

        # Convert owner_type to ObjectType if it's a string
        owner_type = data.get('owner_type')
        if isinstance(owner_type, str):
            owner_type = ObjectType(owner_type)
        else:
            owner_type = ObjectType.UNKNOWN

        if dry_run:
            click.echo(
                f'  Would migrate {dnsmasq_uuid}: '
                f'namespace={data.get("namespace")}, '
                f'owner={owner_type}({data.get("owner_uuid")}), '
                f'dhcp={data.get("provide_dhcp")}, '
                f'dns={data.get("provide_dns")}')
            stats.record_migrated()
            continue

        try:
            dnsmasq_data = DnsMasqData(
                uuid=dnsmasq_uuid,
                namespace=data.get('namespace', 'unknown'),
                owner_type=owner_type,
                owner_uuid=data.get('owner_uuid', dnsmasq_uuid),
                version=DnsMasq.current_version,
                provide_dhcp=data.get('provide_dhcp', True),
                provide_dns=data.get('provide_dns', False)
            )
            success = mariadb.create_dnsmasq(dnsmasq_data)
            if success:
                etcd.delete('dhcp', None, dnsmasq_uuid)
                stats.record_migrated()
            else:
                click.echo(f'  Skipping {dnsmasq_uuid}: already in MariaDB')
                etcd.delete('dhcp', None, dnsmasq_uuid)
                stats.record_skipped()
        except Exception as e:
            stats.record_error(f'Error migrating {dnsmasq_uuid}: {e}')

        stats.show_progress('DnsMasq objects')

    stats.print_summary()
    migration_postcheck(dry_run)


@click.command()
@click.option('--dry-run', is_flag=True, default=False,
              help='Show what would be migrated without making changes')
def migrate_references_to_mariadb(dry_run):
    """Migrate blob references to the MariaDB object_references table.

    This command scans all objects that reference blobs and creates
    relationship records in the MariaDB object_references table. This
    replaces the manual ref_count attribute previously stored on blobs.

    After migration, the old blob_references attributes are removed from
    instances and the ref_count attributes are removed from blobs.

    All Shaken Fist services should be stopped before running this command.
    """
    import time
    from shakenfist.schema.relationship_types import RelationshipType

    migration_precheck(dry_run)
    stats = MigrationStats()
    stats.add_category('DISK')
    stats.add_category('NVRAM_TEMPLATE')
    stats.add_category('ARTIFACT_INDEX')
    stats.add_category('DEPENDS_ON')
    stats.add_category('TRANSCODE')
    stats.add_category('AGENT_OUTPUT')
    now = time.time()

    # --- Instances: disk references and nvram_template ---
    click.echo('\nScanning instances for blob references...')
    for objkey, data in etcd.get_all('instance', None):
        instance_uuid = objkey.split('/')[-1]
        instance_uuid_obj = parse_uuid(instance_uuid, 'instance UUID')
        if not instance_uuid_obj:
            stats.record_error(f'Invalid instance UUID: {instance_uuid}')
            continue

        disk_spec = data.get('disk_spec', [])

        # Create DISK references for each disk with a blob_uuid
        for disk_idx, disk in enumerate(disk_spec):
            blob_uuid = disk.get('blob_uuid')
            if not blob_uuid:
                continue

            blob_uuid_obj = parse_uuid(blob_uuid, 'blob UUID')
            if not blob_uuid_obj:
                stats.record_error(f'Invalid blob UUID: {blob_uuid}')
                continue

            if dry_run:
                click.echo(
                    f'  Would create DISK reference: instance {instance_uuid} '
                    f'disk {disk_idx} -> blob {blob_uuid}')
                stats.record_migrated('DISK')
            else:
                success = mariadb.record_relationship(
                    ObjectType.INSTANCE, instance_uuid_obj,
                    RelationshipType.DISK, str(disk_idx),
                    ObjectType.BLOB, blob_uuid_obj,
                    now)
                if success:
                    stats.record_migrated('DISK')
                else:
                    stats.record_skipped()

        # Handle nvram_template
        nvram_template = data.get('nvram_template')
        if nvram_template:
            nvram_uuid_obj = parse_uuid(nvram_template, 'nvram UUID')
            if not nvram_uuid_obj:
                stats.record_error(f'Invalid nvram UUID: {nvram_template}')
            elif dry_run:
                click.echo(
                    f'  Would create NVRAM_TEMPLATE reference: '
                    f'instance {instance_uuid} -> blob {nvram_template}')
                stats.record_migrated('NVRAM_TEMPLATE')
            else:
                success = mariadb.record_relationship(
                    ObjectType.INSTANCE, instance_uuid_obj,
                    RelationshipType.NVRAM_TEMPLATE, None,
                    ObjectType.BLOB, nvram_uuid_obj,
                    now)
                if success:
                    stats.record_migrated('NVRAM_TEMPLATE')
                else:
                    stats.record_skipped()

        # Remove old blob_references attribute if not dry run
        if not dry_run:
            etcd.delete('attribute/instance', instance_uuid, 'blob_references')

        stats.show_progress('instance references')

    # --- Artifacts: index_* references ---
    click.echo('\nScanning artifacts for index references...')
    for objkey, _ in etcd.get_all('artifact', None):
        artifact_uuid = objkey.split('/')[-1]
        artifact_uuid_obj = parse_uuid(artifact_uuid, 'artifact UUID')
        if not artifact_uuid_obj:
            stats.record_error(f'Invalid artifact UUID: {artifact_uuid}')
            continue

        # Get all index_* attributes
        for attrkey, index_data in etcd.get_all(
                'attribute/artifact', artifact_uuid, prefix='index_'):
            if not index_data:
                continue

            # Extract index number from attribute key
            index_str = attrkey.split('/')[-1].replace('index_', '')
            blob_uuid = index_data.get('blob_uuid')
            if not blob_uuid:
                continue

            blob_uuid_obj = parse_uuid(blob_uuid, 'blob UUID')
            if not blob_uuid_obj:
                stats.record_error(f'Invalid blob UUID: {blob_uuid}')
                continue

            if dry_run:
                click.echo(
                    f'  Would create ARTIFACT_INDEX reference: '
                    f'artifact {artifact_uuid} index {index_str} -> '
                    f'blob {blob_uuid}')
                stats.record_migrated('ARTIFACT_INDEX')
            else:
                success = mariadb.record_relationship(
                    ObjectType.ARTIFACT, artifact_uuid_obj,
                    RelationshipType.ARTIFACT_INDEX, index_str,
                    ObjectType.BLOB, blob_uuid_obj,
                    now)
                if success:
                    stats.record_migrated('ARTIFACT_INDEX')
                else:
                    stats.record_skipped()

        stats.show_progress('artifact references')

    # --- Blobs: depends_on and transcoded references ---
    click.echo('\nScanning blobs for depends_on and transcoded references...')
    for objkey, data in etcd.get_all('blob', None):
        blob_uuid = objkey.split('/')[-1]
        blob_uuid_obj = parse_uuid(blob_uuid, 'blob UUID')
        if not blob_uuid_obj:
            stats.record_error(f'Invalid blob UUID: {blob_uuid}')
            continue

        # Handle depends_on (stored in static values)
        depends_on = data.get('depends_on')
        if depends_on:
            dep_uuid_obj = parse_uuid(depends_on, 'depends_on UUID')
            if not dep_uuid_obj:
                stats.record_error(f'Invalid depends_on UUID: {depends_on}')
            elif dry_run:
                click.echo(
                    f'  Would create DEPENDS_ON reference: '
                    f'blob {blob_uuid} -> blob {depends_on}')
                stats.record_migrated('DEPENDS_ON')
            else:
                success = mariadb.record_relationship(
                    ObjectType.BLOB, blob_uuid_obj,
                    RelationshipType.DEPENDS_ON, None,
                    ObjectType.BLOB, dep_uuid_obj,
                    now)
                if success:
                    stats.record_migrated('DEPENDS_ON')
                else:
                    stats.record_skipped()

        # Handle transcoded (stored as attribute)
        transcoded = etcd.get('attribute/blob', blob_uuid, 'transcoded')
        if transcoded:
            for style, transcoded_blob_uuid in transcoded.items():
                trans_uuid_obj = parse_uuid(transcoded_blob_uuid,
                                            'transcoded UUID')
                if not trans_uuid_obj:
                    stats.record_error(
                        f'Invalid transcoded UUID: {transcoded_blob_uuid}')
                    continue

                if dry_run:
                    click.echo(
                        f'  Would create TRANSCODE reference: '
                        f'blob {blob_uuid} style {style} -> '
                        f'blob {transcoded_blob_uuid}')
                    stats.record_migrated('TRANSCODE')
                else:
                    success = mariadb.record_relationship(
                        ObjectType.BLOB, blob_uuid_obj,
                        RelationshipType.TRANSCODE, style,
                        ObjectType.BLOB, trans_uuid_obj,
                        now)
                    if success:
                        stats.record_migrated('TRANSCODE')
                    else:
                        stats.record_skipped()

        # Remove old attributes if not dry run
        if not dry_run:
            etcd.delete('attribute/blob', blob_uuid, 'ref_count')
            etcd.delete('attribute/blob', blob_uuid, 'transcoded')

        stats.show_progress('blob references')

    # --- AgentOperations: *_blob references ---
    click.echo('\nScanning agent operations for blob references...')
    for objkey, _ in etcd.get_all('agentoperation', None):
        aop_uuid = objkey.split('/')[-1]
        aop_uuid_obj = parse_uuid(aop_uuid, 'agentoperation UUID')
        if not aop_uuid_obj:
            stats.record_error(f'Invalid agentoperation UUID: {aop_uuid}')
            continue

        results_data = etcd.get('attribute/agentoperation', aop_uuid, 'results')
        if not results_data:
            continue

        results = results_data.get('results', {})
        for result_idx, result in results.items():
            if not isinstance(result, dict):
                continue
            for key, value in result.items():
                if not key.endswith('_blob'):
                    continue
                output_type = key.replace('_blob', '')

                result_blob_uuid_obj = parse_uuid(value, 'result blob UUID')
                if not result_blob_uuid_obj:
                    stats.record_error(f'Invalid blob UUID: {value}')
                    continue

                if dry_run:
                    click.echo(
                        f'  Would create AGENT_OUTPUT reference: '
                        f'agentoperation {aop_uuid} {output_type} -> '
                        f'blob {value}')
                    stats.record_migrated('AGENT_OUTPUT')
                else:
                    success = mariadb.record_relationship(
                        ObjectType.AGENTOPERATION, aop_uuid_obj,
                        RelationshipType.AGENT_OUTPUT, output_type,
                        ObjectType.BLOB, result_blob_uuid_obj,
                        now)
                    if success:
                        stats.record_migrated('AGENT_OUTPUT')
                    else:
                        stats.record_skipped()

        stats.show_progress('agent operation references')

    # --- Blobs: locations -> BLOB_LOCATION ---
    stats.add_category('BLOB_LOCATION')
    click.echo('\nScanning blobs for location references...')
    for objkey, _ in etcd.get_all('blob', None):
        blob_uuid = objkey.split('/')[-1]
        blob_uuid_obj = parse_uuid(blob_uuid, 'blob UUID')
        if not blob_uuid_obj:
            stats.record_error(f'Invalid blob UUID: {blob_uuid}')
            continue

        # Get locations attribute
        locations_data = etcd.get('attribute/blob', blob_uuid, 'locations')
        if not locations_data:
            continue

        locations = locations_data.get('locations', [])
        for node_name in locations:
            if dry_run:
                click.echo(
                    f'  Would create BLOB_LOCATION reference: '
                    f'node {node_name} -> blob {blob_uuid}')
                stats.record_migrated('BLOB_LOCATION')
            else:
                # Node UUIDs are node names (strings like "sf-1"), not UUIDs
                success = mariadb.record_relationship(
                    ObjectType.NODE, node_name,
                    RelationshipType.BLOB_LOCATION, None,
                    ObjectType.BLOB, blob_uuid_obj,
                    now)
                if success:
                    stats.record_migrated('BLOB_LOCATION')
                else:
                    stats.record_skipped()

        # Remove old locations attribute if not dry run
        if not dry_run:
            etcd.delete('attribute/blob', blob_uuid, 'locations')

        stats.show_progress('blob location references')

    stats.print_summary()
    migration_postcheck(dry_run)


cli.add_command(bootstrap_system_key)
cli.add_command(migrate_floating_network_uuid)
cli.add_command(migrate_state_to_mariadb)
cli.add_command(migrate_ipam_to_mariadb)
cli.add_command(migrate_uploads_to_mariadb)
cli.add_command(migrate_dnsmasq_to_mariadb)
cli.add_command(migrate_references_to_mariadb)
cli.add_command(show_etcd_config)
cli.add_command(set_etcd_config)
cli.add_command(verify_config)
cli.add_command(ensure_mariadb_schema)
cli.add_command(initialise_node)
cli.add_command(register_daemon)
cli.add_command(deregister_daemon)
cli.add_command(stop)
