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


class GroupCatchExceptions(click.Group):
    def __call__(self, *args, **kwargs):
        try:
            return self.main(*args, **kwargs)

        except apiclient.RequestMalformedException as e:
            click.echo('ERROR: Malformed Request: %s' % error_text(e.text))
            sys.exit(1)

        except apiclient.UnauthorizedException:
            click.echo('ERROR: Not authorized')
            sys.exit(1)

        except apiclient.ResourceCannotBeDeletedException as e:
            click.echo('ERROR: Cannot delete resource: %s' %
                       error_text(e.text))
            sys.exit(1)

        except apiclient.ResourceNotFoundException as e:
            click.echo('ERROR: Resource not found: %s' % error_text(e.text))
            sys.exit(1)

        except apiclient.ResourceInUseException as e:
            click.echo('ERROR: Resource in use: %s' % error_text(e.text))
            sys.exit(1)

        except apiclient.InternalServerError as e:
            # Print full error since server should not fail
            click.echo('ERROR: Internal Server Error: %s' % e.text)
            sys.exit(1)

        except apiclient.InsufficientResourcesException as e:
            click.echo('ERROR: Insufficient Resources: %s' %
                       error_text(e.text))
            sys.exit(1)

        except apiclient.requests.exceptions.ConnectionError as e:
            click.echo('ERROR: Unable to connect to server: %s' % e)


def error_text(json_text):
    try:
        err = json.loads(json_text)
        if 'error' in err:
            return err['error']
    except Exception:
        pass

    return json_text


def auto_complete(func):
    global CLIENT

    if not CLIENT:
        CLIENT = apiclient.Client(
            namespace=os.getenv('SHAKENFIST_NAMESPACE'),
            key=os.getenv('SHAKENFIST_KEY'),
            base_url=os.getenv('SHAKENFIST_API_URL', 'http://localhost:13000'),
        )

    return func


def filter_dict(d, allowed_keys):
    out = {}
    for key in allowed_keys:
        if key in d:
            out[key] = d[key]
    return out


def longest_str(d):
    if not d:
        return 0
    return max(len(k) for k in d)


@click.group(cls=GroupCatchExceptions)
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
        x.field_names = ['name', 'ip', 'lastseen', 'version']
        for n in nodes:
            x.add_row([n['name'], n['ip'], n['lastseen'], n['version']])
        print(x)

    elif ctx.obj['OUTPUT'] == 'simple':
        print('name,ip,lastseen,version')
        for n in nodes:
            print('%s,%s,%s,%s' % (
                n['name'], n['ip'], n['lastseen'], n['version']))

    elif ctx.obj['OUTPUT'] == 'json':
        filtered_nodes = []
        for n in nodes:
            filtered_nodes.append(
                filter_dict(n, ['name', 'ip', 'lastseen', 'version']))
        print(json.dumps({'nodes': filtered_nodes}, indent=4, sort_keys=True))


cli.add_command(node)


@click.group(help='Namespace commands')
def namespace():
    pass


@auto_complete
def _get_namespaces(ctx, args, incomplete):
    choices = CLIENT.get_namespaces()
    return [arg for arg in choices if arg.startswith(incomplete)]


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
                         'NAMESPACE: The name of the namespace'))
@click.argument('namespace', type=click.STRING)
@click.pass_context
def namespace_create(ctx, namespace=None):
    CLIENT.create_namespace(namespace)


@namespace.command(name='delete',
                   help=('delete a namespace.\n\n'
                         'NAMESPACE: The name of the namespace'))
@click.argument('namespace', type=click.STRING)
@click.pass_context
def namespace_delete(ctx, namespace=None):
    CLIENT.delete_namespace(namespace)


def _show_namespace(ctx, namespace):
    if namespace not in CLIENT.get_namespaces():
        print('Namespace not found')
        sys.exit(1)

    key_names = CLIENT.get_namespace_keynames(namespace)
    metadata = CLIENT.get_namespace_metadata(namespace)

    if ctx.obj['OUTPUT'] == 'json':
        out = {'key_names': key_names,
               'metadata': metadata,
               }
        print(json.dumps(out, indent=4, sort_keys=True))
        return

    if ctx.obj['OUTPUT'] == 'pretty':
        format_string = '    %s'
        print('Key Names:')
        if key_names:
            for key in key_names:
                print(format_string % (key))

        print('Metadata:')
        if metadata:
            format_string = '    %-' + str(longest_str(metadata)) + 's: %s'
            for key in metadata:
                print(format_string % (key, metadata[key]))

    else:
        print('metadata,keyname')
        if key_names:
            for key in key_names:
                print('keyname,%s' % (key))
        print('metadata,key,value')
        if metadata:
            for key in metadata:
                print('metadata,%s,%s' % (key, metadata[key]))


@namespace.command(name='show', help='Show a namespace')
@click.argument('namespace', type=click.STRING, autocompletion=_get_namespaces)
@click.pass_context
def namespace_show(ctx, namespace=None):
    _show_namespace(ctx, namespace)


@namespace.command(name='clean',
                   help=('Clean (delete) namespace of all instances and networks'))
@click.option('--confirm',  is_flag=True)
@click.option('--namespace', type=click.STRING)
@click.pass_context
def namespace_clean(ctx, confirm=False, namespace=None):
    if not confirm:
        print('You must be sure. Use option --confirm.')
        return

    CLIENT.delete_all_instances(namespace)
    CLIENT.delete_all_networks(namespace)


@namespace.command(name='add-key',
                   help=('add a key to a namespace.\n\n'
                         'NAMESPACE: The name of the namespace\n'
                         'KEY_NAME:  The unique name of the key\n'
                         'KEY:       The password for the namespace'))
@click.argument('namespace', type=click.STRING)
@click.argument('keyname', type=click.STRING)
@click.argument('key', type=click.STRING)
@click.pass_context
def namespace_add_key(ctx, namespace=None, keyname=None, key=None):
    CLIENT.add_namespace_key(namespace, keyname, key)


@namespace.command(name='delete-key',
                   help=('delete a specific key from a namespace.\n\n'
                         'NAMESPACE: The name of the namespace\n'
                         'KEYNAME:   The name of the key'))
@click.argument('namespace', type=click.STRING)
@click.argument('keyname', type=click.STRING)
@click.pass_context
def namespace_delete_key(ctx, namespace=None, keyname=None):
    CLIENT.delete_namespace_key(namespace, keyname)


@namespace.command(name='get-metadata', help='Get metadata items')
@click.argument('namespace', type=click.STRING)
@click.pass_context
def namespace_get_metadata(ctx, namespace=None):
    metadata = CLIENT.get_namespace_metadata(namespace)

    if ctx.obj['OUTPUT'] == 'json':
        return metadata

    format_string = '%-12s: %s'
    if ctx.obj['OUTPUT'] == 'simple':
        format_string = '%s:%s'
    for key in metadata:
        print(format_string % (key, metadata[key]))


@namespace.command(name='set-metadata', help='Set a metadata item')
@click.argument('namespace', type=click.STRING)
@click.argument('key', type=click.STRING)
@click.argument('value', type=click.STRING)
@click.pass_context
def namespace_set_metadata(ctx, namespace=None, key=None, value=None):
    CLIENT.set_namespace_metadata_item(namespace, key, value)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


@namespace.command(name='delete-metadata', help='Delete a metadata item')
@click.argument('namespace', type=click.STRING)
@click.argument('key', type=click.STRING)
@click.pass_context
def namespace_delete_metadata(ctx, namespace=None, key=None):
    CLIENT.delete_namespace_metadata_item(namespace, key)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


cli.add_command(namespace)


@click.group(help='Network commands')
def network():
    pass


@auto_complete
def _get_networks(ctx, args, incomplete):
    choices = [i['uuid'] for i in CLIENT.get_networks()]
    return [arg for arg in choices if arg.startswith(incomplete)]


@network.command(name='list', help='List networks')
@click.argument('all', type=click.BOOL, default=False)
@click.pass_context
def network_list(ctx, all=False):
    nets = list(CLIENT.get_networks(all=all))

    if ctx.obj['OUTPUT'] == 'pretty':
        x = PrettyTable()
        x.field_names = ['uuid', 'name', 'namespace', 'netblock']
        for n in nets:
            x.add_row([n['uuid'], n['name'], n['namespace'], n['netblock']])
        print(x)

    elif ctx.obj['OUTPUT'] == 'simple':
        print('uuid,name,namespace,netblock')
        for n in nets:
            print('%s,%s,%s,%s' %
                  (n['uuid'], n['name'], n['namespace'], n['netblock']))

    elif ctx.obj['OUTPUT'] == 'json':
        filtered_nets = []
        for n in nets:
            filtered_nets.append(filter_dict(
                n, ['uuid', 'name', 'namespace', 'netblock']))
        print(json.dumps({'networks': filtered_nets},
                         indent=4, sort_keys=True))


def _show_network(ctx, n):
    if not n:
        print('Network not found')
        sys.exit(1)

    metadata = CLIENT.get_network_metadata(n['uuid'])

    if ctx.obj['OUTPUT'] == 'json':
        filtered = filter_dict(n, ['uuid', 'name', 'vxid', 'netblock',
                                   'provide_dhcp', 'provide_nat',
                                   'floating_gateway', 'namespace'])
        filtered['metadata'] = metadata
        print(json.dumps(filtered, indent=4, sort_keys=True))
        return

    format_string = '%-16s: %s'
    if ctx.obj['OUTPUT'] == 'simple':
        format_string = '%s:%s'

    print(format_string % ('uuid', n['uuid']))
    print(format_string % ('name', n['name']))
    print(format_string % ('vxlan id', n['vxid']))
    print(format_string % ('netblock', n['netblock']))
    print(format_string % ('provide dhcp', n['provide_dhcp']))
    print(format_string % ('provide nat', n['provide_nat']))
    print(format_string % ('floating gateway', n['floating_gateway']))
    print(format_string % ('namespace', n['namespace']))
    print(format_string % ('state', n['state']))

    print()
    if ctx.obj['OUTPUT'] == 'pretty':
        format_string = '    %-8s: %s'
        print('Metadata:')
        for key in metadata:
            print(format_string % (key, metadata[key]))

    else:
        print('metadata,key,value')
        for key in metadata:
            print('metadata,%s,%s' % (key, metadata[key]))


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
                       '                  the Internet via NAT?\n'
                       '\n'
                       '--namespace:     If you are an admin, you can create this object in a\n'
                       '                 different namespace.\n'))
@click.argument('netblock', type=click.STRING)
@click.argument('name', type=click.STRING)
@click.option('--dhcp/--no-dhcp', default=True)
@click.option('--nat/--no-nat', default=True)
@click.option('--namespace', type=click.STRING)
@click.pass_context
def network_create(ctx, netblock=None, name=None, dhcp=None, nat=None, namespace=None):
    _show_network(ctx, CLIENT.allocate_network(
        netblock, dhcp, nat, name, namespace))


@network.command(name='delete-all', help='Delete ALL networks')
@click.option('--confirm',  is_flag=True)
@click.option('--namespace', type=click.STRING)
@click.pass_context
def network_delete_all(ctx, confirm=False, namespace=None):
    if not confirm:
        print('You must be sure. Use option --confirm.')
        return

    CLIENT.delete_all_networks(namespace)


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


@network.command(name='instances', help='List instances on a network')
@click.argument('network_uuid',
                type=click.STRING, autocompletion=_get_networks)
@click.pass_context
def network_list_instances(ctx, network_uuid=None):
    interfaces = CLIENT.get_network_interfaces(network_uuid)

    if ctx.obj['OUTPUT'] == 'pretty':
        x = PrettyTable()
        x.field_names = ['instance_uuid', 'ipv4', 'floating']
        for ni in interfaces:
            x.add_row([ni['instance_uuid'], ni['ipv4'], ni['floating']])
        print(x)

    elif ctx.obj['OUTPUT'] == 'simple':
        print('instance_uuid,ipv4,floating')
        for ni in interfaces:
            print('%s,%s,%s' %
                  (ni['instance_uuid'], ni['ipv4'], ni['floating']))

    elif ctx.obj['OUTPUT'] == 'json':
        filtered_ni = []
        for ni in interfaces:
            filtered_ni.append(filter_dict(
                ni, ['instance_uuid', 'ipv4', 'floating']))
        print(json.dumps({'instances': filtered_ni},
                         indent=4, sort_keys=True))


@network.command(name='set-metadata', help='Set a metadata item')
@click.argument('network_uuid', type=click.STRING, autocompletion=_get_networks)
@click.argument('key', type=click.STRING)
@click.argument('value', type=click.STRING)
@click.pass_context
def network_set_metadata(ctx, network_uuid=None, key=None, value=None):
    CLIENT.set_network_metadata_item(network_uuid, key, value)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


@network.command(name='delete-metadata', help='Delete a metadata item')
@click.argument('network_uuid', type=click.STRING, autocompletion=_get_networks)
@click.argument('key', type=click.STRING)
@click.pass_context
def network_delete_metadata(ctx, network_uuid=None, key=None, value=None):
    CLIENT.delete_network_metadata_item(network_uuid, key)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


cli.add_command(network)


@click.group(help='Instance commands')
def instance():
    pass


@auto_complete
def _get_instances(ctx, args, incomplete):
    choices = [i['uuid'] for i in CLIENT.get_instances()]
    return [arg for arg in choices if arg.startswith(incomplete)]


@instance.command(name='list', help='List instances')
@click.argument('all', type=click.BOOL, default=False)
@click.pass_context
def instance_list(ctx, all=False):
    insts = CLIENT.get_instances(all=all)

    if ctx.obj['OUTPUT'] == 'pretty':
        x = PrettyTable()
        x.field_names = ['uuid', 'name', 'namespace',
                         'cpus', 'memory', 'hypervisor',
                         'power state', 'state']
        for i in insts:
            x.add_row([i['uuid'], i['name'], i['namespace'],
                       i['cpus'], i['memory'], i['node'],
                       i.get('power_state', 'unknown'), i['state']])
        print(x)

    elif ctx.obj['OUTPUT'] == 'simple':
        print('uuid,name,namespace,cpus,memory,hypervisor,power state,state')
        for i in insts:
            print('%s,%s,%s,%s,%s,%s,%s,%s'
                  % (i['uuid'], i['name'], i['namespace'],
                     i['cpus'], i['memory'], i['node'],
                     i.get('power_state', 'unknown'), i['state']))

    elif ctx.obj['OUTPUT'] == 'json':
        filtered_insts = []
        for i in insts:
            filtered_insts.append(filter_dict(
                i, ['uuid', 'name', 'namespace', 'cpus', 'memory', 'node',
                    'power_state', 'state']))
        print(json.dumps({'instances': filtered_insts},
                         indent=4, sort_keys=True))


def _pretty_data(row, space_rules):
    ret = ''
    for key in space_rules:
        ret += key + '=' + str(row.get(key, '')).ljust(space_rules[key]) + '  '
    return ret


def _pretty_dict(lead_space, rows, space_rules):
    ret = ''

    if rows:
        ret += _pretty_data(rows[0], space_rules)
    for r in rows[1:]:
        ret += '\n'.ljust(lead_space) + _pretty_data(r, space_rules)

    return ret


def _show_instance(ctx, i, include_snapshots=False):
    if not i:
        print('Instance not found')
        sys.exit(1)

    metadata = CLIENT.get_instance_metadata(i['uuid'])
    interfaces = CLIENT.get_instance_interfaces(i['uuid'])
    if include_snapshots:
        snapshots = CLIENT.get_instance_snapshots(i['uuid'])

    if ctx.obj['OUTPUT'] == 'json':
        out = filter_dict(i, ['uuid', 'name', 'namespace', 'cpus', 'memory',
                              'disk_spec', 'video', 'node', 'console_port',
                              'vdi_port', 'ssh_key', 'user_data',
                              'power_state', 'state'])
        out['network_interfaces'] = []
        for interface in interfaces:
            _show_interface(ctx, interface, out)

        out['metadata'] = metadata

        if include_snapshots:
            out['snapshots'] = []
            for snap in snapshots:
                out['snapshots'].append(filter_dict(
                    snap, ['uuid', 'device', 'created']))

        print(json.dumps(out, indent=4, sort_keys=True))
        return

    if ctx.obj['OUTPUT'] == 'simple':
        format_string = '%s:%s'
    else:
        format_string = '%-12s: %s'
        d_space = {'type': 5, 'bus': 4, 'size': 2, 'base': 0}
        v_space = {'model': 0, 'memory': 0}

    print(format_string % ('uuid', i['uuid']))
    print(format_string % ('name', i['name']))
    print(format_string % ('namespace', i['namespace']))
    print(format_string % ('cpus', i['cpus']))
    print(format_string % ('memory', i['memory']))
    if ctx.obj['OUTPUT'] == 'pretty':
        print(format_string % ('disk spec',
                               _pretty_dict(15, i['disk_spec'], d_space)))
    if ctx.obj['OUTPUT'] == 'pretty':
        print(format_string % ('video',
                               _pretty_dict(15, (i['video'],), v_space)))
    print(format_string % ('node', i['node']))
    print(format_string % ('power state', i['power_state']))
    print(format_string % ('state', i['state']))

    # NOTE(mikal): I am not sure we should expose this, but it will do
    # for now until a proxy is written.
    print(format_string % ('console port', i['console_port']))
    print(format_string % ('vdi port', i['vdi_port']))

    print()
    print(format_string % ('ssh key', i['ssh_key']))
    print(format_string % ('user data', i['user_data']))

    if ctx.obj['OUTPUT'] == 'simple':
        print()
        print('disk_spec,type,bus,size,base')
        for d in i['disk_spec']:
            print('disk_spec,%s,%s,%s,%s' % (
                d['type'], d['bus'], d['size'], d['base']))

    if ctx.obj['OUTPUT'] == 'simple':
        print()
        print('video,model,memory')
        print('video,%s,%s' % (i['video']['model'], i['video']['memory']))

    print()
    if ctx.obj['OUTPUT'] == 'pretty':
        format_string = '    %-8s: %s'
        print('Metadata:')
        for key in metadata:
            print(format_string % (key, metadata[key]))

    else:
        print('metadata,key,value')
        for key in metadata:
            print('metadata,%s,%s' % (key, metadata[key]))

    print()
    if ctx.obj['OUTPUT'] == 'pretty':
        print('Interfaces:')
        for interface in interfaces:
            print()
            _show_interface(ctx, interface)

    else:
        print('iface,interface uuid,network uuid,macaddr,order,ipv4,floating')
        for interface in interfaces:
            _show_interface(ctx, interface)

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


# TODO(mikal): this misses the detailed version of disk and network specs, as well
# as guidance on how to use the video command line. We need to rethink how we're
# doing this, as its getting pretty long.
@instance.command(name='create',
                  help=('Create an instance.\n\n'
                        'NAME:      The name of the instance.\n'
                        'CPUS:      The number of vCPUs for the instance.\n'
                        'MEMORY:    The amount of RAM for the instance in MB.\n'
                        '\n'
                        'Options (may be repeated, must be specified at least once):\n'
                        '--network/-n:   The uuid of the network to attach the instance to.\n'
                        '--disk/-d:      The disks attached to the instance, in this format: \n'
                        '                size@image_url where size is in GB and @image_url\n'
                        '                is optional.\n'
                        '--video/-V:     The video configuration for the instance.\n'
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
                        '--placement/-p: Force placement of instance on specified node.\n'
                        '--namespace:    If you are an admin, you can create this object in a\n'
                        '                different namespace.'))
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
@click.option('-V', '--videospec', type=click.STRING)
@click.option('--namespace', type=click.STRING)
@click.pass_context
def instance_create(ctx, name=None, cpus=None, memory=None, network=None, networkspec=None,
                    disk=None, diskspec=None, sshkey=None, sshkeydata=None, userdata=None,
                    encodeduserdata=None, placement=None, videospec=None, namespace=None):
    if len(disk) < 1 and len(diskspec) < 1:
        print('You must specify at least one disk')
        return

    if memory < 256:
        if ctx.obj['OUTPUT'] != 'json':
            print('WARNING: Assuming you have specified memory in GB.')
            print('WARNING: This behaviour will be removed in the v0.3 release.')
        memory *= 1024

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
        p = _parse_spec(d)
        size, base = p
        try:
            size_int = int(size)
        except Exception:
            print('Disk size is not an integer')
            return

        diskdefs.append({
            'size': size_int,
            'base': base,
            'bus': None,
            'type': 'disk',
        })
    for d in diskspec:
        defn = {}
        for elem in d.split(','):
            s = elem.split('=')
            if len(s) != 2:
                print('Error in disk specification -'
                      ' should be key=value: %s' % elem)
                return
            defn[s[0]] = s[1]
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
            s = elem.split('=')
            if len(s) != 2:
                print('Error in network specification -'
                      ' should be key=value: %s' % elem)
                return
            defn[s[0]] = s[1]
        netdefs.append(defn)

    video = {'model': 'cirrus', 'memory': 16384}
    if videospec:
        for elem in videospec.split(','):
            s = elem.split('=')
            if len(s) != 2:
                print('Error in video specification - '
                      ' should be key=value: %s' % elem)
                return
            video[s[0]] = s[1]

    _show_instance(
        ctx,
        CLIENT.create_instance(name, cpus, memory, netdefs, diskdefs, sshkey_content,
                               userdata_content, force_placement=placement,
                               namespace=namespace, video=video))


@instance.command(name='delete', help='Delete an instance')
@click.argument('instance_uuid', type=click.STRING, autocompletion=_get_instances)
@click.pass_context
def instance_delete(ctx, instance_uuid=None):
    CLIENT.delete_instance(instance_uuid)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


@instance.command(name='delete-all', help='Delete ALL instances')
@click.option('--confirm',  is_flag=True)
@click.option('--namespace', type=click.STRING)
@click.pass_context
def instance_delete_all(ctx, confirm=False, namespace=None):
    if not confirm:
        print('You must be sure. Use option --confirm.')
        return

    CLIENT.delete_all_instances(namespace)


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


@instance.command(name='set-metadata', help='Set a metadata item')
@click.argument('instance_uuid', type=click.STRING, autocompletion=_get_instances)
@click.argument('key', type=click.STRING)
@click.argument('value', type=click.STRING)
@click.pass_context
def instance_set_metadata(ctx, instance_uuid=None, key=None, value=None):
    CLIENT.set_instance_metadata_item(instance_uuid, key, value)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


@instance.command(name='delete-metadata', help='Delete a metadata item')
@click.argument('instance_uuid', type=click.STRING, autocompletion=_get_instances)
@click.argument('key', type=click.STRING)
@click.pass_context
def instance_delete_metadata(ctx, instance_uuid=None, key=None):
    CLIENT.delete_instance_metadata_item(instance_uuid, key)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


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


@instance.command(name='consoledata', help='Get console data for an instance')
@click.argument('instance_uuid', type=click.STRING, autocompletion=_get_instances)
@click.argument('length', type=click.INT, default=10240)
@click.pass_context
def instance_consoledata(ctx, instance_uuid=None, length=None):
    print(CLIENT.get_console_data(instance_uuid, length=length))


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


@auto_complete
def _get_instance_interfaces(ctx, args, incomplete):
    choices = []
    for i in CLIENT.get_instances():
        for interface in CLIENT.get_instance_interfaces(i['uuid']):
            choices.append(interface['uuid'])
    return [arg for arg in choices if arg.startswith(incomplete)]


def _show_interface(ctx, interface, out=[]):
    if not interface:
        print('Interface not found')
        sys.exit(1)

    if ctx.obj['OUTPUT'] == 'json':
        if 'network_interfaces' not in out:
            out['network_interfaces'] = []

        out['network_interfaces'].append(
            filter_dict(
                interface, ['uuid', 'network_uuid', 'macaddr', 'order',
                            'ipv4', 'floating', 'model']))
        return

    if ctx.obj['OUTPUT'] == 'pretty':
        format_string = '    %-8s: %s'
        print(format_string % ('uuid', interface['uuid']))
        print(format_string % ('network', interface['network_uuid']))
        print(format_string % ('macaddr', interface['macaddr']))
        print(format_string % ('order', interface['order']))
        print(format_string % ('ipv4', interface['ipv4']))
        print(format_string % ('floating', interface['floating']))
        print(format_string % ('model', interface['model']))
    else:
        print('iface,%s,%s,%s,%s,%s,%s,%s'
              % (interface['uuid'], interface['network_uuid'],
                 interface['macaddr'], interface['order'], interface['ipv4'],
                 interface['floating'], interface['model']))


@interface.command(name='show', help='Show an interface')
@click.argument('interface_uuid', type=click.STRING,
                autocompletion=_get_instance_interfaces)
@click.pass_context
def interface_show(ctx, interface_uuid=None):
    interface = CLIENT.get_interface(interface_uuid)

    if ctx.obj['OUTPUT'] == 'json':
        out = {'network_interfaces': []}
        _show_interface(ctx, interface, out)
        print(json.dumps(out, indent=4, sort_keys=True))
        return

    if ctx.obj['OUTPUT'] == 'pretty':
        print('Interface:')
    else:
        print('iface,interface uuid,network uuid,'
              'macaddr,order,ipv4,floating,model')

    _show_interface(ctx, interface)


@interface.command(name='float',
                   help='Add a floating IP to an interface')
@click.argument('interface_uuid', type=click.STRING,
                autocompletion=_get_instance_interfaces)
@click.pass_context
def interface_float(ctx, interface_uuid=None):
    CLIENT.float_interface(interface_uuid)
    if ctx.obj['OUTPUT'] == 'json':
        print('{}')


@interface.command(name='defloat',
                   help='Remove a floating IP to an interface')
@click.argument('interface_uuid', type=click.STRING,
                autocompletion=_get_instance_interfaces)
@click.pass_context
def interface_defloat(ctx, interface_uuid=None):
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
