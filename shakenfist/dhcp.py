# Copyright 2020 Michael Still

import jinja2
import os
import psutil
import shutil
import signal

from shakenfist.config import config
from shakenfist import instance
from shakenfist import networkinterface
from shakenfist import util


class DHCP(object):
    def __init__(self, network, interface):
        self.network = network

        self.subst = {
            'config_dir': os.path.join(
                config.STORAGE_PATH, 'dhcp', self.network.uuid),
            'zone': config.ZONE,

            'router': network.router,
            'dhcp_start': network.dhcp_start,
            'netmask': network.netmask,
            'broadcast': network.broadcast,
            'dns_server': config.DNS_SERVER,
            'mtu': config.MAX_HYPERVISOR_MTU - 50,

            'netns': self.network.uuid,
            'in_netns': 'ip netns exec %s' % self.network.uuid,
            'interface': interface
        }

    def __str__(self):
        return 'dhcp(%s)' % self.network.uuid

    def unique_label(self):
        return ('dhcp', self.network.uuid)

    def _read_template(self, template):
        with open(os.path.join(config.STORAGE_PATH, template)) as f:
            return jinja2.Template(f.read())

    def _make_config(self):
        if not os.path.exists(self.subst['config_dir']):
            os.makedirs(self.subst['config_dir'], exist_ok=True)

        t = self._read_template('dhcp.tmpl')
        c = t.render(self.subst)

        with open(os.path.join(self.subst['config_dir'],
                               'config'), 'w') as f:
            f.write(c)

    def _make_hosts(self):
        if not os.path.exists(self.subst['config_dir']):
            os.makedirs(self.subst['config_dir'], exist_ok=True)

        t = self._read_template('dhcphosts.tmpl')

        instances = []
        for ni in networkinterface.interfaces_for_network(self.network):
            inst = instance.Instance.from_db(ni.instance_uuid)
            if not inst:
                continue

            instances.append(
                {
                    'uuid': ni.instance_uuid,
                    'macaddr': ni.macaddr,
                    'ipv4': ni.ipv4,
                    'name': inst.name.replace(',', '')
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
        pid = self.get_pid()
        if pid:
            if not psutil.pid_exists(pid):
                return False
            os.kill(pid, sig)
            return True
        return False

    def get_pid(self):
        pid_file = os.path.join(self.subst['config_dir'], 'pid')
        if os.path.exists(pid_file):
            with open(pid_file) as f:
                pid = int(f.read())
                return pid
        return None

    def remove_dhcpd(self):
        self._send_signal(signal.SIGKILL)
        self._remove_config()

    def restart_dhcpd(self):
        if not os.path.exists('/var/run/netns/%(netns)s' % self.subst):
            return

        self._make_config()
        self._make_hosts()
        if not self._send_signal(signal.SIGHUP):
            util.execute(None,
                         '%(in_netns)s dnsmasq --conf-file=%(config_dir)s/config' % self.subst)
