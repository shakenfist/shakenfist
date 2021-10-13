# Copyright 2020 Michael Still

import base64
import bcrypt
import click
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
cli.add_command(stop)
