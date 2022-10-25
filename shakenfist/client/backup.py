# Copyright 2020 Michael Still

import click
import importlib
import io
import logging
import os
from shakenfist_utilities import logs
import tarfile


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

sf_config = importlib.import_module('shakenfist.config')
config = sf_config.config

# These imports _must_ occur after the extra config setup has run.
from shakenfist import etcd                  # noqa


@click.group()
@click.option('--verbose/--no-verbose', default=False)
@click.pass_context
def cli(ctx, verbose=None):
    if verbose:
        LOG.setLevel(logging.DEBUG)


@click.command()
@click.argument('output', type=click.Path(exists=False))
@click.pass_context
def backup(ctx, output):
    with tarfile.open(output, 'w:gz') as tar:
        for data, metadata in etcd.WrappedEtcdClient().get_prefix('/'):
            info = tarfile.TarInfo(metadata['key'].decode('utf-8').rstrip('/'))
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


cli.add_command(backup)


@click.command()
@click.argument('input', type=click.Path(exists=True))
@click.pass_context
def restore(ctx, input):
    with tarfile.open(input, 'r:gz') as tar:
        for tarinfo in tar:
            key = tarinfo.name
            data = tar.extractfile(key).read()

            etcd.WrappedEtcdClient().put(key, data)


cli.add_command(restore)
