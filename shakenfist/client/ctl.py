# Copyright 2020 Michael Still
import importlib
import json
import logging
import os

import click
from shakenfist_utilities import logs  # noreorder


LOG = logs.setup_console(__name__)


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
    # Ensure the MariaDB schema exists. This will raise an error if MariaDB
    # is not configured.
    if not dry_run:
        click.echo('Ensuring MariaDB schema exists...')
        mariadb.ensure_schema()

    total_migrated = 0
    total_skipped = 0

    for object_type in OBJECT_TYPES_WITH_STATE:
        click.echo(f'\nMigrating {object_type} objects...')
        migrated = 0
        skipped = 0

        # Iterate through all objects of this type
        for objkey, _ in etcd.get_all(object_type, None):
            # Extract UUID from the etcd key
            objuuid = objkey.split('/')[-1]

            # Get state from etcd
            state_data = etcd.get(f'attribute/{object_type}', objuuid, 'state')
            if not state_data:
                skipped += 1
                continue

            if dry_run:
                click.echo(f'  Would migrate {objuuid}: {state_data.get("value")}')
            else:
                # Write to MariaDB
                state = State(**state_data)
                mariadb.set_state(object_type, objuuid, state)

                # Remove from etcd
                etcd.delete(f'attribute/{object_type}', objuuid, 'state')

            migrated += 1

            # Show progress every 100 objects
            if migrated % 100 == 0:
                click.echo(f'  ... {migrated} objects processed')

        click.echo(f'  {object_type}: {migrated} migrated, {skipped} skipped')
        total_migrated += migrated
        total_skipped += skipped

    click.echo(f'\nTotal: {total_migrated} objects migrated, '
               f'{total_skipped} objects skipped (no state)')

    if dry_run:
        click.echo('\nThis was a dry run. No changes were made.')
    else:
        click.echo('\nMigration complete. You can now start Shaken Fist services.')


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

    # Ensure the MariaDB schema exists
    if not dry_run:
        click.echo('Ensuring MariaDB schema exists...')
        mariadb.ensure_schema()

    total_migrated = 0
    total_skipped = 0
    total_errors = 0

    click.echo('\nScanning for IPAM reservations in etcd...')

    # Get all IPAM reservation paths from etcd
    # The path format is /sf/ipam_reservations/{ipam_uuid}/{address}
    for key, data in etcd.get_prefix_raw('/sf/ipam_reservations/'):
        # Parse the key to extract ipam_uuid and address
        # Key format: /sf/ipam_reservations/{ipam_uuid}/{address}
        parts = key.split('/')
        if len(parts) < 5:
            click.echo(f'  Skipping invalid key: {key}')
            total_skipped += 1
            continue

        ipam_uuid = parts[3]
        address = parts[4]

        if dry_run:
            res_type = data.get('type', 'unknown')
            click.echo(f'  Would migrate {ipam_uuid}/{address}: {res_type}')
            total_migrated += 1
            continue

        try:
            # Convert legacy data to IPAMReservation
            reservation = IPAMReservation.from_legacy_dict(ipam_uuid, data)

            # Write to MariaDB - use direct access since we're in ctl
            success = mariadb._direct_reserve_address(reservation)
            if success:
                # Remove from etcd
                etcd.delete_raw(key)
                total_migrated += 1
            else:
                # Address already exists in MariaDB
                click.echo(f'  Skipping {ipam_uuid}/{address}: already in MariaDB')
                # Still remove from etcd since the data is in MariaDB
                etcd.delete_raw(key)
                total_skipped += 1
        except Exception as e:
            click.echo(f'  Error migrating {ipam_uuid}/{address}: {e}')
            total_errors += 1

        # Show progress every 100 reservations
        if (total_migrated + total_skipped + total_errors) % 100 == 0:
            click.echo(
                f'  ... {total_migrated + total_skipped + total_errors} '
                'reservations processed')

    click.echo(f'\nTotal: {total_migrated} migrated, {total_skipped} skipped, '
               f'{total_errors} errors')

    if dry_run:
        click.echo('\nThis was a dry run. No changes were made.')
    else:
        click.echo('\nMigration complete. You can now start Shaken Fist services.')


cli.add_command(bootstrap_system_key)
cli.add_command(migrate_state_to_mariadb)
cli.add_command(migrate_ipam_to_mariadb)
cli.add_command(show_etcd_config)
cli.add_command(set_etcd_config)
cli.add_command(verify_config)
cli.add_command(ensure_mariadb_schema)
cli.add_command(initialise_node)
cli.add_command(register_daemon)
cli.add_command(deregister_daemon)
cli.add_command(stop)
