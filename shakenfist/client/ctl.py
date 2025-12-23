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
def initialise_node():
    # Ensure MariaDB schema exists before creating node state. This will raise
    # an error if MariaDB is not configured, which is intentional - MariaDB is
    # required for all deployments.
    mariadb.ensure_schema()

    click.echo(f'Initializing node "{config.NODE_NAME}" with mesh IP '
               f'{config.NODE_MESH_IP}...')
    n = Node.new(config.NODE_NAME, config.NODE_MESH_IP)
    click.echo(f'Node "{config.NODE_NAME}" is now in state {n.state.value}.')


@click.command()
@click.argument('daemon', nargs=-1)
def register_daemon(daemon):
    n = Node.from_db(config.NODE_NAME)
    if n is None:
        raise click.ClickException(
            f'Node "{config.NODE_NAME}" not found in database. '
            f'Run "sf-ctl initialise-node" first to create the node.')
    for d in daemon:
        click.echo(f'Registering {d} on node...')
        n.register_daemon(d)
        click.echo(f'Daemon is now in state {n.get_daemon_state(d).value}.')
    click.echo(f'Node is now in state {n.state.value}.')


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


# All object types that have state stored in etcd
OBJECT_TYPES_WITH_STATE = [
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


cli.add_command(bootstrap_system_key)
cli.add_command(migrate_state_to_mariadb)
cli.add_command(show_etcd_config)
cli.add_command(set_etcd_config)
cli.add_command(verify_config)
cli.add_command(initialise_node)
cli.add_command(register_daemon)
cli.add_command(deregister_daemon)
cli.add_command(stop)
