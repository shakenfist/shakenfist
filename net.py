# Copyright 2020 Michael Still

import logging
import re

from oslo_concurrency import processutils

import config
import util


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


class Network(object):
    # NOTE(mikal): it should be noted that the maximum interface name length
    # on Linux is 15 user visible characters.
    def __init__(self, uuid=None, vxlan_id=1, provide_dhcp=False,
                 physical_nic='eth0', nodes=None):
        self.uuid = uuid
        self.vxlan_id = vxlan_id
        self.provide_dhcp = provide_dhcp
        self.physical_nic = physical_nic

        self.vx_interface = 'vxlan-%s' % self.vxlan_id
        self.vx_bridge = 'br-%s' % self.vx_interface

        if nodes:
            self.nodes = nodes
            if config.parsed.get('NODE_IP') in nodes:
                nodes.remove(config.parsed.get('NODE_IP'))
        else:
            self.nodes = []

        if self.provide_dhcp:
            self.dhcp_interface = 'dhcp-%s' % self.vx_interface
        else:
            self.dhcp_interface = None

    def __str__(self):
        return 'network(%s, vxid %s)' % (self.uuid, self.vxlan_id)

    def _subst_dict(self):
        return {
            'vx_id': self.vxlan_id,
            'vx_interface': self.vx_interface,
            'vx_bridge': self.vx_bridge,
            'dhcp_interface': self.dhcp_interface,
            'phy_interface': self.physical_nic,
        }

    def create(self):
        subst = self._subst_dict()

        if not util.check_for_interface(self.vx_interface):
            with util.RecordedOperation('create vxlan interface', self) as ro:
                processutils.execute(
                    'ip link add %(vx_interface)s type vxlan id %(vx_id)s '
                    'dev %(phy_interface)s dstport 0'
                    % subst, shell=True)

        if not util.check_for_interface(self.vx_bridge):
            with util.RecordedOperation('create vxlan bridge', self) as ro:
                processutils.execute(
                    'ip link add %(vx_bridge)s type bridge' % subst, shell=True)
                processutils.execute(
                    'ip link set %(vx_interface)s master %(vx_bridge)s' % subst,
                    shell=True)
                processutils.execute(
                    'ip link set %(vx_interface)s up' % subst, shell=True)
                processutils.execute(
                    'ip link set %(vx_bridge)s up' % subst, shell=True)

        if self.provide_dhcp and not util.check_for_interface(self.dhcp_interface):
            with util.RecordedOperation('create dhcp interface', self) as ro:
                processutils.execute(
                    'ip link add %(dhcp_interface)s type veth peer name '
                    '%(dhcp_interface)s-peer' % subst, shell=True)
                processutils.execute(
                    'ip link set %(dhcp_interface)s-peer master %(vx_bridge)s'
                    % subst, shell=True)
                processutils.execute(
                    'ip link set %(dhcp_interface)s up' % subst, shell=True)
                processutils.execute(
                    'ip link set %(dhcp_interface)s-peer up' % subst, shell=True)
                processutils.execute(
                    'ip addr add 192.168.200.1/24 dev %(dhcp_interface)s' % subst,
                    shell=True)

                # Create DHCP config file

                #'docker run -it --rm --init --net host -v /srv/shakenfist/dhcp:/data networkboot/dhcpd %(dhcp_interface)s'
            pass

        self.ensure_mesh(self.nodes)

    def discover_mesh(self):
        mesh_re = re.compile('00: 00: 00: 00: 00: 00 dst (.*) self permanent')

        with util.RecordedOperation('discover mesh', self) as ro:
            stdout, _ = processutils.execute(
                'bridge fdb show brport %(vx_interface)s' % self._subst_dict(),
                shell=True)

            for line in stdout.split('\n'):
                m = mesh_re.match(line)
                if m:
                    yield m.group(1)

    def ensure_mesh(self, all_nodes):
        with util.RecordedOperation('ensure mesh', self) as ro:
            for node in self.discover_mesh():
                if node in all_nodes:
                    all_nodes.remove(node)
                else:
                    self._remove_mesh_element(node)

            for node in all_nodes:
                self._add_mesh_element(node)

    def _add_mesh_element(self, node):
        LOG.info('%s: Adding new mesh element %s' % (self, node))
        subst = self._subst_dict()
        subst['node'] = node
        processutils.execute(
            'bridge fdb append to 00:00:00:00:00:00 dst %(node)s dev %(vx_interface)s'
            % subst,
            shell=True)

    def _remove_mesh_element(self, node):
        LOG.info('%s: Removing excess mesh element %s' % (self, node))
        subst = self._subst_dict()
        subst['node'] = node
        processutils.execute(
            'bridge fdb del to 00:00:00:00:00:00 dst %(node)s dev %(vx_interface)s'
            % subst,
            shell=True)
