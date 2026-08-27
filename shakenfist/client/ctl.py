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
from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc
from shakenfist_utilities import logs  # noreorder


LOG = logs.setup_console(__name__)

# setup_console() only attaches a handler to this module's logger, so the
# root logger needs a handler as well or log lines from every other module
# are dropped. Propagation is then disabled so this module's own lines are
# not emitted twice.
logging.basicConfig(level=logging.INFO)
logging.getLogger(__name__).propagate = False


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

            # Values may legitimately contain '=' (base64 padding, URLs
            # with query strings), so split on the first one only.
            key, value = line.split('=', 1)
            value = value.strip('\'"')

            if key not in os.environ:
                os.environ[key] = value

# We skip verifying the auth seed config setting here because we might be
# bootstrapping it.
sf_config = importlib.import_module('shakenfist.config')
sf_config.verify_config(skip_auth_seed=True)
config = sf_config.config

# These imports _must_ occur after the extra config setup has run.
from shakenfist import exceptions                          # noqa
from shakenfist import mariadb                             # noqa
from shakenfist.constants import EVENT_TYPE_AUDIT          # noqa
from shakenfist.namespace import Namespace                 # noqa
from shakenfist.node import Node                           # noqa
from shakenfist.schema.object_state import State           # noqa
from shakenfist.schema.object_types import ObjectType      # noqa
from shakenfist.util.grpc_channel import make_database_channel  # noqa
from shakenfist.util import vdi_tokens                     # noqa


@click.group()
@click.option('--verbose/--no-verbose', default=False)
@click.pass_context
def cli(ctx: click.Context, verbose: Optional[bool] = None) -> None:
    from shakenfist.util.caller_identity import set_caller_identity
    set_caller_identity('ctl')
    if verbose:
        LOG.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)


def _read_stdin_value() -> str:
    # Trailing newlines are stripped because callers (including ansible's
    # command module) routinely append one; other whitespace is preserved
    # in case it is part of the value.
    return click.get_text_stream('stdin').read().rstrip('\n')


@click.command()
@click.argument('keyname')
@click.argument('key', required=False)
@click.option('--key-from-stdin', is_flag=True, default=False,
              help='Read the key from stdin instead of an argument, keeping '
                   'it out of the process table.')
def bootstrap_system_key(keyname: str, key: Optional[str],
                         key_from_stdin: bool) -> None:
    if key_from_stdin == (key is not None):
        raise click.UsageError(
            'Provide the key either as an argument or via --key-from-stdin, '
            'but not both.')
    if key_from_stdin:
        key = _read_stdin_value()
    assert key is not None

    click.echo('Creating key %s' % keyname)
    ns = Namespace.new('system')
    ns.add_key(keyname, key)
    click.echo('Done')


@click.command(name='show-config')
@click.option('--show-secrets', is_flag=True, default=False,
              help='Include secret values instead of redacting them.')
def show_config(show_secrets: bool) -> None:
    """Show cluster-wide configuration, redacting secrets by default."""
    config_data = mariadb.get_cluster_config()
    if not show_secrets:
        for key in config_data:
            if sf_config.SECRET_CONFIG_KEY_RE.search(key):
                config_data[key] = '<redacted>'
    click.echo(json.dumps(config_data, indent=4, sort_keys=True))


@click.command(name='set-config')
@click.argument('flag')
@click.argument('value', required=False)
@click.option('--value-from-stdin', is_flag=True, default=False,
              help='Read the value from stdin instead of an argument, '
                   'keeping it out of the process table.')
def set_config(flag: str, value: Optional[str],
               value_from_stdin: bool) -> None:
    """Set a cluster-wide configuration value."""
    if value_from_stdin == (value is not None):
        raise click.UsageError(
            'Provide the value either as an argument or via '
            '--value-from-stdin, but not both.')
    if value_from_stdin:
        value = _read_stdin_value()
    assert value is not None

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

    if value_from_stdin or sf_config.SECRET_CONFIG_KEY_RE.search(flag):
        click.echo(f'Setting {flag} to {type(converted_value)}(<redacted>)')
    else:
        click.echo(
            f'Setting {flag} to {type(converted_value)}({converted_value})')
    mariadb.set_cluster_config(flag, converted_value)


@click.command(name='unset-config')
@click.argument('flag')
def unset_config(flag: str) -> None:
    """Delete a cluster-wide configuration value."""
    click.echo(f'Unsetting {flag}')
    mariadb.delete_cluster_config(flag)


@click.command()
def verify_config() -> None:
    sf_config.verify_config()
    click.echo('Configuration is ok')


@click.command()
def ensure_mariadb_schema() -> None:
    """Ensure the MariaDB schema exists and is up to date.

    This command should be run on a database node before
    initializing any nodes. It creates the required MariaDB tables if
    they don't already exist, and applies any pending schema migrations.
    Only nodes with direct MariaDB access (MARIADB_HOST configured) can
    run this command.
    """
    if not config.MARIADB_HOST:
        raise click.ClickException(
            'This command requires MARIADB_HOST to be configured. '
            'It should only be run on database nodes.')

    engine = mariadb._get_engine()
    try:
        mariadb.verify_mariadb_compat(engine)
    except exceptions.MariaDBIncompatibleError as e:
        raise click.ClickException(str(e))

    results = mariadb.ensure_schema()

    for r in results:
        if 'altered_columns' in r:
            if r['migrated']:
                click.echo('Widened ENUM column(s): '
                           f"{', '.join(r['altered_columns'])}")
            else:
                click.echo('Native ENUM columns are up to date')
        elif r['migrated']:
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


@click.command(name='ensure-kerbside-signing-key')
def ensure_kerbside_signing_key() -> None:
    """Ensure the Kerbside VDI console token signing key exists.

    Idempotent: safe to run repeatedly. The first invocation generates
    and stores a fresh signing key; every later invocation simply
    reports the existing active key id without changing anything.
    Never prints private key material.
    """
    material = vdi_tokens.ensure_signing_key()
    active_kid = material['active_kid']
    key_count = len(material['keys'])
    click.echo(f'Active kid: {active_kid}')
    click.echo(f'Published keys: {key_count}')


@click.command(name='rotate-kerbside-signing-key')
def rotate_kerbside_signing_key() -> None:
    """Rotate the Kerbside VDI console token signing key.

    Generates a fresh signing key and makes it active, keeping the
    previous key published so tokens already in flight still verify.
    Rotating twice in quick succession drops the third-oldest key,
    making any tokens it signed unverifiable. Never prints private key
    material.
    """
    material = vdi_tokens.rotate_signing_key()
    active_kid = material['active_kid']
    key_count = len(material['keys'])
    click.echo(f'New active kid: {active_kid}')
    click.echo(f'Published keys: {key_count}')


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
    a database node with direct database access).
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
    for bootstrapping from a database node with direct database access).
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


@click.command(name='clear-node-error')
@click.option('--node-name', default=None,
              help='Node to clear (defaults to NODE_NAME from config)')
def clear_node_error(node_name: Optional[str]) -> None:
    """Return a node from the error state to created.

    Node resource-health errors never clear automatically -- a marginal
    disk must not flap the node in and out of service -- so an operator
    runs this once the underlying storage is confirmed healthy. If the
    failure persists, sf-resources re-errors the node within one check
    interval.

    This returns only the node to service; any instances that were errored
    when the node failed stay terminal for the operator to snapshot or
    delete.
    """
    node_name = node_name or config.NODE_NAME
    n = Node.from_db(node_name)
    if not n:
        raise click.ClickException(f'Node "{node_name}" not found.')

    if n.state.value != Node.STATE_ERROR:
        raise click.ClickException(
            f'Node "{node_name}" is in state {n.state.value}, not error; '
            'nothing to clear.')

    n.add_event(EVENT_TYPE_AUDIT, 'operator cleared node error state')
    n.state = Node.STATE_CREATED  # type: ignore[misc]
    click.echo(f'Node "{node_name}" is now in state {n.state.value}.')


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


@click.command(name='gateway-health')
@click.option('--host', default=None,
              help='Gateway mesh IP to probe (defaults to NODE_MESH_IP).')
@click.option('--timeout', default=2, type=int,
              help='Health-check RPC deadline, in seconds.')
def gateway_health(host: Optional[str], timeout: int) -> None:
    """Check the local sf-database gRPC gateway reports SERVING.

    Exits zero when the gateway's grpc.health.v1 Check returns SERVING
    (which on sf-database means MariaDB is reachable and the schema is
    current), and raises -- a non-zero exit -- otherwise.

    This is the health gate for the database tier's rolling restart: the
    deploy waits on it after (re)starting sf-database so the next node in
    the serial roll is not taken down until this gateway is actually
    serving again, not merely listening on its port.
    """
    host = host or config.NODE_MESH_IP
    channel = make_database_channel([host], config.MARIADB_GATEWAY_PORT)
    try:
        stub = health_pb2_grpc.HealthStub(channel)
        # wait_for_ready lets a single call absorb the cold-connect window
        # (a freshly restarted gateway that is up but whose subchannel has
        # not connected yet); the RPC deadline still bounds the wait.
        resp = stub.Check(
            health_pb2.HealthCheckRequest(service=''),
            timeout=timeout, wait_for_ready=True)
    except Exception as e:
        raise click.ClickException(
            f'gateway {host} health check failed: {e}')
    finally:
        channel.close()

    status = health_pb2.HealthCheckResponse.ServingStatus.Name(resp.status)
    if resp.status != health_pb2.HealthCheckResponse.SERVING:
        raise click.ClickException(
            f'gateway {host} reports {status}, not SERVING')
    click.echo(f'gateway {host} is SERVING')


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
cli.add_command(unset_config)
cli.add_command(verify_config)
cli.add_command(ensure_mariadb_schema)
cli.add_command(ensure_kerbside_signing_key)
cli.add_command(rotate_kerbside_signing_key)
cli.add_command(initialise_node)
cli.add_command(register_daemon)
cli.add_command(deregister_daemon)
cli.add_command(clear_node_error)
cli.add_command(stop)
cli.add_command(gateway_health)
