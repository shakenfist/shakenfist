# Copyright 2020 Michael Still
from functools import partial
from uuid import uuid4

from shakenfist_utilities import logs

from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import network
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.tasks import DefloatNetworkInterfaceTask
from shakenfist.util import network as util_network


LOG, _ = logs.setup(__name__)


class NetworkInterface(dbo):
    object_type = 'networkinterface'
    initial_version = 2
    current_version = 3

    # docs/developer_guide/state_machine.md has a description of these states.
    state_targets = {
        None: (dbo.STATE_INITIAL, ),
        dbo.STATE_INITIAL: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_CREATED: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_ERROR: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_DELETED: (),
    }

    def __init__(self, static_values):
        self.upgrade(static_values)

        super().__init__(static_values.get('uuid'), static_values.get('version'))

        self.__network_uuid = static_values['network_uuid']
        self.__instance_uuid = static_values['instance_uuid']
        self.__macaddr = static_values['macaddr']
        self.__ipv4 = static_values['ipv4']
        self.__order = static_values['order']
        self.__model = static_values['model']

    @classmethod
    def _upgrade_step_2_to_3(cls, static_values):
        cls._upgrade_metadata_to_attribute(static_values['uuid'])

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
        n.add_networkinterface(ni)

        return ni

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        n = self._external_view()
        n.update({
            'network_uuid': self.network_uuid,
            'instance_uuid': self.instance_uuid,
            'macaddr': self.macaddr,
            'ipv4': self.ipv4,
            'order': self.order,
            'model': self.model
        })

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

        n = network.Network.from_db(self.network_uuid)
        if n:
            n.ipam.release(self.ipv4)
            n.remove_networkinterface(self)

        self.state = dbo.STATE_DELETED

    def hard_delete(self):
        etcd.delete('macaddress', None, self.macaddr)
        super().hard_delete()


class NetworkInterfaces(dbo_iter):
    base_object = NetworkInterface

    def __iter__(self):
        for _, ni in self.get_iterator():
            ni = NetworkInterface(ni)
            if not ni:
                continue

            out = self.apply_filters(ni)
            if out:
                yield out


def instance_filter(inst, ni):
    return inst.uuid == ni.instance_uuid


def network_filter(network, ni):
    return network.uuid == ni.network_uuid


def network_uuid_filter(network_uuid, ni):
    return network_uuid == ni.network_uuid


# Convenience helpers
def interfaces_for_instance(instance):
    nis = {}
    loggable_nis = {}
    for ni in NetworkInterfaces([partial(instance_filter, instance)],
                                prefilter='active'):
        nis[ni.order] = ni
        loggable_nis[ni.order] = str(ni)

    for order in sorted(nis.keys()):
        yield nis[order]
