# Copyright 2020 Michael Still

import jinja2
import logging
import os

from oslo_concurrency import processutils

import config
import util


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


class DHCP(object):
    def __init__(self, network=None):
        self.network = network

    def __str__(self):
        return str(self.network).replace('network', 'dhcp')

    def make_config(self):
        self.config_dir_path = os.path.join(
            config.parsed.get('STORAGE_PATH'), 'dhcp', self.network.uuid)
        if not os.path.exists(self.config_dir_path):
            LOG.debug('%s: Creating dhcp config at %s' %
                      (self, self.config_dir_path))
            os.makedirs(self.config_dir_path)

        with open(os.path.join(config.parsed.get('STORAGE_PATH'), 'dhcp.tmpl')) as f:
            t = jinja2.Template(f.read())

        config = t.render(self.network.subst_dict())

        with open(os.path.join(self.config_dir_path, 'dhcpd.conf'), 'w') as f:
            f.write(config)

    def restart_dhcpd(self):
        subst = self.network.subst_dict()
        subst['config_dir'] = self.config_dir_path

        processutils.execute(
            'docker rm -f %(dhcp_interface)s' % subst, shell=True)

        processutils.execute(
            'docker run -d --name %(dhcp_interface)s --restart=always '
            '--init --net host -v %(config_dir)s:/data networkboot/dhcpd '
            '%(dhcp_interface)s'
            % subst, shell=True)
