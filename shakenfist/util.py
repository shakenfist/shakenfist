# Copyright 2020 Michael Still

import ipaddress
import logging
import random
import re
import time

from oslo_concurrency import processutils


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


class RecordedOperation():
    def __init__(self, operation, object, callback=None):
        self.operation = operation
        self.object = object
        self.callback = callback

    def __enter__(self):
        self.start_time = time.time()
        LOG.info('%s: Start %s' % (self.object, self.operation))
        if self.callback:
            self.callback({
                'event': 'start',
                'operation': self.operation,
                'object': str(self.object)
            })
        return self

    def __exit__(self, *args):
        LOG.info('%s: Finish %s, duration %.02f seconds'
                 % (self.object, self.operation,
                    time.time() - self.start_time))
        if self.callback:
            self.callback({
                'event': 'finish',
                'operation': self.operation,
                'object': str(self.object)
            })

    def heartbeat(self, status=None):
        LOG.info('%s: Heartbeat %s, status %s'
                 % (self.object, self.operation, status))
        if self.callback:
            self.callback({
                'event': 'heartbeat',
                'operation': self.operation,
                'object': str(self.object),
                'status': status
            })


def check_for_interface(name):
    _, stderr = processutils.execute(
        'ip link show %s' % name, check_exit_code=[0, 1], shell=True)
    return not stderr.rstrip('\n').endswith(' does not exist.')


def get_interface_addresses(namespace, name):
    in_namespace = ''
    if namespace:
        in_namespace = 'ip netns exec %s ' % namespace

    stdout, _ = processutils.execute(
        '%(in_namespace)sip addr show %(name)s'
        % {
            'in_namespace': in_namespace,
            'name': name
        },
        check_exit_code=[0, 1], shell=True)
    if not stdout:
        return

    inet_re = re.compile(' +inet (.*)/[0-9]+.*')
    for line in stdout.split('\n'):
        m = inet_re.match(line)
        if m:
            yield m.group(1)

    return


def nat_rules_for_ipblock(ipblock):
    out, _ = processutils.execute(
        'iptables -t nat -L POSTROUTING -n -v', shell=True)
    # Output looks like this:
    # Chain POSTROUTING (policy ACCEPT 199 packets, 18189 bytes)
    # pkts bytes target     prot opt in     out     source               destination
    #   23  1736 MASQUERADE  all  --  *      ens4    192.168.242.0/24     0.0.0.0/0

    for line in out.split('\n'):
        if line.find(str(ipblock)) != -1:
            return True

    return False
