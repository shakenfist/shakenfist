# Copyright 2020 Michael Still

import copy
import jinja2
import os
import psutil
import shutil
import signal
import time

from shakenfist.config import config
from shakenfist import instance
from shakenfist import networkinterface
from shakenfist.util import process as util_process


class DHCP:
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
            'provide_nat': network.provide_nat,

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
        os.makedirs(self.subst['config_dir'], exist_ok=True)

        t = self._read_template('dhcp.tmpl')
        c = t.render(self.subst)

        with open(os.path.join(self.subst['config_dir'], 'config'), 'w') as f:
            f.write(c)

    def _make_hosts(self):
        os.makedirs(self.subst['config_dir'], exist_ok=True)
        t = self._read_template('dhcphosts.tmpl')

        instances = []
        allowed_leases = {}

        for ni_uuid in self.network.networkinterfaces:
            ni = networkinterface.NetworkInterface.from_db(ni_uuid)
            if not ni:
                continue

            inst = instance.Instance.from_db(ni.instance_uuid)
            if not inst:
                continue

            instances.append(
                {
                    'uuid': ni.instance_uuid,
                    'macaddr': ni.macaddr,
                    'ipv4': ni.ipv4,
                    'name': inst.name.replace(',', '')
                })
            allowed_leases[ni.macaddr] = ni.ipv4

        self.subst['instances'] = instances
        c = t.render(self.subst)

        with open(os.path.join(self.subst['config_dir'], 'hosts'), 'w') as f:
            f.write(c)

        return allowed_leases

    def _remove_invalid_leases(self, allowed_leases):
        lf = os.path.join(self.subst['config_dir'], 'leases')
        if not os.path.exists(lf):
            return False

        needs_restart = False
        with open(lf) as lin, open(lf + '.new', 'w') as lout:
            for line in lin.readlines():
                # 1672899136 02:00:00:55:04:a2 172.10.0.8 client *
                # ^--expiry  ^--mac            ^--ip      ^-- hostname
                elems = line.split(' ')

                # The lease is expired, so we don't care
                if time.time() > int(elems[0]):
                    lout.write(line)
                    continue

                # The lease is valid, so keep it
                if elems[1] in allowed_leases:
                    lout.write(line)
                    continue

                # Otherwise, this lease is invalid and we'll need to do a
                # hard restart
                needs_restart = True

        return needs_restart

    def _remove_config(self):
        if os.path.exists(self.subst['config_dir']):
            shutil.rmtree(self.subst['config_dir'])

    def _send_signal(self, sig):
        pid = self.get_pid()
        if pid:
            if not psutil.pid_exists(pid):
                return False
            os.kill(pid, sig)
            if sig == signal.SIGKILL:
                try:
                    os.waitpid(pid, 0)
                except ChildProcessError:
                    pass
            return True
        return False

    def get_pid(self):
        pid_file = os.path.join(self.subst['config_dir'], 'pid')
        if os.path.exists(pid_file):
            with open(pid_file) as f:
                pid = int(f.read())
                return pid
        return None

    def remove_lease(self, ipv4, macaddr):
        subst = copy.copy(self.subst)
        subst.update({
            'ipv4': ipv4,
            'macaddr': macaddr
        })
        util_process.execute(
                None, 'dhcp_release %(interface)s %(ipv4)s %(macaddr)s' % subst,
                namespace=self.network.uuid)

    def remove_dhcpd(self):
        self._send_signal(signal.SIGKILL)
        self._remove_config()

    def restart_dhcpd(self):
        if not os.path.exists('/var/run/netns/%s' % self.network.uuid):
            return

        self._make_config()
        allowed_leases = self._make_hosts()

        needs_start = False
        if self._remove_invalid_leases(allowed_leases):
            # We found invalid leases and need to do a hard restart of dnsmasq
            self._send_signal(signal.SIGKILL)
            leases_file = os.path.join(self.subst['config_dir'], 'leases')
            os.unlink(leases_file)
            os.rename(leases_file + '.new', leases_file)
            needs_start = True

        elif not self._send_signal(signal.SIGHUP):
            # We failed to find a PID to SIGHUP and therefore must start
            # dnsmasq
            needs_start = True

        if needs_start:
            util_process.execute(
                None, 'dnsmasq --conf-file=%(config_dir)s/config' % self.subst,
                namespace=self.network.uuid)
