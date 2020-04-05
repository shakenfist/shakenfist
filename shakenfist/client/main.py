# Copyright 2020 Michael Still

import click
import datetime
import logging
import os
from prettytable import PrettyTable
import sys
import time
import uuid

from shakenfist.client import apiclient


logging.basicConfig(level=logging.INFO)

LOG = logging.getLogger(__file__)
LOG.setLevel(logging.INFO)


CLIENT = apiclient.Client()


@click.group()
@click.option('--pretty/--no-pretty', default=True)
@click.option('--verbose/--no-verbose', default=False)
@click.pass_context
def cli(ctx, pretty, verbose):
    if not ctx.obj:
        ctx.obj = {}
    ctx.obj['PRETTY'] = pretty

    if verbose:
        LOG.setLevel(logging.DEBUG)

        global CLIENT
        CLIENT = apiclient.Client(verbose=True)


@click.group(help='Node commands')
def node():
    pass


def _get_networks(ctx, args, incomplete):
    for n in CLIENT.get_networks():
        yield n['uuid']


@node.command(name='list', help='List nodes')
@click.pass_context
def node_list(ctx):
    nodes = list(CLIENT.get_nodes())

    if ctx.obj['PRETTY']:
        x = PrettyTable()
        x.field_names = ['name', 'ip', 'lastseen']
        for n in nodes:
            x.add_row([n['name'], n['ip'], n['lastseen']])
        print(x)

    else:
        print('name,ip,lastseen')
        for n in nodes:
            print('%s,%s,%s' % (n['name'], n['ip'], n['lastseen']))


cli.add_command(node)


@click.group(help='Network commands')
def network():
    pass


def _get_networks(ctx, args, incomplete):
    for n in list(CLIENT.get_networks()):
        yield n['uuid']


@network.command(name='list', help='List networks')
@click.pass_context
def network_list(ctx):
    nets = list(CLIENT.get_networks())

    if ctx.obj['PRETTY']:
        x = PrettyTable()
        x.field_names = ['uuid', 'owner', 'netblock']
        for n in nets:
            x.add_row([n['uuid'], n['owner'], n['netblock']])
        print(x)

    else:
        print('uuid,owner,netblock')
        for n in nets:
            print('%s,%s,%s' % (n['uuid'], n['owner'], n['netblock']))


def _show_network(n):
    if not n:
        print('Network not found')
        sys.exit(1)

    print('%-12s: %s' % ('uuid', n['uuid']))
    print('%-12s: %s' % ('vxlan id', n['vxid']))
    print('%-12s: %s' % ('netblock', n['netblock']))
    print('%-12s: %s' % ('provide dhcp', n['provide_dhcp']))
    print('%-12s: %s' % ('provide nat', n['provide_nat']))
    print('%-12s: %s' % ('owner', n['owner']))


@network.command(name='show', help='Show a network')
@click.argument('uuid', type=click.STRING, autocompletion=_get_networks)
@click.pass_context
def network_show(ctx, uuid=None):
    _show_network(CLIENT.get_network(uuid))


@network.command(name='create',
                 help=('Create a network.\n\n'
                       'NETBLOCK:         The IP address block to use, as a CIDR\n'
                       '                  range -- for example 192.168.200.1/24\n'
                       '--dhcp/--no-dhcp: Should this network have DCHP?\n'
                       '--nat/--no-nat:   Should this network be able to access'
                       '                  the Internet via NAT?'))
@click.argument('netblock', type=click.STRING)
@click.option('--dhcp/--no-dhcp', default=True)
@click.option('--nat/--no-nat', default=True)
@click.pass_context
def network_create(ctx, netblock=None, dhcp=None, nat=None):
    _show_network(CLIENT.allocate_network(netblock, dhcp, nat))


@network.command(name='delete', help='Delete a network')
@click.argument('uuid', type=click.STRING, autocompletion=_get_networks)
@click.pass_context
def network_delete(ctx, uuid=None):
    CLIENT.delete_network(uuid)


cli.add_command(network)


@click.group(help='Instance commands')
def instance():
    pass


def _get_instances(ctx, args, incomplete):
    for i in CLIENT.get_instances():
        yield i['uuid']


@instance.command(name='list', help='List instances')
@click.pass_context
def instance_list(ctx):
    insts = list(CLIENT.get_instances())

    if ctx.obj['PRETTY']:
        x = PrettyTable()
        x.field_names = ['uuid', 'name', 'cpus', 'memory', 'hypervisor']
        for i in insts:
            x.add_row([i['uuid'], i['name'], i['cpus'], i['memory'], i['node']])
        print(x)

    else:
        print('uuid,name,cpus,memory,hypervisor')
        for i in insts:
            print('%s,%s,%s,%s,%s' %
                  (i['uuid'], i['name'], i['cpus'], i['memory'], i['node']))


def _show_instance(i):
    if not i:
        print('Instance not found')
        sys.exit(1)

    print('%-12s: %s' % ('uuid', i['uuid']))
    print('%-12s: %s' % ('name', i['name']))
    print('%-12s: %s' % ('cpus', i['cpus']))
    print('%-12s: %s' % ('memory', i['memory']))
    print('%-12s: %s' % ('disk spec', i['disk_spec']))
    print('%-12s: %s' % ('ssh key', i['ssh_key']))
    print('%-12s: %s' % ('hypervisor', i['node']))

    # NOTE(mikal): I am not sure we should expose this, but it will do
    # for now until a proxy is written.
    print('%-12s: %s' % ('console port', i['console_port']))

    print()
    print('Interfaces:')
    for interface in CLIENT.get_instance_interfaces(i['uuid']):
        print()
        print('    %-8s: %s' % ('uuid', interface['uuid']))
        print('    %-8s: %s' % ('network', interface['network_uuid']))
        print('    %-8s: %s' % ('macaddr', interface['macaddr']))
        print('    %-8s: %s' % ('ipv4', interface['ipv4']))


@instance.command(name='show', help='Show an instance')
@click.argument('uuid', type=click.STRING, autocompletion=_get_instances)
@click.pass_context
def instance_show(ctx, uuid=None):
    _show_instance(CLIENT.get_instance(uuid))


@instance.command(name='create',
                  help=('Create an instance.\n\n'
                        'NAME:      The name of the instance.\n'
                        'CPUS:      The number of vCPUs for the instance.\n'
                        'MEMORY:    The amount RAM for the instance in GB.\n'
                        '\n'
                        'Options (may be repeated, must be specified at least once):\n'
                        '--network: The uuid of the network to attach the instance to.\n'
                        '--disk:    The disks attached to the instance, in this format: \n'
                        '           size@image_url where size is in GB and @image_url\n'
                        '           is optional.\n'))
@click.argument('name', type=click.STRING)
@click.argument('cpus', type=click.INT)
@click.argument('memory', type=click.INT)
@click.option('-n', '--network', type=click.STRING, multiple=True,
              autocompletion=_get_networks)
@click.option('-d', '--disk', type=click.STRING, multiple=True)
@click.pass_context
def instance_create(ctx, name=None, cpus=None, memory=None, network=None, disk=None):
    if len(disk) < 1:
        print('You must specify at least one disk')
    if len(network) < 1:
        print('You must specify at least one network')

    _show_instance(
        CLIENT.create_instance(
            name,
            cpus,
            memory,
            network,
            disk,
            'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC2Pas6zLLgzXsUSZzt8E8fX7tzpwmNlrsbAeH9YoI2snfo+cKfO1BZVQgJnJVz+hGhnC1mzsMZMtdW2NRonRgeeQIPTUFXJI+3dyGzmiNrmtH8QQz++7zsmdwngeXKDrYhD6JGnPTkKcjShYcbvB/L3IDDJvepLxVOGRJBVHXJzqHgA62AtVsoiECKxFSn8MOuRfPHj5KInLxOEX9i/TfYKawSiId5xEkWWtcrp4QhjuoLv4UHL2aKs85ppVZFTmDHHcx3Au7pZ7/T9NOcUrvnwmQDVIBeU0LEELzuQZWLkFYvStAeCF7mYra+EJVXjiCQ9ZBw0vXGqJR1SU+W6dh9 mikal@kolla-m1'))


@instance.command(name='delete', help='Delete an instance')
@click.argument('uuid', type=click.STRING, autocompletion=_get_instances)
@click.pass_context
def instance_delete(ctx, uuid=None):
    CLIENT.delete_instance(uuid)


@instance.command(name='snapshot', help='Snapshot instance')
@click.argument('uuid', type=click.STRING, autocompletion=_get_instances)
@click.argument('all', type=click.BOOL, default=False)
@click.pass_context
def instance_snapshot(ctx, uuid=None, all=False):
    print('Created snapshot %s'
          % CLIENT.snapshot_instance(uuid, all))


cli.add_command(instance)


@click.group(help='Image commands')
def image():
    pass


@image.command(name='cache',
               help=('Cache an image.\n\n'
                     'IMAGE_URL: The URL of the image to cache'))
@click.argument('image_url', type=click.STRING)
@click.pass_context
def image_cache(ctx, image_url=None):
    CLIENT.cache_image(image_url)


cli.add_command(image)
