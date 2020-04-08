# Copyright 2020 Michael Still

import jinja2
import logging
import os
import shutil

from oslo_concurrency import processutils

from shakenfist import config
from shakenfist.db import impl as db
from shakenfist.net import impl as net
from shakenfist import util

LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


class DHCP(object):
    def __init__(self, network_uuid=None):
        self.network_uuid = network_uuid

        n = net.from_db(self.network_uuid)
        self.subst = n.subst_dict()
        self.subst['config_dir'] = os.path.join(
            config.parsed.get('STORAGE_PATH'), 'dhcp', self.network_uuid)

    def __str__(self):
        return 'dhcp(%s)' % self.network_uuid

    def make_config(self):
        if not os.path.exists(self.subst['config_dir']):
            LOG.debug('%s: Creating dhcp config at %s' %
                      (self, self.subst['config_dir']))
            os.makedirs(self.subst['config_dir'])

        with open(os.path.join(config.parsed.get('STORAGE_PATH'), 'dhcp.tmpl')) as f:
            t = jinja2.Template(f.read())

        instances = []
        interfaces = list(db.get_network_interfaces(self.network_uuid))
        for interface in interfaces:
            hostname = db.get_instance(interface['instance_uuid'])
            instances.append(
                {
                    'uuid': interface['instance_uuid'],
                    'macaddr': interface['macaddr'],
                    'ipv4': interface['ipv4'],
                    'name': hostname['name']
                }
            )
        self.subst['instances'] = instances
        c = t.render(self.subst)

        with open(os.path.join(self.subst['config_dir'], 'dhcpd.conf'), 'w') as f:
            f.write(c)

    def remove_config(self):
        if os.path.exists(self.subst['config_dir']):
            shutil.rmtree(self.subst['config_dir'])

    def remove_dhcpd(self):
        processutils.execute(
            'docker rm -f %(dhcp_interface)s' % self.subst,
            shell=True, check_exit_code=[0, 1])

    def restart_dhcpd(self):
        self.remove_dhcpd()

        processutils.execute(
            'docker run -d --name %(dhcp_interface)s --restart=always '
            '--init --net host -v %(config_dir)s:/data networkboot/dhcpd '
            '%(dhcp_interface)s'
            % self.subst, shell=True)
