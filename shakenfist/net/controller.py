# A state machine which controls the network meshes.

from shakenfist.db import impl as default_db_impl
from shakenfist.net import impl as default_net_impl


class NetworkStateMachine(object):
    def __init__(netimpl=None, dbimpl=None):
        if netimpl:
            self.net_impl = netimpl
        else:
            self.net_impl = default_net_impl

        if dbimpl:
            self.db_impl = dbimpl
        else:
            self.db_impl = default_db_impl

    def validate(self):
        exists = self.net_impl.find_existing_networks()
        desired = self.db_impl.find_desired_networks()
        touched = []

        for n in exists:
            if not n in desired:
                n.delete()
                touched.append(n)

        for n in desired:
            if not n in exists:
                n.create()
                touched.append(n)

        for n in self.net_impl.find_existing_networks():
            if not n in touched:
                n.ensure_mesh()
                touched.append(n)