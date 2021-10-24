# Copyright 2020 Michael Still

import json

from shakenfist import etcd
from shakenfist.util import process as util_process

# Very simple data upgrader


def clean_events_mesh_operations(etcd_client):
    # TODO(andy): This can be removed when older releases do not exist

    # We probably need to cleanup excess network mesh events. We also need to
    # try and fetch small batches because of limits in the amount of data etcd3
    # can return at one time.

    # Save time and use the already available etcdctl client.
    net_keys, stderr = util_process.execute(None,
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

    for data, _ in etcd_client.get_prefix('/sf/node/'):
        n = json.loads(data.decode('utf-8'))

        observed = etcd_client.get(
            '/sf/attribute/node/%s/observed' % n['fqdn'])
        if not observed:
            continue

        observed = json.loads(observed[0].decode('utf-8'))
        release = observed['release']
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
            clean_events_mesh_operations(etcd_client)

            # Find version 3 instances and migrate them to version 4
            for data, metadata in etcd_client.get_prefix('/sf/instance/'):
                i = json.loads(data.decode('utf-8'))
                if i.get('version') == 3:
                    i['configdrive'] = 'openstack-disk'
                    i['version'] = 4
                    etcd_client.put(metadata['key'],
                                    json.dumps(i, indent=4, sort_keys=True))


if __name__ == '__main__':
    main()
