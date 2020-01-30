# Copyright 2020 Michael Still

import click
from prettytable import PrettyTable

from shakenfist import config
from shakenfist import db


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
