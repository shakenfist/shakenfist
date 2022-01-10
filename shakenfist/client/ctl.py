# Copyright 2020 Michael Still

import base64
import bcrypt
import click
from etcd3gw.client import Etcd3Client
import json
import logging
import time

from shakenfist.config import config
from shakenfist import db
from shakenfist.node import Node


logging.basicConfig(level=logging.INFO)

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)


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

    encoded = str(base64.b64encode(bcrypt.hashpw(
        key.encode('utf-8'), bcrypt.gensalt())), 'utf-8')

    db.persist_namespace('system',
                         {
                             'name': 'system',
                             'keys': {
                                 keyname: encoded
                             }
                         })
    click.echo('Done')


@click.command()
def show_etcd_config():
    value = Etcd3Client(
        host='localhost', port=2379, protocol='http', api_path='/v3beta/').get(
            '/sf/config', metadata=True)
    if value is None or len(value) == 0:
        click.echo('{}')
    else:
        click.echo(json.dumps(json.loads(
            value[0][0]), indent=4, sort_keys=True))


@click.command()
@click.argument('flag')
@click.argument('value')
def set_etcd_config(flag, value):
    client = Etcd3Client(host='localhost', port=2379, protocol='http',
                         api_path='/v3beta/')
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

    click.echo('Setting %s to %s(%s)' % (flag, type(value), value))
    config[flag] = value
    client.put('/sf/config', json.dumps(config, indent=4, sort_keys=True))


@click.command()
def stop():
    click.echo('Gracefully stopping Shaken Fist on this node...')
    n = Node.from_db(config.NODE_NAME)
    n.state = Node.STATE_STOPPING
    click.echo('Placed node in stopping state')

    while n.state.value != Node.STATE_STOPPED:
        click.echo('Waiting for Shaken Fist to stop...')
        time.sleep(5)

    click.echo('Node is now stopped')


cli.add_command(bootstrap_system_key)
cli.add_command(show_etcd_config)
cli.add_command(set_etcd_config)
cli.add_command(stop)
