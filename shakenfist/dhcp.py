# Copyright 2020 Michael Still

import jinja2
import logging
from logging import handlers as logging_handlers
import os
import psutil
import shutil
import signal

from oslo_concurrency import processutils

from shakenfist import config
from shakenfist import db
from shakenfist import net

LOG = logging.getLogger(__file__)
LOG.setLevel(logging.INFO)
LOG.addHandler(logging_handlers.SysLogHandler(address='/dev/log'))


class DHCP(object):
    def __init__(self, network_uuid, interface):
        self.network_uuid = network_uuid

        n = net.from_db(self.network_uuid)
        self.subst = {
            'config_dir': os.path.join(
                config.parsed.get('STORAGE_PATH'), 'dhcp', self.network_uuid),
            'zone': config.parsed.get('ZONE'),

            'router': n.router,
            'dhcp_start': n.dhcp_start,
            'netmask': n.netmask,
            'broadcast': n.broadcast,

            'in_netns': 'ip netns exec %s' % self.network_uuid,
            'interface': interface
        }

    def __str__(self):
        return 'dhcp(%s)' % self.network_uuid

    def get_describing_tuple(self):
        return ('dhcp', self.network_uuid)

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
        for ni in list(db.get_network_interfaces(self.network_uuid)):
            hostname = db.get_instance(ni['instance_uuid'])
            instances.append(
                {
                    'uuid': ni['instance_uuid'],
                    'macaddr': ni['macaddr'],
                    'ipv4': ni['ipv4'],
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
                '%(in_netns)s dnsmasq --conf-file=%(config_dir)s/config' % self.subst,
                shell=True)
