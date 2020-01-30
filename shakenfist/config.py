# Copyright 2019 Michael Still

import copy
import logging
import os
import socket


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


node_name = socket.getfqdn()
try:
    node_ip = socket.gethostbyname(node_name)
except:
    # Only for localhost development environments
    node_ip = '127.0.0.1'
    LOG.warning(
        'Could not determine hostname. This is a failure for production deploys.')

CONFIG_DEFAULTS = {
    # Deployment options
    # Where the MySQL database is
    'SQL_URL': 'mysql://root:foo@localhost/sf',

    # What nova called an availability zone
    'ZONE': 'shaken',

    ### NODE SPECIFIC ###
    # The IP of this node
    'NODE_IP': node_ip,
    'NODE_NAME': node_name,
    'NODE_EGRESS_NIC': 'eth0',

    # Where on disk instances are stored
    'STORAGE_PATH': '/srv/shakenfist',
}


class Config(object):
    def __init__(self):
        self.config = copy.copy(CONFIG_DEFAULTS)
        for var in os.environ:
            if var.startswith('SHAKENFIST_'):
                self.config[var.replace('SHAKENFIST_', '')] = os.environ[var]

    def get(self, var):
        return self.config.get(var)


parsed = Config()
LOG.info('Parsed configuration: %s' % repr(parsed))
