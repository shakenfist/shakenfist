# Copyright 2020 Michael Still

import click
import io
import logging
import tarfile

from shakenfist import etcd

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
@click.argument('output', type=click.Path(exists=False))
@click.pass_context
def backup(ctx, output):
    with tarfile.open(output, 'w:gz') as tar:
        for data, metadata in etcd.Etcd3Client().get_prefix('/'):
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

            etcd.Etcd3Client().put(key, data)


cli.add_command(restore)
