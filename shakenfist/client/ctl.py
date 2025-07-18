# Copyright 2020 Michael Still
import datetime
import importlib
import json
import logging
import os

import click
from shakenfist_utilities import logs  # noreorder


LOG = logs.setup_console(__name__)


# Utilities not started by systemd need to load /etc/sf/config to ensure
# that they are correctly configured
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

            os.environ[key] = value

# We skip verifying the auth seed config setting here because we might be
# bootstrapping it.
sf_config = importlib.import_module('shakenfist.config')
sf_config.verify_config(skip_auth_seed=True)
config = sf_config.config

# These imports _must_ occur after the extra config setup has run.
from shakenfist.cache import refresh_object_state_caches   # noqa
from shakenfist.daemons.cluster.scheduled_tasks \
    import _fill_per_deleted_object_queue                  # noqa
from shakenfist.daemons.cluster.scheduled_tasks \
    import _process_per_deleted_object_queue               # noqa
from shakenfist.daemons.cluster.scheduled_tasks \
    import DELETED_OBJECTS_QUEUE                           # noqa
from shakenfist import etcd                                # noqa
from shakenfist.namespace import Namespace                 # noqa
from shakenfist.node import Node                           # noqa


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
    click.echo('Initializing node...')
    n = Node.new(config.NODE_NAME, config.NODE_MESH_IP)
    click.echo(f'Node is now in state {n.state.value}.')


@click.command()
@click.argument('daemon', nargs=-1)
def register_daemon(daemon):
    n = Node.from_db(config.NODE_NAME)
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


@click.command()
def rebuild_etcd_caches():
    click.echo(f'{datetime.datetime.now()} Hard deleting objects...')
    _fill_per_deleted_object_queue()
    click.echo(f'{datetime.datetime.now()}     '
               f'Built list of {DELETED_OBJECTS_QUEUE.qsize()} objects')
    while DELETED_OBJECTS_QUEUE.qsize() > 0:
        processed = _process_per_deleted_object_queue(10)
        click.echo(f'{datetime.datetime.now()}     '
                   f'Processed {processed} objects, '
                   f'{DELETED_OBJECTS_QUEUE.qsize()} objects remaining')

    click.echo(f'{datetime.datetime.now()} Rebuilding caches...')
    refresh_object_state_caches()
    click.echo(f'{datetime.datetime.now()} etcd caches rebuilt.')


cli.add_command(bootstrap_system_key)
cli.add_command(show_etcd_config)
cli.add_command(set_etcd_config)
cli.add_command(verify_config)
cli.add_command(initialise_node)
cli.add_command(register_daemon)
cli.add_command(deregister_daemon)
cli.add_command(stop)
cli.add_command(rebuild_etcd_caches)
