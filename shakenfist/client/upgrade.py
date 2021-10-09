# Copyright 2020 Michael Still

from collections import defaultdict
import json

from shakenfist import etcd

# Very simple data upgrader


def main():
    etcd_client = etcd.WrappedEtcdClient()

    releases = defaultdict(int)
    for data, _ in etcd_client.get_prefix('/sf/node/'):
        n = json.loads(data.decode('utf-8'))

        observed = etcd_client.get(
            '/sf/attribute/node/%s/observed' % n['fqdn'])
        if not observed:
            continue

        observed = json.loads(observed[0].decode('utf-8'))
        release = observed['release']
        releases[release] += 1

    print('Deployed releases:')
    for release in sorted(releases):
        print(' - %s: %s' % (release, releases[release]))
    print()

    min_release = None
    if not releases:
        min_release = '0.4'
    elif 'unknown' in releases:
        min_release = '0.4'
    else:
        min_release = sorted(releases)[0]
    print('Minimum release is %s' % min_release)

    elems = min_release.split('.')
    major = int(elems[0])
    minor = int(elems[1])

    if major == 0:
        if minor <= 4:
            for data, metadata in etcd_client.get_prefix('/sf/instance/'):
                i = json.loads(data.decode('utf-8'))
                changed = False

                # Find version 3 instances and migrate them to version 4
                if i.get('version') == 3:
                    i['configdrive'] = 'openstack-disk'
                    i['version'] = 4
                    changed = True

                # Find version 4 instances and migrate them to version 5
                if i.get('version') == 4:
                    i['nvram_template'] = None
                    i['secure_boot'] = False
                    i['version'] = 5
                    changed = True

                # Find version 5 instances and migrate them to version 6
                if i.get('version') == 5:
                    i['machine_type'] = 'pc'
                    i['version'] = 6
                    changed = True

                if changed:
                    print('--> Upgraded instance %s to version %d'
                          % (i['uuid'], i['version']))
                    etcd_client.put(metadata['key'],
                                    json.dumps(i, indent=4, sort_keys=True))


if __name__ == '__main__':
    main()
