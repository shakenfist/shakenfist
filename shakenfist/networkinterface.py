# Copyright 2020 Michael Still

from functools import partial
from uuid import uuid4

from shakenfist import baseobject
from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist import db
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist.ipmanager import IPManager
from shakenfist import logutil
from shakenfist import network
from shakenfist.tasks import DefloatNetworkInterfaceTask
from shakenfist.util import network as util_network


LOG, _ = logutil.setup(__name__)


class NetworkInterface(dbo):
    object_type = 'networkinterface'
    current_version = 2
    state_targets = {
        None: (dbo.STATE_INITIAL, ),
        dbo.STATE_INITIAL: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_CREATED: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_ERROR: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_DELETED: (),
    }

    def __init__(self, static_values):
        super(NetworkInterface, self).__init__(static_values.get('uuid'),
                                               static_values.get('version'))

        self.__network_uuid = static_values['network_uuid']
        self.__instance_uuid = static_values['instance_uuid']
        self.__macaddr = static_values['macaddr']
        self.__ipv4 = static_values['ipv4']
        self.__order = static_values['order']
        self.__model = static_values['model']

    @classmethod
    def new(cls, interface_uuid, netdesc, instance_uuid, order):
        if 'macaddress' not in netdesc or not netdesc['macaddress']:
            possible_mac = util_network.random_macaddr()
            mac_iface = {'interface_uuid': interface_uuid}
            while not etcd.create('macaddress', None, possible_mac, mac_iface):
                possible_mac = util_network.random_macaddr()
            netdesc['macaddress'] = possible_mac

        if not interface_uuid:
            # uuid should only be specified in testing
            interface_uuid = str(uuid4())

        NetworkInterface._db_create(
            interface_uuid,
            {
                'network_uuid': netdesc['network_uuid'],
                'instance_uuid': instance_uuid,
                'macaddr': netdesc['macaddress'],
                'ipv4': netdesc['address'],
                'order': order,
                'model': netdesc['model'],

                'version': cls.current_version
            }
        )

        ni = NetworkInterface.from_db(interface_uuid)
        ni._db_set_attribute('floating', {'floating_address': None})
        ni.state = NetworkInterface.STATE_INITIAL

        n = network.Network.from_db(netdesc['network_uuid'])
        if not n:
            raise exceptions.NetworkMissing(
                'No such network: %s' % netdesc['network_uuid'])
        n.add_networkinterface(interface_uuid)

        # TODO(andy): Integrate metadata into each object type
        # Initialise metadata
        db.persist_metadata('networkinterface', interface_uuid, {})

        return ni

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        n = {
            'uuid': self.uuid,
            'network_uuid': self.network_uuid,
            'instance_uuid': self.instance_uuid,
            'macaddr': self.macaddr,
            'ipv4': self.ipv4,
            'order': self.order,
            'model': self.model,
            'state': self.state.value,
            'version': self.version
        }

        n['floating'] = self._db_get_attribute(
            'floating').get('floating_address')
        return n

    # Static values
    @property
    def network_uuid(self):
        return self.__network_uuid

    @property
    def instance_uuid(self):
        return self.__instance_uuid

    @property
    def macaddr(self):
        return self.__macaddr

    @property
    def ipv4(self):
        return self.__ipv4

    @property
    def order(self):
        return self.__order

    @property
    def model(self):
        return self.__model

    # Values routed to attributes, writes are via helper methods.
    @property
    def floating(self):
        return self._db_get_attribute('floating')

    @floating.setter
    def floating(self, address):
        if address and self.floating.get('floating_address') is not None:
            raise exceptions.NetworkInterfaceAlreadyFloating()
        self._db_set_attribute('floating', {'floating_address': address})

    def delete(self):
        if self.floating['floating_address']:
            etcd.enqueue(
                'networknode',
                DefloatNetworkInterfaceTask(self.network_uuid, self.uuid))

        with db.get_lock('ipmanager', None, self.network_uuid,
                         ttl=120, op='Release fixed IP'):
            ipm = IPManager.from_db(self.network_uuid)
            ipm.release(self.ipv4)
            ipm.persist()

        n = network.Network.from_db(self.network_uuid)
        if not n:
            raise exceptions.NetworkMissing(
                'No such network: %s' % self.network_uuid)
        n.remove_networkinterface(self.uuid)

        self.state = dbo.STATE_DELETED

    def hard_delete(self):
        etcd.delete('macaddress', None, self.macaddr)
        super(NetworkInterface, self).hard_delete()


class NetworkInterfaces(dbo_iter):
    def __iter__(self):
        for _, ni in etcd.get_all('networkinterface', None):
            out = self.apply_filters(NetworkInterface(ni))
            if out:
                yield out


def instance_filter(inst, ni):
    return inst.uuid == ni.instance_uuid


def network_filter(network, ni):
    return network.uuid == ni.network_uuid


# Convenience helpers
def interfaces_for_instance(instance):
    nis = {}
    loggable_nis = {}
    for ni in NetworkInterfaces([baseobject.active_states_filter,
                                 partial(instance_filter, instance)]):
        nis[ni.order] = ni
        loggable_nis[ni.order] = str(ni)

    for order in sorted(nis.keys()):
        yield nis[order]
