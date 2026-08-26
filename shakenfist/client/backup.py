# Copyright 2020 Michael Still
import importlib
import logging
import os

import click
from shakenfist_utilities import logs  # noreorder


LOG = logs.setup_console(__name__)

# setup_console() only attaches a handler to this module's logger, so the
# root logger needs a handler as well or log lines from every other module
# are dropped. Propagation is then disabled so this module's own lines are
# not emitted twice.
logging.basicConfig(level=logging.INFO)
logging.getLogger(__name__).propagate = False


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

sf_config = importlib.import_module('shakenfist.config')
config = sf_config.config


@click.group()
@click.option('--verbose/--no-verbose', default=False)
@click.pass_context
def cli(ctx, verbose=None):
    if verbose:
        LOG.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)


@click.command()
@click.argument('output', type=click.Path(exists=False))
@click.option('-a', '--anonymise', is_flag=True,
              help='Remove authentication details from backup')
@click.pass_context
def backup(ctx, output, anonymise=False):
    click.echo(
        'sf-backup has not yet been reimplemented against MariaDB. '
        'Use mariadb-dump for now.')


cli.add_command(backup)


@click.command()
@click.argument('input', type=click.Path(exists=True))
@click.pass_context
def restore(ctx, input):
    click.echo(
        'sf-backup has not yet been reimplemented against MariaDB. '
        'Use mariadb restore tooling for now.')


cli.add_command(restore)
