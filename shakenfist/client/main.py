# Copyright 2020 Michael Still

import base64
import click
import datetime
import json
import logging
import os
from prettytable import PrettyTable
import sys

from shakenfist.client import apiclient


logging.basicConfig(level=logging.INFO)

LOG = logging.getLogger(__file__)
LOG.setLevel(logging.INFO)


CLIENT = None


def filter_dict(d, allowed_keys):
    out = {}
    for key in allowed_keys:
        if key in d:
            out[key] = d[key]
    return out


@click.group()
@click.option('--pretty', 'output', flag_value='pretty', default=True)
@click.option('--simple', 'output', flag_value='simple')
@click.option('--json', 'output', flag_value='json')
@click.option('--verbose/--no-verbose', default=False)
@click.option('--namespace', envvar='SHAKENFIST_NAMESPACE', default=None)
@click.option('--key', envvar='SHAKENFIST_KEY', default=None)
@click.option('--apiurl', envvar='SHAKENFIST_API_URL', default='http://localhost:13000')
@click.pass_context
def cli(ctx, output, verbose, namespace, key, apiurl):
    if not ctx.obj:
        ctx.obj = {}
    ctx.obj['OUTPUT'] = output

    if verbose:
        LOG.setLevel(logging.INFO)

    # Where do we find authentication details? First off, we try command line
    # flags; then environment variables (thanks for doing this for free click);
    # and finally ~/.shakenfist (which is a JSON file).
    if not namespace:
        user_conf = os.path.expanduser('~/.shakenfist')
        if os.path.exists(user_conf):
            with open(user_conf) as f:
                d = json.loads(f.read())
                namespace = d['namespace']
                key = d['key']
                apiurl = d['apiurl']

    if not namespace:
        if os.path.exists('/etc/sf/shakenfist.json'):
            with open('/etc/sf/shakenfist.json') as f:
                d = json.loads(f.read())
                namespace = d['namespace']
                key = d['key']
                apiurl = d['apiurl']

    global CLIENT
    CLIENT = apiclient.Client(
        namespace=namespace,
        key=key,
        base_url=apiurl,
        verbose=verbose)


@click.group(help='Node commands')
def node():
    pass


@node.command(name='list', help='List nodes')
@click.pass_context
def node_list(ctx):
    nodes = list(CLIENT.get_nodes())

    if ctx.obj['OUTPUT'] == 'pretty':
        x = PrettyTable()
        x.field_names = ['name', 'ip', 'lastseen']
        for n in nodes:
            x.add_row([n['name'], n['ip'], n['lastseen']])
        print(x)

    elif ctx.obj['OUTPUT'] == 'simple':
        print('name,ip,lastseen')
        for n in nodes:
            print('%s,%s,%s' % (n['name'], n['ip'], n['lastseen']))

    elif ctx.obj['OUTPUT'] == 'json':
        filtered_nodes = []
        for n in nodes:
            filtered_nodes.append(filter_dict(n, ['name', 'ip', 'lastseen']))
        print(json.dumps({'nodes': filtered_nodes}, indent=4, sort_keys=True))


cli.add_command(node)


@click.group(help='Namespace commands')
def namespace():
    pass


def _get_namespaces(ctx, args, incomplete):
    return list(CLIENT.get_namespaces())


@namespace.command(name='list', help='List namespaces')
@click.pass_context
def namespace_list(ctx):
    namespaces = list(CLIENT.get_namespaces())

    if ctx.obj['OUTPUT'] == 'pretty':
        x = PrettyTable()
        x.field_names = ['namespace']
        for n in namespaces:
            x.add_row([n])
        print(x)

    elif ctx.obj['OUTPUT'] == 'simple':
        print('namespace')
        for n in namespaces:
            print(n)

    elif ctx.obj['OUTPUT'] == 'json':
        print(json.dumps(namespaces))


@namespace.command(name='create',
                   help=('Create a namespace.\n\n'
                         'NAMESPACE: The name of the namespace\n'
                         'KEY_NAME:  The unique name of the key\n'
                         'KEY:       The password for the namespace'))
@click.argument('namespace', type=click.STRING)
@click.argument('key_name', type=click.STRING)
@click.argument('key', type=click.STRING)
@click.pass_context
def namespace_create(ctx, namespace=None, key_name=None, key=None):
    CLIENT.create_namespace(namespace, key_name, key)


@namespace.command(name='delete',
                   help=('delete a namespace.\n\n'
                         'namespace: The name of the namespace'))
@click.argument('namespace', type=click.STRING)
@click.pass_context
def namespace_delete(ctx, namespace=None):
    CLIENT.delete_namespace(namespace)


@namespace.command(name='delete_key',
                   help=('delete a specific key from a namespace.\n\n'
                         'NAMESPACE: The name of the namespace\n'
                         'KEYNAME:   The name of the key'))
@click.argument('namespace', type=click.STRING)
@click.argument('keyname', type=click.STRING)
@click.pass_context
def namespace_delete(ctx, namespace=None, keyname=None):
    CLIENT.delete_namespace_key(namespace, keyname)


cli.add_command(namespace)


@click.group(help='Network commands')
def network():
    pass


def _get_networks(ctx, args, incomplete):
    for n in list(CLIENT.get_networks()):
        yield n['uuid']


@network.command(name='list', help='List networks')
@click.argument('all', type=click.BOOL, default=False)
@click.pass_context
def network_list(ctx, all=False):
    nets = list(CLIENT.get_networks(all=all))

    if ctx.obj['OUTPUT'] == 'pretty':
        x = PrettyTable()
        x.field_names = ['uuid', 'name', 'owner', 'netblock']
        for n in nets:
            x.add_row([n['uuid'], n['name'], n['owner'], n['netblock']])
        print(x)

    elif ctx.obj['OUTPUT'] == 'simple':
        print('uuid,name,owner,netblock')
        for n in nets:
            print('%s,%s,%s,%s' %
                  (n['uuid'], n['name'], n['owner'], n['netblock']))

    elif ctx.obj['OUTPUT'] == 'json':
        filtered_nets = []
        for n in nets:
            filtered_nets.append(filter_dict(
                n, ['uuid', 'name', 'owner', 'netblock']))
        print(json.dumps({'networks': filtered_nets},
                         indent=4, sort_keys=True))


def _show_network(ctx, n):
    if not n:
        print('Network not found')
        sys.exit(1)

    if ctx.obj['OUTPUT'] == 'json':
        print(json.dumps(filter_dict(n, ['uuid', 'name', 'vxid', 'netblock', 'provide_dhcp',
                                         'provide_nat', 'owner']),
                         indent=4, sort_keys=True))
        return

    format_string = '%-12s: %s'
    if ctx.obj['OUTPUT'] == 'simple':
        format_string = '%s:%s'

    print(format_string % ('uuid', n['uuid']))
    print(format_string % ('name', n['name']))
    print(format_string % ('vxlan id', n['vxid']))
    print(format_string % ('netblock', n['netblock']))
    print(format_string % ('provide dhcp', n['provide_dhcp']))
    print(format_string % ('provide nat', n['provide_nat']))
    print(format_string % ('owner', n['owner']))


@network.command(name='show', help='Show a network')
@click.argument('network_uuid', type=click.STRING, autocompletion=_get_networks)
@click.pass_context
def network_show(ctx, network_uuid=None):
    _show_network(ctx, CLIENT.get_network(network_uuid))


@network.command(name='create',
                 help=('Create a network.\n\n'
                       'NETBLOCK:         The IP address block to use, as a CIDR\n'
                       '                  range -- for example 192.168.200.1/24\n'
                       'NAME:             The name of the network\n'
                       '--dhcp/--no-dhcp: Should this network have DHCP?\n'
                       '--nat/--no-nat:   Should this network be able to access'
                       '                  the Internet via NAT?'))
@click.argument('netblock', type=click.STRING)
@click.argument('name', type=click.STRING)
@click.option('--dhcp/--no-dhcp', default=True)
@click.option('--nat/--no-nat', default=True)
@click.pass_context
def network_create(ctx, netblock=None, name=None, dhcp=None, nat=None):
    _show_network(ctx, CLIENT.allocate_network(netblock, dhcp, nat, name))


@network.command(name='events', help='Display events for a network')
@click.argument('network_uuid', type=click.STRING, autocompletion=_get_networks)
@click.pass_context
def network_events(ctx, network_uuid=None):
    events = CLIENT.get_network_events(network_uuid)
    if ctx.obj['OUTPUT'] == 'pretty':
        x = PrettyTable()
        x.field_names = ['timestamp', 'node',
                         'operation', 'phase', 'duration', 'message']
        for e in events:
            e['timestamp'] = datetime.datetime.fromtimestamp(e['timestamp'])
            x.add_row([e['timestamp'], e['fqdn'], e['operation'], e['phase'],
                       e['duration'], e['message']])
        print(x)

    elif ctx.obj['OUTPUT'] == 'simple':
        print('timestamp,node,operation,phase,duration,message')
        for e in events:
            e['timestamp'] = datetime.datetime.fromtimestamp(e['timestamp'])
            x.add_row('%s,%s,%s,%s,%s,%s'
                      % (e['timestamp'], e['fqdn'], e['operation'], e['phase'],
                         e['duration'], e['message']))

    elif ctx.obj['OUTPUT'] == 'json':
        filtered_events = []
        for e in events:
            filtered_events.append(filter_dict(
                e, ['timestamp', 'fqdn', 'operation', 'phase', 'duration', 'message']))
        print(json.dumps({'networks': filtered_events},
                         indent=4, sort_keys=True))


@network.command(name='delete', help='Delete a network')
@click.argument('network_uuid', type=click.STRING, autocompletion=_get_networks)
@click.pass_context
def network_delete(ctx, network_uuid=None):
    CLIENT.delete_network(network_uuid)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


cli.add_command(network)


@click.group(help='Instance commands')
def instance():
    pass


def _get_instances(ctx, args, incomplete):
    for i in CLIENT.get_instances():
        yield i['uuid']


@instance.command(name='list', help='List instances')
@click.argument('all', type=click.BOOL, default=False)
@click.pass_context
def instance_list(ctx, all=False):
    insts = CLIENT.get_instances(all=all)

    if ctx.obj['OUTPUT'] == 'pretty':
        x = PrettyTable()
        x.field_names = ['uuid', 'name', 'cpus', 'memory', 'hypervisor']
        for i in insts:
            x.add_row([i['uuid'], i['name'], i['cpus'], i['memory'], i['node']])
        print(x)

    elif ctx.obj['OUTPUT'] == 'simple':
        print('uuid,name,cpus,memory,hypervisor')
        for i in insts:
            print('%s,%s,%s,%s,%s' %
                  (i['uuid'], i['name'], i['cpus'], i['memory'], i['node']))

    elif ctx.obj['OUTPUT'] == 'json':
        filtered_insts = []
        for i in insts:
            filtered_insts.append(filter_dict(
                i, ['uuid', 'name', 'cpus', 'memory', 'node']))
        print(json.dumps({'instances': filtered_insts},
                         indent=4, sort_keys=True))


def _show_instance(ctx, i, include_snapshots=False):
    if not i:
        print('Instance not found')
        sys.exit(1)

    if include_snapshots:
        snapshots = CLIENT.get_instance_snapshots(i['uuid'])

    if ctx.obj['OUTPUT'] == 'json':
        out = filter_dict(i, ['uuid', 'name', 'cpus', 'memory', 'disk_spec',
                              'node', 'console_port', 'vdi_port', 'ssh_key',
                              'user_data'])
        out['network_interfaces'] = []
        for interface in CLIENT.get_instance_interfaces(i['uuid']):
            out['network_interfaces'].append(
                filter_dict(
                    interface, ['uuid', 'network_uuid', 'macaddr', 'order',
                                'ipv4', 'floating']))
        if include_snapshots:
            out['snapshots'] = []
            for snap in snapshots:
                out['snapshots'].append(filter_dict(
                    snap, ['uuid', 'device', 'created']))

        print(json.dumps(out, indent=4, sort_keys=True))
        return

    format_string = '%-12s: %s'
    if ctx.obj['OUTPUT'] == 'simple':
        format_string = '%s:%s'

    print(format_string % ('uuid', i['uuid']))
    print(format_string % ('name', i['name']))
    print(format_string % ('cpus', i['cpus']))
    print(format_string % ('memory', i['memory']))
    print(format_string % ('disk spec', i['disk_spec']))
    print(format_string % ('node', i['node']))

    # NOTE(mikal): I am not sure we should expose this, but it will do
    # for now until a proxy is written.
    print(format_string % ('console port', i['console_port']))
    print(format_string % ('vdi port', i['vdi_port']))

    print()
    print(format_string % ('ssh key', i['ssh_key']))
    print(format_string % ('user data', i['user_data']))

    print()
    if ctx.obj['OUTPUT'] == 'pretty':
        format_string = '    %-8s: %s'
        print('Interfaces:')
        for interface in CLIENT.get_instance_interfaces(i['uuid']):
            print()
            print(format_string % ('uuid', interface['uuid']))
            print(format_string % ('network', interface['network_uuid']))
            print(format_string % ('macaddr', interface['macaddr']))
            print(format_string % ('order', interface['order']))
            print(format_string % ('ipv4', interface['ipv4']))
            print(format_string % ('floating', interface['floating']))
    else:
        print('iface,interface uuid,network uuid,macaddr,order,ipv4,floating')
        for interface in CLIENT.get_instance_interfaces(i['uuid']):
            print('iface,%s,%s,%s,%s,%s,%s'
                  % (interface['uuid'], interface['network_uuid'],
                     interface['macaddr'], interface['order'], interface['ipv4'],
                     interface['floating']))

    if include_snapshots:
        print()
        if ctx.obj['OUTPUT'] == 'pretty':
            format_string = '    %-8s: %s'
            print('Snapshots:')
            for snap in snapshots:
                print()
                print(format_string % ('uuid', snap['uuid']))
                print(format_string % ('device', snap['device']))
                print(format_string % (
                    'created', datetime.datetime.fromtimestamp(snap['created'])))
        else:
            print('snapshot,uuid,device,created')
            for snap in snapshots:
                print('snapshot,%s,%s,%s'
                      % (snap['uuid'], snap['device'],
                         datetime.datetime.fromtimestamp(snap['created'])))


@instance.command(name='show', help='Show an instance')
@click.argument('instance_uuid', type=click.STRING, autocompletion=_get_instances)
@click.argument('snapshots', type=click.BOOL, default=False)
@click.pass_context
def instance_show(ctx, instance_uuid=None, snapshots=False):
    _show_instance(ctx, CLIENT.get_instance(instance_uuid), snapshots)


def _parse_spec(spec):
    if '@' not in spec:
        return spec, None
    return spec.split('@')


@instance.command(name='create',
                  help=('Create an instance.\n\n'
                        'NAME:      The name of the instance.\n'
                        'CPUS:      The number of vCPUs for the instance.\n'
                        'MEMORY:    The amount of RAM for the instance in GB.\n'
                        '\n'
                        'Options (may be repeated, must be specified at least once):\n'
                        '--network/-n:   The uuid of the network to attach the instance to.\n'
                        '--disk/-d:      The disks attached to the instance, in this format: \n'
                        '                size@image_url where size is in GB and @image_url\n'
                        '                is optional.\n'
                        '--sshkey/-i:    The path to a ssh public key to configure on the\n'
                        '                instance via config drive / cloud-init.\n'
                        '--sshkeydata/-I:\n'
                        '               A ssh public key as a string to configure on the\n'
                        '                 instance via config drive / cloud-init.\n'
                        '--userdata/-u:  The path to a file containing user data to provided\n'
                        '                to the instance via config drive / cloud-init.'
                        '--encodeduserdata/-U:\n'
                        '                Base64 encoded user data to provide to the instance\n'
                        '                via config drive / cloud-init.\n'
                        '\n'
                        '--placement/-p: Force placement of instance on specified node'))
@click.argument('name', type=click.STRING)
@click.argument('cpus', type=click.INT)
@click.argument('memory', type=click.INT)
@click.option('-n', '--network', type=click.STRING, multiple=True,
              autocompletion=_get_networks)
@click.option('-N', '--networkspec', type=click.STRING, multiple=True)
@click.option('-d', '--disk', type=click.STRING, multiple=True)
@click.option('-D', '--diskspec', type=click.STRING, multiple=True)
@click.option('-i', '--sshkey', type=click.STRING)
@click.option('-I', '--sshkeydata', type=click.STRING)
@click.option('-u', '--userdata', type=click.STRING)
@click.option('-U', '--encodeduserdata', type=click.STRING)
@click.option('-p', '--placement', type=click.STRING)
@click.pass_context
def instance_create(ctx, name=None, cpus=None, memory=None, network=None, networkspec=None,
                    disk=None, diskspec=None, sshkey=None, sshkeydata=None, userdata=None,
                    encodeduserdata=None, placement=None):
    if len(disk) < 1 and len(diskspec) < 1:
        print('You must specify at least one disk')

    sshkey_content = None
    if sshkey:
        with open(sshkey) as f:
            sshkey_content = f.read()
    if sshkeydata:
        sshkey_content = sshkeydata

    userdata_content = None
    if userdata:
        with open(userdata) as f:
            userdata_content = f.read()
        userdata_content = str(base64.b64encode(
            userdata_content.encode('utf-8')), 'utf-8')
    if encodeduserdata:
        userdata_content = encodeduserdata

    diskdefs = []
    for d in disk:
        size, base = _parse_spec(d)
        diskdefs.append({
            'size': int(size),
            'base': base,
            'bus': None,
            'type': 'disk',
        })
    for d in diskspec:
        defn = {}
        for elem in d.split(','):
            key, value = elem.split('=')
            defn[key] = value
        diskdefs.append(defn)

    netdefs = []
    for n in network:
        network_uuid, address = _parse_spec(n)
        netdefs.append({
            'network_uuid': network_uuid,
            'address': address,
            'macaddress': None,
            'model': 'virtio'
        })
    for n in networkspec:
        defn = {}
        for elem in n.split(','):
            key, value = elem.split('=')
            defn[key] = value
        netdefs.append(defn)

    _show_instance(
        ctx,
        CLIENT.create_instance(name, cpus, memory, netdefs, diskdefs, sshkey_content,
                               userdata_content, force_placement=placement))


@instance.command(name='delete', help='Delete an instance')
@click.argument('instance_uuid', type=click.STRING, autocompletion=_get_instances)
@click.pass_context
def instance_delete(ctx, instance_uuid=None):
    CLIENT.delete_instance(instance_uuid)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


@instance.command(name='events', help='Display events for an instance')
@click.argument('instance_uuid', type=click.STRING, autocompletion=_get_instances)
@click.pass_context
def instance_events(ctx, instance_uuid=None):
    events = CLIENT.get_instance_events(instance_uuid)
    if ctx.obj['OUTPUT'] == 'pretty':
        x = PrettyTable()
        x.field_names = ['timestamp', 'node',
                         'operation', 'phase', 'duration', 'message']
        for e in events:
            e['timestamp'] = datetime.datetime.fromtimestamp(e['timestamp'])
            x.add_row([e['timestamp'], e['fqdn'], e['operation'], e['phase'],
                       e['duration'], e['message']])
        print(x)

    elif ctx.obj['OUTPUT'] == 'simple':
        print('timestamp,node,operation,phase,duration,message')
        for e in events:
            e['timestamp'] = datetime.datetime.fromtimestamp(e['timestamp'])
            print('%s,%s,%s,%s,%s,%s'
                  % (e['timestamp'], e['fqdn'], e['operation'], e['phase'],
                     e['duration'], e['message']))

    elif ctx.obj['OUTPUT'] == 'json':
        filtered_events = []
        for e in events:
            filtered_events.append(filter_dict(
                e, ['timestamp', 'fqdn', 'operation', 'phase', 'duration', 'message']))
        print(json.dumps({'events': filtered_events},
                         indent=4, sort_keys=True))


@instance.command(name='reboot', help='Reboot instance')
@click.argument('instance_uuid', type=click.STRING, autocompletion=_get_instances)
@click.option('--hard/--soft', default=False)
@click.pass_context
def instance_reboot(ctx, instance_uuid=None, hard=False):
    CLIENT.reboot_instance(instance_uuid, hard=hard)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


@instance.command(name='poweron', help='Power on an instance')
@click.argument('instance_uuid', type=click.STRING, autocompletion=_get_instances)
@click.pass_context
def instance_power_on(ctx, instance_uuid=None):
    CLIENT.power_on_instance(instance_uuid)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


@instance.command(name='poweroff', help='Power off an instance')
@click.argument('instance_uuid', type=click.STRING, autocompletion=_get_instances)
@click.pass_context
def instance_power_off(ctx, instance_uuid=None):
    CLIENT.power_off_instance(instance_uuid)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


@instance.command(name='pause', help='Pause an instance')
@click.argument('instance_uuid', type=click.STRING, autocompletion=_get_instances)
@click.pass_context
def instance_pause(ctx, instance_uuid=None):
    CLIENT.pause_instance(instance_uuid)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


@instance.command(name='unpause', help='Unpause an instance')
@click.argument('instance_uuid', type=click.STRING, autocompletion=_get_instances)
@click.pass_context
def instance_unpause(ctx, instance_uuid=None):
    CLIENT.unpause_instance(instance_uuid)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


@instance.command(name='snapshot', help='Snapshot instance')
@click.argument('instance_uuid', type=click.STRING, autocompletion=_get_instances)
@click.argument('all', type=click.BOOL, default=False)
@click.pass_context
def instance_snapshot(ctx, instance_uuid=None, all=False):
    snapshot_uuid = CLIENT.snapshot_instance(instance_uuid, all)
    if ctx.obj['OUTPUT'] == 'json':
        print(json.dumps({'uuid': snapshot_uuid}, indent=4, sort_keys=True))
    else:
        print('Created snapshot %s' % snapshot_uuid)


cli.add_command(instance)


@click.group(help='Interface commands')
def interface():
    pass


@interface.command(name='float',
                   help='Add a floating IP to an interface')
@click.argument('interface_uuid', type=click.STRING)
@click.pass_context
def interface_float(ctx, interface_uuid=None):
    CLIENT.float_interface(interface_uuid)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


@interface.command(name='defloat',
                   help='Remove a floating IP to an interface')
@click.argument('interface_uuid', type=click.STRING)
@click.pass_context
def interface_deloat(ctx, interface_uuid=None):
    CLIENT.defloat_interface(interface_uuid)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


cli.add_command(interface)


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
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


cli.add_command(image)
