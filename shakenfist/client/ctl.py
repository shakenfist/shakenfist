# Copyright 2020 Michael Still
import importlib
import json
import logging
import os
import uuid as uuid_module
from dataclasses import dataclass
from dataclasses import field
from typing import Any
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
    categories: dict[str, int] = field(default_factory=dict)
    progress_interval: int = 100

    def add_category(self, name: str) -> None:
        """Add a category to track separately."""
        self.categories[name] = 0

    def record_migrated(self, category: Optional[str] = None) -> None:
        """Record a successful migration."""
        self.migrated += 1
        if category and category in self.categories:
            self.categories[category] += 1

    def record_skipped(self) -> None:
        """Record a skipped item (already exists)."""
        self.skipped += 1

    def record_error(self, message: str) -> None:
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

    def show_progress(self, object_type: str = 'items') -> None:
        """Show progress if interval reached."""
        if self.should_show_progress():
            click.echo(f'  ... {self.total_processed} {object_type} processed')

    def print_summary(self) -> None:
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


def migration_postcheck(dry_run: bool) -> None:
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
from shakenfist import database as sf_database             # noqa
from shakenfist import mariadb                             # noqa
from shakenfist.namespace import Namespace                 # noqa
from shakenfist.node import Node                           # noqa
from shakenfist.schema.object_state import State           # noqa
from shakenfist.schema.object_types import ObjectType      # noqa


@click.group()
@click.option('--verbose/--no-verbose', default=False)
@click.pass_context
def cli(ctx: click.Context, verbose: Optional[bool] = None) -> None:
    if verbose:
        LOG.setLevel(logging.DEBUG)


@click.command()
@click.argument('keyname')
@click.argument('key')
def bootstrap_system_key(keyname: str, key: str) -> None:
    click.echo('Creating key %s' % keyname)
    ns = Namespace.new('system')
    ns.add_key(keyname, key)
    click.echo('Done')


@click.command(name='show-config')
def show_config() -> None:
    """Show cluster-wide configuration."""
    config_data = sf_database.get_cluster_config()
    click.echo(json.dumps(config_data, indent=4, sort_keys=True))


@click.command(name='set-config')
@click.argument('flag')
@click.argument('value')
def set_config(flag: str, value: str) -> None:
    """Set a cluster-wide configuration value."""
    # Convert values if possible
    converted_value: Any = value
    if value in ['t', 'true', 'True']:
        converted_value = True
    elif value in ['f', 'false', 'False']:
        converted_value = False
    else:
        try:
            if value.find('.') != -1:
                converted_value = float(value)
            else:
                converted_value = int(value)
        except ValueError:
            pass

    click.echo(f'Setting {flag} to {type(converted_value)}({converted_value})')
    sf_database.set_cluster_config(flag, converted_value)


# Backward compatibility aliases
@click.command(name='show-etcd-config', hidden=True)
def show_etcd_config() -> None:
    """Deprecated: use show-config instead."""
    config_data = sf_database.get_cluster_config()
    click.echo(json.dumps(config_data, indent=4, sort_keys=True))


@click.command(name='set-etcd-config', hidden=True)
@click.argument('flag')
@click.argument('value')
def set_etcd_config(flag: str, value: str) -> None:
    """Deprecated: use set-config instead."""
    ctx = click.get_current_context()
    ctx.invoke(set_config, flag=flag, value=value)


@click.command()
def verify_config() -> None:
    sf_config.verify_config()
    click.echo('Configuration is ok')


@click.command()
def ensure_mariadb_schema() -> None:
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
def initialise_node(node_name: Optional[str], node_mesh_ip: Optional[str]) -> None:
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
def register_daemon(daemon: tuple[str, ...], node_name: Optional[str]) -> None:
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
def deregister_daemon(daemon: tuple[str, ...]) -> None:
    n = Node.from_db(config.NODE_NAME)
    if not n:
        raise click.ClickException(
            f'Node "{config.NODE_NAME}" not found.')
    for d in daemon:
        click.echo(f'Deregistering {d} on node...')
        n.deregister_daemon(d)
    click.echo(f'Node is now in state {n.state.value}.')


@click.command()
@click.argument('daemon')
def stop(daemon: str) -> None:
    click.echo(
        f'Gracefully stopping Shaken Fist {daemon} daemon '
        f'on this node...')
    n = Node.from_db(config.NODE_NAME)
    if not n:
        raise click.ClickException(
            f'Node "{config.NODE_NAME}" not found.')

    # If we were missing, we're not any more
    if n.state.value == Node.STATE_MISSING:
        n.state = Node.STATE_DEGRADED  # type: ignore[misc]

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


cli.add_command(bootstrap_system_key)
cli.add_command(show_config)
cli.add_command(set_config)
cli.add_command(show_etcd_config)
cli.add_command(set_etcd_config)
cli.add_command(verify_config)
cli.add_command(ensure_mariadb_schema)
cli.add_command(initialise_node)
cli.add_command(register_daemon)
cli.add_command(deregister_daemon)
cli.add_command(stop)
