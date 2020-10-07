#!/usr/bin/python3

import random
import string

from shakenfist.client import apiclient


def randomName():
    letters = []
    for i in range(8):
        letters.append(random.choice(string.ascii_lowercase))
    return ''.join(letters)


def main():
    c = apiclient.Client(base_url='http://localhost:13000', verbose=True)

    # Return to a clean state
    for i in c.get_instances():
        c.delete_instance(i['uuid'])
        print('Deleted instance %s' % i['uuid'])

    for n in c.get_networks():
        c.delete_network(n['uuid'])
        print('Deleted network %s' % n['uuid'])

    # Some disk specs we randomly select from
    disks = [
        {
            'base': 'cirros',
            'size': 4,
            'type': 'disk'
        },
        {
            'base': 'ubuntu',
            'size': 8,
            'type': 'disk'
        }
    ]

    # Some network specs we randomly select from
    networks = []
    for i in range(5):
        n = c.allocate_network(
            '192.168.%d.0/24' % (50 + i), True, True, randomName())
        networks.append({
            'network_uuid': n['uuid']
        })
        print('Created network %s' % n['uuid'])

    # Launch instances until we get an error
    instances = []
    while True:
        choice = random.randint(0, 100)

        if choice == 1:
            n = c.allocate_network(
                '192.168.%d.0/24' % (50 + random.randint(0, 100)), True, True, randomName())
            networks.append({
                'network_uuid': n['uuid']
            })
            print('Created network %s' % n['uuid'])

        elif choice < 30:
            if len(instances) > 0:
                i = random.choice(instances)
                c.delete_instance(i)
                instances.remove(i)
                print('Deleted instance %s' % i)

        else:
            try:
                i = c.create_instance(
                    randomName(),
                    random.randint(1, 4),
                    random.randint(1, 4),
                    [random.choice(networks)],
                    [random.choice(disks)],
                    None,
                    None)
                instances.append(i['uuid'])
                print('Created instance %s' % i['uuid'])

            except apiclient.InsufficientResourcesException:
                print('Failed to start an instance due to insufficient resources')


if __name__ == '__main__':
    main()
