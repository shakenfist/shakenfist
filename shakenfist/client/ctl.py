# Copyright 2020 Michael Still
import importlib
import json
import logging
import os
import time
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
from shakenfist.schema import database_load_budget         # noqa
from shakenfist.util import metrics_scrape                 # noqa
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


def _gather_database_load(window: int, timeout: int) -> dict[str, Any]:
    """Scrape the tier twice and turn the difference into rates.

    Returns the rates, plus which gateways answered, because a partial
    measurement reported as a whole one is worse than no measurement: it
    reads as load having fallen.
    """
    # Deduplicated, order preserved. A host listed twice is one gateway,
    # and scraping it twice per sample costs a second round trip inside
    # the measured window for a reading which is then thrown away --
    # everything below is keyed by host.
    hosts = list(dict.fromkeys(config.MARIADB_GATEWAY_HOSTS))
    if not hosts:
        raise click.ClickException(
            'MARIADB_GATEWAY_HOSTS is empty, so there is no database tier '
            'to measure.')
    port = config.MARIADB_GATEWAY_METRICS_PORT

    # A counter delta covers the time between the two reads of that
    # counter, which is the sleep plus however long the scrapes took --
    # not the sleep. Timing each gateway individually and dividing by
    # what it actually measured keeps a slow or timing-out gateway from
    # inflating every rate it contributes to: at the default 5s timeout,
    # a handful of gateways is already several percent of a 60s window,
    # and the numbers here are compared against a budget.
    def _sample() -> tuple[
            dict[str, tuple[dict[tuple[str, str], float], float]],
            dict[str, str]]:
        reached: dict[str, tuple[dict[tuple[str, str], float], float]] = {}
        failed: dict[str, str] = {}
        for host in hosts:
            try:
                pairs = metrics_scrape.scrape_request_pairs(
                    host, port, timeout=timeout)
                reached[host] = (pairs, time.monotonic())
            except Exception as e:
                failed[host] = str(e)
        return reached, failed

    first, first_failed = _sample()
    if not first:
        raise click.ClickException(
            'No sf-database gateway answered on port %d. Tried: %s'
            % (port, ', '.join('%s (%s)' % (h, e)
                               for h, e in sorted(first_failed.items()))))

    time.sleep(window)
    second, second_failed = _sample()

    # Only gateways which answered both times can contribute a rate. One
    # which answered once would otherwise look like a counter which reset.
    usable = sorted(set(first) & set(second))
    unreachable = sorted(set(hosts) - set(usable))

    rates: dict[tuple[str, str], float] = {}
    elapsed_by_host: dict[str, float] = {}
    for host in usable:
        before, before_at = first[host]
        after, after_at = second[host]
        elapsed = after_at - before_at
        elapsed_by_host[host] = round(elapsed, 3)
        if elapsed <= 0:
            # Cannot happen with a monotonic clock and a sleep between
            # the samples, but a zero here would divide by zero rather
            # than report a rate, so say so instead.
            continue
        for key, value in after.items():
            delta = value - before.get(key, 0.0)
            if delta < 0:
                # sf-database restarted inside the window, so its counters
                # began again from zero. Its share of this pair is unknown
                # rather than negative.
                continue
            rates[key] = rates.get(key, 0.0) + delta / elapsed

    return {
        'rates': rates,
        'gateways_measured': usable,
        'gateways_unreachable': unreachable,
        'gateway_elapsed_seconds': elapsed_by_host,
        'gateway_errors': {h: second_failed.get(h, first_failed.get(h, ''))
                           for h in unreachable},
    }


# sf-resources republishes every node's metrics row each cycle and only
# clears its own at startup, so a node which has left the cluster leaves
# a row behind. Prometheus drops such a series once it goes stale and the
# reader below has to do the same, or the per-node term keeps charging
# for a node which is gone. Generous against the 60s publish interval,
# and the same 5 minutes Prometheus looks back by default.
NODE_METRICS_STALE_SECONDS = 300


def _cluster_shape() -> tuple[int, int]:
    """How many nodes, and how many instances are standing.

    Both numbers come from the metrics sf-resources publishes, because
    that is the series the budget's coefficients were fitted against:
    per_instance_qps is a regression against ``sum(instances_active)``,
    which counts running libvirt domains, and the generated Prometheus
    rules evaluate the same model with ``count(instances_active)`` and
    ``sum(instances_active)``. Counting instances in the created state
    instead would include powered off ones, which cost the database
    nothing -- and since the ceiling is a multiple of the modelled
    value, a cluster with many of them would quietly raise its own
    ceiling by about 0.5/s per instance on the larger pairs, which is
    room enough to hide the regression this command exists to find.
    """
    now = time.time()
    fresh = [m for m in mariadb.get_all_node_metrics()  # nopushdown: every node wanted
             if now - float(m.get('timestamp') or 0)
             < NODE_METRICS_STALE_SECONDS]
    if not fresh:
        raise click.ClickException(
            'No node has published resource metrics in the last %d '
            'seconds, so the shape of this cluster is unknown and the '
            'model below cannot be evaluated. Is sf-resources running?'
            % NODE_METRICS_STALE_SECONDS)

    instances = sum(int((m.get('metrics') or {}).get('instances_active', 0))
                    for m in fresh)
    return len(fresh), instances


@click.command(name='database-load')
@click.option('--window', default=60, type=int,
              help='Seconds to measure over. Shorter is noisier.')
@click.option('--timeout', default=5, type=int,
              help='Per-gateway scrape timeout, in seconds.')
@click.option('--all-pairs', is_flag=True, default=False,
              help='Print the quiet pairs nobody has budgeted too. '
                   '"--json" always includes them.')
@click.option('--json', 'as_json', is_flag=True, default=False,
              help='Emit JSON rather than a table.')
def database_load(window: int, timeout: int, all_pairs: bool,
                  as_json: bool) -> None:
    """Compare this cluster's database load against what it should be.

    Scrapes every sf-database gateway twice, and reports per caller what
    the tier is actually serving next to what Shaken Fist's shipped load
    model predicts for a cluster of this size. Sorted by how far over
    budget each is, because that is what matters -- the busiest pair on
    any cluster is usually a poll doing exactly what it should.

    Load here is mostly polling, and polling rates are set by how many
    things exist rather than by how much work anybody is doing, so the
    model is a per-node base plus a per-standing-instance coefficient
    rather than a flat number. If a pair is well over its budget, please
    report it with "--json" output attached.

    Two kinds of entry are printed but never counted as over budget.
    "provisional" means Shaken Fist has an open bug about that pair and
    its current level is not a floor worth defending. "activity" means
    the level is set by what you and your tooling do rather than by one
    of our loops, so only you can say whether it is reasonable.

    Most pairs a cluster serves have no budget entry at all, because they
    are activity driven and near zero at idle -- around 300 of them on a
    cluster like the one the model was derived from. The table leaves
    those out unless they are over the unbudgeted ceiling or
    "--all-pairs" is given, since a table nobody reads to the end is no
    better than no table. "--json" always carries every pair.
    """
    budget = database_load_budget.load_budget()
    defaults = budget.defaults

    measurement = _gather_database_load(window, timeout)
    nodes, instances = _cluster_shape()
    rates = measurement['rates']

    rows = []
    for (operation, caller), measured in rates.items():
        entry = budget.get(operation, caller)
        if entry is None:
            modelled = None
            ceiling = defaults.unbudgeted_ceiling_qps(nodes)
            flags = ['unbudgeted']
        else:
            modelled = entry.expected_qps(nodes, instances)
            ceiling = entry.ceiling_qps(nodes, instances,
                                        defaults.tolerance_multiplier,
                                        defaults.tolerance_floor_qps)
            flags = []
            if entry.provisional:
                flags.append('provisional:#%d' % entry.provisional.issue)
            if entry.activity_coupled:
                flags.append('activity')
        rows.append({
            'operation': operation,
            'caller_daemon': caller,
            'measured_qps': round(measured, 3),
            'modelled_qps': None if modelled is None else round(modelled, 3),
            'ceiling_qps': round(ceiling, 3),
            'excess_qps': round(measured - ceiling, 3),
            # An unbudgeted pair has no entry to ask about enforcement,
            # but it does have a ceiling -- and exceeding it is exactly
            # the new-poll case ShakenFistUnbudgetedDatabasePolling
            # alerts on and test_no_unbudgeted_fixed_rate_database_polling
            # fails the build on. Reading `entry is not None` here made
            # this the one consumer of the three which counted it as
            # fine, and then printed "nothing is over budget" over the
            # top of it.
            'over_budget': measured > ceiling and (
                entry is None or entry.enforced),
            'flags': flags,
        })

    rows.sort(key=lambda r: -r['excess_qps'])

    # A budgeted pair carrying a flag is worth showing whatever its rate:
    # "provisional" and "activity" are things the reader has to know
    # about their own numbers. "unbudgeted" is not, on its own -- almost
    # every pair on any cluster is unbudgeted and quiet, and printing all
    # of them buries the handful of rows this command exists to surface.
    shown = rows if all_pairs else [
        r for r in rows
        if r['excess_qps'] > 0 or (r['flags']
                                   and 'unbudgeted' not in r['flags'])]
    hidden = len(rows) - len(shown)

    # Counted apart because the two carry different weights of evidence.
    # A budgeted pair over its ceiling is measured against a model of this
    # cluster and is worth an issue. An unbudgeted pair over the
    # new-poll threshold in one short window is worth a longer look
    # first: the Prometheus alert for the same thing wants an hour of it,
    # and the CI check wants the same rate in two consecutive windows,
    # because a burst of ordinary work looks identical over one.
    budgeted_over = [r for r in rows
                     if r['over_budget'] and 'unbudgeted' not in r['flags']]
    unbudgeted_over = [r for r in rows
                       if r['over_budget'] and 'unbudgeted' in r['flags']]

    report = {
        'window_seconds': window,
        'measured_seconds': measurement['gateway_elapsed_seconds'],
        'cluster': {'nodes': nodes, 'standing_instances': instances},
        'gateways_measured': measurement['gateways_measured'],
        'gateways_unreachable': measurement['gateways_unreachable'],
        'gateway_errors': measurement['gateway_errors'],
        'total_measured_qps': round(sum(rates.values()), 3),
        'total_modelled_qps': round(
            budget.expected_total_qps(nodes, instances), 3),
        # Every pair, whatever the table is about to print. This output
        # is what a deployer attaches to an issue, and an attachment
        # somebody has to be asked to re-run with another flag is worse
        # than a long one.
        'pairs': rows,
        'pairs_seen': len(rows),
        'pairs_hidden_from_table': hidden,
        'pairs_over_budget': len(budgeted_over) + len(unbudgeted_over),
        'budgeted_pairs_over_budget': len(budgeted_over),
        'unbudgeted_pairs_over_ceiling': len(unbudgeted_over),
    }

    if as_json:
        click.echo(json.dumps(report, indent=4, sort_keys=True))
        return

    if measurement['gateways_unreachable']:
        click.echo(
            'WARNING: %d of %d gateways did not answer, so these numbers '
            'are the tier minus those gateways, not the whole tier:'
            % (len(measurement['gateways_unreachable']),
               len(measurement['gateways_unreachable'])
               + len(measurement['gateways_measured'])))
        for host in measurement['gateways_unreachable']:
            click.echo('  %s: %s' % (host, measurement['gateway_errors'][host]))
        click.echo('')

    click.echo('%d nodes, %d standing instances, measured over %ds across '
               '%d gateway(s).'
               % (nodes, instances, window,
                  len(measurement['gateways_measured'])))
    click.echo('Total %.1f/s measured against %.1f/s modelled.'
               % (report['total_measured_qps'],
                  report['total_modelled_qps']))
    click.echo('')

    if not shown:
        click.echo('Every pair is inside its budget.')
        if hidden:
            click.echo('%d quiet pair(s) with no budget entry were not '
                       'shown. Use --all-pairs to see them.' % hidden)
        return

    click.echo('%-34s %-13s %9s %9s %9s  %s'
               % ('OPERATION', 'CALLER', 'MEASURED', 'MODELLED', 'EXCESS',
                  'NOTES'))
    for row in shown:
        modelled_text = ('        -' if row['modelled_qps'] is None
                         else '%9.2f' % row['modelled_qps'])
        click.echo('%-34s %-13s %9.2f %s %9.2f  %s'
                   % (row['operation'], row['caller_daemon'],
                      row['measured_qps'], modelled_text,
                      row['excess_qps'],
                      ','.join(row['flags'])))

    click.echo('')
    if hidden:
        click.echo('%d quiet pair(s) with no budget entry were not shown. '
                   'Use --all-pairs to see them.' % hidden)
    if budgeted_over:
        click.echo(
            '%d budgeted pair(s) are above what the model allows for a '
            'cluster of this shape. Please report this at '
            'https://github.com/shakenfist/shakenfist/issues with the '
            'output of "sf-ctl database-load --json" attached.'
            % len(budgeted_over))
    if unbudgeted_over:
        click.echo(
            '%d pair(s) with no budget entry ran above %.2f/s across this '
            'one %ds window, which is what a new polling loop looks like '
            '-- and also what a burst of ordinary work looks like over a '
            'window this short. Re-run with a longer --window, and report '
            'it if it holds.'
            % (len(unbudgeted_over), defaults.unbudgeted_ceiling_qps(nodes),
               window))
    if not budgeted_over and not unbudgeted_over:
        click.echo('Nothing is over budget; the rows above are flagged '
                   'rather than excessive.')


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
cli.add_command(database_load)
