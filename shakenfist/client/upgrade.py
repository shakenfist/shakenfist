# Copyright 2020 Michael Still

import etcd3
import ipaddress
import json

from shakenfist import db
from shakenfist import util

# Very simple data upgrader


def clean_events_mesh_operations(etcd_client):
    # TODO(andy): This can be removed when older versions do not exist

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
    etcd_client = etcd3.client()

    versions = {}
    for node in db.get_nodes():
        versions.setdefault(node.get('version', 'unknown'), 0)
        versions[node.get('version', 'unknown')] += 1

    print('Deployed versions:')
    for version in sorted(versions):
        print(' - %s: %s' % (version, versions[version]))
    print()

    min_version = None
    if not versions:
        min_version = '0.2'
    elif 'unknown' in versions:
        min_version = '0.2'
    else:
        min_version = sorted(versions)[0]
    print('Minimum version is %s' % min_version)

    elems = min_version.split('.')
    major = int(elems[0])
    minor = int(elems[1])

    if major == 0:
        if minor == 2:
            clean_events_mesh_operations(etcd_client)

        elif minor == 3:
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
                    for ni in db.get_network_interfaces(n['uuid']):
                        db.enqueue_instance_error(
                            ni['instance_uuid'], 'Instance was on invalid network at upgrade.')

                    # NOTE(mikal): I feel like I _should_ queue a network delete here to
                    # do it properly, but we don't actually have a network delete queue
                    # task at the moment. Here's a comment to prompt that refactor when
                    # the time comes... netobj.delete()
                    etcd_client.put(
                        '/sf/attribute/network/%s' % n['uuid'],
                        json.dumps({'state': 'deleted'}, indent=4, sort_keys=True))
                    print('--> Removed bad network %s' % n['uuid'])

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

                    network['version'] = 2
                    etcd_client.put(
                        '/sf/network/%s' % network['uuid'],
                        json.dumps(network, indent=4, sort_keys=True))
                    print('--> Upgraded network %s to version 2'
                          % network['uuid'])

            # Upgrade instances to the new attribute style
            for data, _ in etcd_client.get_prefix('/sf/instance/'):
                instance = json.loads(data.decode('utf-8'))
                if int(instance.get('version', 0)) < 2:
                    data = {}
                    for attr in ['node', 'placement_attempts']:
                        if instance.get(attr):
                            data[attr] = instance[attr]
                            del instance[attr]
                    etcd_client.put(
                        '/sf/attribute/instance/%s/placement' % instance['uuid'],
                        json.dumps(data, indent=4, sort_keys=True))

                    if 'enforced_deletes' in instance:
                        data = {'count': instance.get('enforced_deletes', 0)}
                        del instance['enforced_deletes']
                        etcd_client.put(
                            '/sf/attribute/instance/%s/enforce_deletes' % instance['uuid'],
                            json.dumps(data, indent=4, sort_keys=True))

                    if 'block_devices' in instance:
                        data = {'block_devices': instance.get(
                            'block_devices', 0)}
                        del instance['block_devices']
                        etcd_client.put(
                            '/sf/attribute/instance/%s/block_devices' % instance['uuid'],
                            json.dumps(data, indent=4, sort_keys=True))

                    data = {}
                    for attr in ['state', 'state_updated', 'error_message']:
                        if instance.get(attr):
                            data[attr] = instance[attr]
                            del instance[attr]
                    etcd_client.put(
                        '/sf/attribute/instance/%s/state' % instance['uuid'],
                        json.dumps(data, indent=4, sort_keys=True))

                    data = {}
                    for attr in ['power_state', 'power_state_previous',
                                 'power_state_updated']:
                        if instance.get(attr):
                            data[attr] = instance[attr]
                            del instance[attr]
                    etcd_client.put(
                        '/sf/attribute/instance/%s/power_state' % instance['uuid'],
                        json.dumps(data, indent=4, sort_keys=True))

                    data = {}
                    for attr in ['console_port', 'vdi_port']:
                        if instance.get(attr):
                            data[attr] = instance[attr]
                            del instance[attr]
                    etcd_client.put(
                        '/sf/attribute/instance/%s/ports' % instance['uuid'],
                        json.dumps(data, indent=4, sort_keys=True))

                    # These fields were set in code to v0.3.3, but never used
                    for key in ['node_history', 'requested_placement']:
                        if key in instance:
                            del instance[key]

                    instance['version'] = 2
                    etcd_client.put(
                        '/sf/instance/%s' % instance['uuid'],
                        json.dumps(instance, indent=4, sort_keys=True))
                    print('--> Upgraded instance %s to version 2'
                          % instance['uuid'])


if __name__ == '__main__':
    main()
