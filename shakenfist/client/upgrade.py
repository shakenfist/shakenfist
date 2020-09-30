# Copyright 2020 Michael Still

import etcd3

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


if __name__ == '__main__':
    main()
