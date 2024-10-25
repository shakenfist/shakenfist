import os
import signal
import time

from shakenfist import instance
from shakenfist import networkinterface
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.exceptions import NatOnlyNetworksShouldNotHaveDnsMasq
from shakenfist.managed_executables import managedexecutable
from shakenfist.util import process as util_process


class DnsMasq(managedexecutable.ManagedExecutable):
    # Note that this slightly confusing object type is required for historical
    # reasons so that objects and config files don't need to be renamed on
    # upgrade.
    object_type = 'dhcp'
    initial_version = 1
    current_version = 2

    def __init__(self, static_values):
        self.upgrade(static_values)

        super().__init__(static_values)

        self.__provide_dhcp = static_values['provide_dhcp']
        self.__provide_dns = static_values['provide_dns']

        # These aren't really static values as they're not stored in the db
        self.__interface = None
        self.__network = None

        self._read_template('config', 'dhcp.tmpl')
        if self.provide_dhcp:
            self._read_template('hosts', 'dhcphosts.tmpl')
        if self.provide_dns:
            self._read_template('dnshosts', 'dnshosts.tmpl')

    @classmethod
    def _upgrade_step_1_to_2(cls, static_values):
        static_values['provide_dhcp'] = True
        static_values['provide_dns'] = False

    # Static values
    @property
    def interface(self):
        return self.__interface

    @interface.setter
    def interface(self, value):
        self.__interface = value

    @property
    def network(self):
        return self.__network

    @network.setter
    def network(self, value):
        self.__network = value

    @property
    def provide_dhcp(self):
        return self.__provide_dhcp

    @property
    def provide_dns(self):
        return self.__provide_dns

    # Helpers
    @classmethod
    def new(cls, owner_network, provide_dhcp=True, provide_nat=True,
            provide_dns=False):
        if not provide_dhcp and not provide_dns:
            raise NatOnlyNetworksShouldNotHaveDnsMasq()

        u = owner_network.uuid
        n = cls.from_db(u, suppress_failure_audit=True)
        if n:
            n.interface = owner_network._vx_veth_inner
            n.network = owner_network
            return n

        uniq = owner_network.unique_label()
        cls._db_create(u, {
            'uuid': u,
            'namespace': owner_network.namespace,
            'owner_type': uniq[0],
            'owner_uuid': uniq[1],
            'provide_dhcp': provide_dhcp,
            'provide_nat': provide_nat,
            'provide_dns': provide_dns,
            'version': cls.current_version
        })
        n = cls.from_db(u)
        n.state = cls.STATE_CREATED
        n.interface = owner_network._vx_veth_inner
        n.network = owner_network
        return n

    def subst_dict(self):
        instances, _ = self._enumerate_leases()

        # NOTE(mikal): provide_nat comes from the network subst dictionary, not
        # the dnsmasq one.
        d = super().subst_dict()
        d.update({
            'zone': config.ZONE,
            'dns_server': config.DNS_SERVER,
            'mtu': config.MAX_HYPERVISOR_MTU - 50,
            'interface': self.interface,
            'instances': instances,
            'provide_dhcp': self.provide_dhcp,
            'provide_dns': self.provide_dns
        })
        d.update(self.network.subst_dict())
        return d

    def _enumerate_leases(self):
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

        return instances, allowed_leases

    def _remove_invalid_leases(self, allowed_leases):
        lf = os.path.join(self.config_directory, 'leases')
        if not os.path.exists(lf):
            return False

        needs_restart = False
        with open(lf) as lin, open(lf + '.new', 'w') as lout:
            for line in lin.readlines():
                # 1672899136 02:00:00:55:04:a2 172.10.0.8 client *
                # ^--expiry  ^--mac            ^--ip      ^-- hostname
                elems = line.split(' ')
                expiry = int(elems[0])

                # The lease is expired, so we don't care
                if time.time() > expiry:
                    lout.write(line)
                    continue

                # The lease is valid, so keep it
                if elems[1] in allowed_leases:
                    lout.write(line)
                    continue

                # Otherwise, this lease is invalid and we'll need to do a
                # hard restart
                needs_restart = True
                self.add_event(EVENT_TYPE_AUDIT, 'detected invalid DHCP lease',
                               extra={
                                   'expiry': expiry,
                                   'remaining_life': time.time() - expiry,
                                   'macaddr': elems[1],
                                   'ipv4': elems[2],
                                   'hostname': elems[3]
                               })

        return needs_restart

    def remove_lease(self, ipv4, macaddr):
        subst = self.subst_dict()
        subst.update({
            'ipv4': ipv4,
            'macaddr': macaddr
        })
        util_process.execute(
            None, 'dhcp_release %(interface)s %(ipv4)s %(macaddr)s' % subst,
            namespace=self.network.uuid)
        self.add_event(EVENT_TYPE_AUDIT, 'released a DHCP lease',
                       extra={
                           'macaddr': macaddr,
                           'ipv4': ipv4
                       })

    def restart(self):
        if not os.path.exists('/var/run/netns/%s' % self.network.uuid):
            return

        _, allowed_leases = self._enumerate_leases()
        needs_start = False

        self._make_config()

        if self._remove_invalid_leases(allowed_leases):
            # We found invalid leases and need to do a hard restart of dnsmasq
            self._send_signal(signal.SIGKILL)
            leases_file = os.path.join(self.config_directory, 'leases')
            os.unlink(leases_file)
            os.rename(leases_file + '.new', leases_file)
            needs_start = True

        elif not self._send_signal(signal.SIGHUP):
            # We failed to find a PID to SIGHUP and therefore must start
            # dnsmasq
            needs_start = True

        if needs_start:
            util_process.execute(
                None, 'dnsmasq --conf-file=%s/config' % self.config_directory,
                namespace=self.network.uuid)
            self.add_event(EVENT_TYPE_AUDIT, 'started')
