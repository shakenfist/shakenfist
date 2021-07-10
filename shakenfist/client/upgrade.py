# Copyright 2020 Michael Still

import ipaddress
import json
import time

from shakenfist import baseobject
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist import etcd
from shakenfist import instance
from shakenfist import networkinterface
from shakenfist.node import Node
from shakenfist import util

# Very simple data upgrader


def clean_events_mesh_operations(etcd_client):
    # TODO(andy): This can be removed when older releases do not exist

    # We probably need to cleanup excess network mesh events. We also need to
    # try and fetch small batches because of limits in the amount of data etcd3
    # can return at one time.

    # Save time and use the already available etcdctl client.
    net_keys, stderr = util.execute(None,
                                    'etcdctl get --prefix /sf/event/network/ | grep sf/event',
                                    check_exit_code=[0, 1])
    if stderr:
        print('ERROR: Unable to retrieve network keys:%s' % stderr)
        return

    # Split network events into networks
    network_events = {}
    for key in net_keys.split('\n'):
        if not key:
            continue
        _blank, _sf, _event, _network, uuid, _time = key.split('/')
        network_events.setdefault(uuid, []).append(key)

    # Delete all but last 50 events
    count = 0
    for keys in network_events.values():
        for k in keys[:-50]:
            print('--> Removing verbose network event %s' % k)
            etcd_client.delete(k)
            count += 1
    print(' - Cleaned up %d old network mesh events' % count)


def main():
    etcd_client = etcd.WrappedEtcdClient()

    releases = {}
    old_style_nodes = []

    for data, _ in etcd_client.get_prefix('/sf/node/'):
        n = json.loads(data.decode('utf-8'))

        observed = etcd_client.get(
            '/sf/attribute/node/%s/observed' % n['fqdn'])
        if observed:
            # New style node
            observed = json.loads(observed[0].decode('utf-8'))
            release = observed['release']
        else:
            # Old style node
            release = n.get('version', 'unknown')
            old_style_nodes.append(n['fqdn'])

        releases.setdefault(release, 0)
        releases[release] += 1

    print('Deployed releases:')
    for release in sorted(releases):
        print(' - %s: %s' % (release, releases[release]))
    print()

    min_release = None
    if not releases:
        min_release = '0.2'
    elif 'unknown' in releases:
        min_release = '0.2'
    else:
        min_release = sorted(releases)[0]
    print('Minimum release is %s' % min_release)

    elems = min_release.split('.')
    major = int(elems[0])
    minor = int(elems[1])

    if major == 0:
        if minor <= 4:
            # Upgrade networkinterfaces to the new attribute style
            for data, metadata in etcd_client.get_prefix('/sf/networkinterface/'):
                ni = json.loads(data.decode('utf-8'))
                if int(ni.get('version', 0)) < 2:
                    etcd_client.put(
                        '/sf/attribute/image/%s/floating' % ni['uuid'],
                        json.dumps({'floating_address': ni.get('floating')},
                                   indent=4, sort_keys=True))
                    if 'floating' in ni:
                        del ni['floating']

                    etcd_client.put(
                        '/sf/attribute/image/%s/state' % ni['uuid'],
                        json.dumps({
                            'state': ni['state'],
                            'state_updated': ni['state_updated']
                        }, indent=4, sort_keys=True))
                    del ni['state']
                    del ni['state_updated']

                    ni['version'] = 2
                    etcd_client.put(
                        metadata['key'], json.dumps(ni, indent=4, sort_keys=True))
                    print('--> Upgraded networkinterface %s to version 2'
                          % ni['uuid'])

            # Upgrade ipmanagers to v2, deleting strays while we're at it
            for data, metadata in etcd_client.get_prefix('/sf/ipmanager/'):
                network_uuid = metadata['key'].decode('utf-8').split('/')[-1]

                if not etcd_client.get('/sf/network/%s' % network_uuid):
                    print('--> Deleted stray ipmanager %s' % network_uuid)
                    etcd_client.delete(metadata['key'])
                    continue

                ipm = json.loads(data.decode('utf-8'))
                if 'ipmanager.v1' in ipm:
                    ipm['ipmanager.v2'] = {
                        'ipblock': ipm['ipmanager.v1']['ipblock'],
                        'in_use': {},
                        'uuid': network_uuid
                    }
                    for elem in ipm['ipmanager.v1']['in_use']:
                        ipm['ipmanager.v2']['in_use'][elem] = ('unknown', None)

                    del ipm['ipmanager.v1']

                if ipm['ipmanager.v2']['uuid'] == 'floating':
                    ipblock_obj = ipaddress.ip_network(ipm['ipmanager.v2']['ipblock'],
                                                       strict=False)
                    for addr in [str(ipblock_obj[0]),
                                 str(ipblock_obj[1]),
                                 str(ipblock_obj.broadcast_address),
                                 str(ipblock_obj.network_address)]:
                        ipm['ipmanager.v2']['in_use'][addr] = (
                            'ipmanager', network_uuid)

                etcd_client.put(
                    metadata['key'], json.dumps(ipm, indent=4, sort_keys=True))
                print('--> Upgraded ipmanager %s to version 2' % network_uuid)

            # Bump instance version to support UEFI
            for data, metadata in etcd_client.get_prefix('/sf/instance/'):
                i = json.loads(data.decode('utf-8'))
                if i['version'] == 2:
                    i['uefi'] = False
                    i['version'] = 3
                    etcd_client.put(
                        metadata['key'], json.dumps(i, indent=4, sort_keys=True))

            clean_events_mesh_operations(etcd_client)

        if minor <= 3:
            # Upgrade instances to the new attribute style (this needs to
            # happen before we upgrade networks below).
            for data, _ in etcd_client.get_prefix('/sf/instance/'):
                inst = json.loads(data.decode('utf-8'))
                if int(inst.get('version', 0)) < 2:
                    data = {}
                    for attr in ['node', 'placement_attempts']:
                        if inst.get(attr):
                            data[attr] = inst[attr]
                            del inst[attr]
                    etcd_client.put(
                        '/sf/attribute/instance/%s/placement' % inst['uuid'],
                        json.dumps(data, indent=4, sort_keys=True))

                    if 'enforced_deletes' in inst:
                        data = {'count': inst.get('enforced_deletes', 0)}
                        del inst['enforced_deletes']
                        etcd_client.put(
                            '/sf/attribute/instance/%s/enforce_deletes' % inst['uuid'],
                            json.dumps(data, indent=4, sort_keys=True))

                    if 'block_devices' in inst:
                        data = {'block_devices': inst.get(
                            'block_devices', 0)}
                        del inst['block_devices']
                        etcd_client.put(
                            '/sf/attribute/instance/%s/block_devices' % inst['uuid'],
                            json.dumps(data, indent=4, sort_keys=True))

                    state = baseobject.State(inst.get('state'),
                                             inst.get('state_updated'))
                    for attr in ['state', 'state_updated']:
                        inst.pop(attr, None)
                    etcd_client.put(
                        '/sf/attribute/instance/%s/state' % inst['uuid'],
                        json.dumps(state.obj_dict(), indent=4, sort_keys=True))

                    err_msg = inst.get('error_message')
                    if err_msg:
                        inst.pop('error_message', None)
                        etcd_client.put(
                            '/sf/attribute/instance/%s/error' % inst['uuid'],
                            json.dumps({'message': err_msg},
                                       indent=4, sort_keys=True))

                    data = {}
                    for attr in ['power_state', 'power_state_previous',
                                 'power_state_updated']:
                        if inst.get(attr):
                            data[attr] = inst[attr]
                            del inst[attr]
                    etcd_client.put(
                        '/sf/attribute/instance/%s/power_state' % inst['uuid'],
                        json.dumps(data, indent=4, sort_keys=True))

                    data = {}
                    for attr in ['console_port', 'vdi_port']:
                        if inst.get(attr):
                            data[attr] = inst[attr]
                            del inst[attr]
                    etcd_client.put(
                        '/sf/attribute/instance/%s/ports' % inst['uuid'],
                        json.dumps(data, indent=4, sort_keys=True))

                    # These fields were set in code to v0.3.3, but never used
                    for key in ['node_history', 'requested_placement']:
                        if key in inst:
                            del inst[key]

                    inst['version'] = 2
                    etcd_client.put(
                        '/sf/instance/%s' % inst['uuid'],
                        json.dumps(inst, indent=4, sort_keys=True))
                    print('--> Upgraded instance %s to version 2'
                          % inst['uuid'])

            # Upgrade images to the new attribute style
            for data, metadata in etcd_client.get_prefix('/sf/image/'):
                image_node = '/'.join(
                    metadata['key'].decode('utf-8').split('/')[-2:])
                image = json.loads(data.decode('utf-8'))
                if int(image.get('version', 0)) < 2:
                    data = {}
                    RENAMES = {
                        'fetched': 'fetched_at',
                        'file_version': 'sequence'
                    }
                    for attr in ['size', 'modified', 'fetched', 'file_version']:
                        if image.get(attr):
                            data[RENAMES.get(attr, attr)] = image[attr]
                            del image[attr]
                    etcd_client.put(
                        ('/sf/attribute/image/%s/download_%d'
                         % (image_node, image.get('sequence', 0))),
                        json.dumps(data, indent=4, sort_keys=True))

                    if image.get('checksum'):
                        etcd_client.put(
                            '/sf/attribute/image/%s/latest_checksum' % image_node,
                            json.dumps({'checksum': image.get('checksum')},
                                       indent=4, sort_keys=True))
                        del image['checksum']

                    etcd_client.put(
                        '/sf/attribute/image/%s/state' % image_node,
                        json.dumps({
                            'state': dbo.STATE_CREATED,
                            'state_updated': time.time()
                        }, indent=4, sort_keys=True))

                    new = baseobject.State(dbo.STATE_CREATED, time.time())
                    etcd_client.put(
                        '/sf/attribute/image/%s/state' % image_node,
                        json.dumps(new.obj_dict(), indent=4, sort_keys=True))

                    image['uuid'] = image_node
                    image['ref'], image['node'] = image_node.split('/')
                    image['version'] = 2
                    etcd_client.put(metadata['key'],
                                    json.dumps(image, indent=4, sort_keys=True))
                    print('--> Upgraded image %s to version 2' % image_node)

            # Find invalid networks
            for data, _ in etcd_client.get_prefix('/sf/network/'):
                n = json.loads(data.decode('utf-8'))
                bad = False
                try:
                    netblock = ipaddress.ip_network(n['netblock'])
                    if netblock.num_addresses < 8:
                        bad = True
                except ValueError:
                    bad = True

                if bad:
                    for ni in networkinterface.interfaces_for_network(n):
                        inst = instance.Instance.from_db(ni.instance_uuid)
                        if inst:
                            inst.enqueue_delete_due_error(
                                'Instance was on invalid network at upgrade.')
                        else:
                            print('--> Instance %s on invalid network, does '
                                  'not exist in DB' % ni.instance_uuid)

                    # NOTE(mikal): we have to hard delete this network here, or
                    # it will cause a crash later in the Networks iterator.
                    etcd_client.delete('/sf/network/%s' % n['uuid'])
                    etcd_client.delete(
                        '/sf/attribute/network/%s/state' % n['uuid'])
                    print('--> Deleted invalid network %s (netblock too small)'
                          % n['uuid'])
                    continue

                # Upgrade networks to the new attribute style
                network = json.loads(data.decode('utf-8'))
                if int(network.get('version', 0)) < 2:
                    data = {}
                    for attr in ['state', 'state_updated', 'error_message']:
                        if network.get(attr):
                            data[attr] = network[attr]
                            del network[attr]
                    etcd_client.put(
                        '/sf/attribute/network/%s/state' % network['uuid'],
                        json.dumps(data, indent=4, sort_keys=True))

                    if 'floating_gateway' in network:
                        etcd_client.put(
                            '/sf/attribute/network/%s/routing' % network['uuid'],
                            json.dumps({'floating_gateway': network['floating_gateway']},
                                       indent=4, sort_keys=True))
                        del network['floating_gateway']

                    new = baseobject.State(dbo.STATE_CREATED, time.time())
                    etcd_client.put(
                        '/sf/attribute/network/%s/state' % n['uuid'],
                        json.dumps(new.obj_dict(), indent=4, sort_keys=True))

                    network['version'] = 2
                    etcd_client.put(
                        '/sf/network/%s' % network['uuid'],
                        json.dumps(network, indent=4, sort_keys=True))
                    print('--> Upgraded network %s to version 2'
                          % network['uuid'])

        if minor <= 4:
            for old_name in old_style_nodes:
                # We do not observe() the new node, or set its release,
                # because we might not be running on that node and might
                # get the details wrong. Let the node do that thing.
                data = etcd_client.get('/sf/node/%s' % old_name)
                old_node = json.loads(data[0].decode('utf-8'))
                etcd_client.delete('/sf/node/%s' % old_name)

                n = Node.new(old_node['fqdn'], old_node['ip'])
                n._db_set_attribute('observed', {
                    'at': old_node['lastseen'],
                    'release': old_node['version']
                })
                print('--> Upgraded node %s to version 2' % old_name)


if __name__ == '__main__':
    main()
