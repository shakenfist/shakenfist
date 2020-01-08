# Copyright 2019 Michael Still

import copy
import os
import socket


CONFIG_DEFAULTS = {
    # Where on disk to store data
    'STORAGE_PATH': '/srv/shakenfist',

    # What nova called an availability zone
    'ZONE': 'shaken',

    ### NODE SPECIFIC ###
    # The IP of this node
    'NODE_IP': socket.gethostbyname(socket.getfqdn())
}


class Config(object):
    def __init__(self):
        self.config = copy.copy(CONFIG_DEFAULTS)
        for var in os.environ:
            if var.startswith('SHAKEN_'):
                self.config[var.replace('SHAKEN_', '')] = os.environ[var]

    def get(self, var):
        return self.config.get(var)


parsed = Config()
