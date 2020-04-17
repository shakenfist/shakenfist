# Copyright 2020 Michael Still

import ipaddress
import jinja2
import logging
import os
import psutil
import shutil
import signal

from oslo_concurrency import processutils

from shakenfist import config
from shakenfist.db import impl as db
from shakenfist import util

LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


class DHCP(object):
    def __init__(self, network_uuid, interface):
        self.network_uuid = network_uuid

        n = db.get_network(self.network_uuid)
        netblock = ipaddress.ip_network(n['netblock'], strict=False)

        self.subst = {}
        self.subst['config_dir'] = os.path.join(
            config.parsed.get('STORAGE_PATH'), 'dhcp', self.network_uuid)
        self.subst['zone'] = config.parsed.get('ZONE')
        self.subst['interface'] = interface

        router, dhcp_server, dhcp_start = util.get_network_fundamentals(
            netblock)
        self.subst['router'] = router
        self.subst['dhcp_server'] = dhcp_server
        self.subst['dhcp_start'] = dhcp_start
        self.subst['netmask'] = netblock.netmask
        self.subst['broadcast'] = netblock.broadcast_address

    def __str__(self):
        return 'dhcp(%s)' % self.network_uuid

    def _read_template(self, template):
        with open(os.path.join(config.parsed.get('STORAGE_PATH'),
                               template)) as f:
            return jinja2.Template(f.read())

    def _make_config(self):
        if not os.path.exists(self.subst['config_dir']):
            os.makedirs(self.subst['config_dir'])

        t = self._read_template('dhcp.tmpl')
        c = t.render(self.subst)

        with open(os.path.join(self.subst['config_dir'],
                               'config'), 'w') as f:
            f.write(c)

    def _make_hosts(self):
        if not os.path.exists(self.subst['config_dir']):
            os.makedirs(self.subst['config_dir'])

        t = self._read_template('dhcphosts.tmpl')

        instances = []
        interfaces = list(db.get_network_interfaces(self.network_uuid))
        for interface in interfaces:
            hostname = db.get_instance(interface['instance_uuid'])
            instances.append(
                {
                    'uuid': interface['instance_uuid'],
                    'macaddr': interface['macaddr'],
                    'ipv4': interface['ipv4'],
                    'name': hostname['name'].replace(',', '')
                }
            )
        self.subst['instances'] = instances
        c = t.render(self.subst)

        with open(os.path.join(self.subst['config_dir'],
                               'hosts'), 'w') as f:
            f.write(c)

    def _remove_config(self):
        if os.path.exists(self.subst['config_dir']):
            shutil.rmtree(self.subst['config_dir'])

    def _send_signal(self, sig):
        pidfile = os.path.join(self.subst['config_dir'], 'pid')
        if os.path.exists(pidfile):
            with open(pidfile) as f:
                pid = int(f.read())

            if not psutil.pid_exists(pid):
                return False

            os.kill(pid, sig)
            return True

        return False

    def remove_dhcpd(self):
        self._send_signal(signal.SIGKILL)
        self._remove_config()

    def restart_dhcpd(self):
        self._make_config()
        self._make_hosts()
        if not self._send_signal(signal.SIGHUP):
            processutils.execute(
                'dnsmasq --conf-file=%s/config' % self.subst['config_dir'],
                shell=True)
