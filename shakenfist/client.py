# Copyright 2020 Michael Still

import click
from prettytable import PrettyTable
import uuid

from shakenfist import config
from shakenfist.db import impl as db
from shakenfist.net import impl as net
from shakenfist import util
from shakenfist import virt


@click.group()
def cli():
    pass


@click.group(help='Network commands')
def network():
    pass


def _get_networks(ctx, args, incomplete):
    for n in db.get_networks():
        yield n.uuid


@network.command(name='list', help='List networks')
def network_list():
    x = PrettyTable()
    x.field_names = ['uuid', 'owner', 'netblock']

    for n in db.get_networks():
        x.add_row([n.uuid, n.owner, n.netblock])

    print(x)


@network.command(name='show', help='Show a network')
@click.argument('uuid', type=click.STRING, autocompletion=_get_networks)
def network_show(uuid=None):
    n = db.get_network(uuid)
    if not n:
        print('Network %s not found' % uuid)
        sys.exit(1)

    print('%-12s: %s' % ('uuid', n.uuid))
    print('%-12s: %s' % ('vxlan id', n.vxid))
    print('%-12s: %s' % ('netblock', n.netblock))
    print('%-12s: %s' % ('provide dhcp', n.provide_dhcp))
    print('%-12s: %s' % ('provide nat', n.provide_nat))
    print('%-12s: %s' % ('owner', n.owner))


@network.command(name='create',
                 help=('Create a network.\n\n'
                       'NETBLOCK: The IP address block to use, as a CIDR'
                       ' range -- for example 192.168.200.1/24'))
@click.argument('netblock', type=click.STRING)
def network_create(netblock=None):
    db.allocate_network(netblock)


@network.command(name='delete', help='Delete a network')
@click.argument('uuid', type=click.STRING, autocompletion=_get_networks)
def network_delete(uuid=None):
    click.echo('123')


cli.add_command(network)


@click.group(help='Instance commands')
def instance():
    pass


def _get_instances(ctx, args, incomplete):
    return


def _resolve_image(image):
    if image == 'cirros':
        versions = discover_


@instance.command(name='list', help='List instances')
def instance_list():
    print('123')


@instance.command(name='show', help='Show an instance')
@click.argument('uuid', type=click.STRING, autocompletion=_get_instances)
def instance_show(uuid=None):
    print('123')


@instance.command(name='create',
                  help=('Create an instance.\n\n'
                        'NETWORK: The uuid of the network to attach the instance to.\n'
                        'NAME: The name of the instance.\n'
                        'CPUS: The number of vCPUs for the instance.\n'
                        'MEMORY: The amount RAM for the instance in GB.\n'
                        'DISK: The disks attached to the instance, in this format: \n'
                        '          size@image_url where size is in GB and @image_url\n'
                        '          is optional.\n'))
@click.argument('network', type=click.STRING)
@click.argument('name', type=click.STRING)
@click.argument('cpus', type=click.INT)
@click.argument('memory', type=click.INT)
@click.argument('disk', type=click.STRING, nargs=-1)
def instance_create(network=None, name=None, cpus=None, memory=None, disk=None):
    n = net.from_db(network)
    if not n:
        print('Network %s not found' % uuid)
        sys.exit(1)
    n.create()

    newid = str(uuid.uuid4())
    instance = virt.Instance(
        uuid=newid,
        name=name,
        tenant=None,
        disks=disk,
        memory_kb=memory * 1024 * 1024,
        vcpus=cpus)

    with util.RecordedOperation('allocate ip address', instance) as _:
        n.allocate_ip_to_instance(instance)
        n.update_dhcp()

    with util.RecordedOperation('instance creation', instance) as _:
        instance.create()

    print('Created %s' % newid)


@instance.command(name='delete', help='Delete an instance')
@click.argument('uuid', type=click.STRING, autocompletion=_get_networks)
def instance_delete(uuid=None):
    click.echo('123')


cli.add_command(instance)
