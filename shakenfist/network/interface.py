# Copyright 2020 Michael Still
from functools import partial
from typing import Any
from typing import Optional
from uuid import UUID
from uuid import uuid4

from shakenfist_utilities import logs  # noreorder

from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.network import network
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.schema.network_interface_attributes import NetworkInterfaceAttributesData
from shakenfist.schema.network_interface_data import NetworkInterfaceData
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation \
    import PRIORITY
from shakenfist.schema.operations.net_iface_ip_op \
    import create_and_enqueue as nii_create_and_enqueue
from shakenfist.schema.operations.net_iface_ip_op \
    import model_tasks as nii_tasks
from shakenfist.util import network as util_network


LOG, _ = logs.setup(__name__)


class NetworkInterface(dbo):
    object_type = ObjectType.INTERFACE
    initial_version = 2
    current_version = 5

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
    def _upgrade_step_3_to_4(cls, static_values):
        # State migration to MariaDB is now handled by sf-ctl migrate-state-to-mariadb
        ...

    @classmethod
    def _upgrade_step_4_to_5(cls, static_values):
        # Static values and attributes migration to MariaDB is handled by the
        # database daemon data migrations.
        ...

    @classmethod
    def _db_create(cls, object_uuid: str, metadata: dict[str, Any]) -> None:
        """Create a NetworkInterface record in both etcd and MariaDB."""
        # Write to etcd (base class behavior)
        super()._db_create(object_uuid, metadata)

        # Also write static values to MariaDB. Convert string UUIDs to
        # uuid.UUID objects as required by SQLAlchemy's native UUID type.
        data = NetworkInterfaceData(
            uuid=UUID(object_uuid),
            network_uuid=UUID(metadata['network_uuid']),
            instance_uuid=UUID(metadata['instance_uuid']),
            macaddr=metadata['macaddr'],
            ipv4=metadata['ipv4'],
            order=metadata['order'],
            model=metadata['model'],
            version=metadata['version']
        )
        mariadb.create_network_interface(data)

    @classmethod
    def _db_get(cls, object_uuid) -> Optional[dict]:
        """Get NetworkInterface static values, trying MariaDB first."""
        # Try MariaDB first. Ensure we pass a uuid.UUID object as required
        # by SQLAlchemy's native UUID column type.
        if not isinstance(object_uuid, UUID):
            object_uuid = UUID(object_uuid)
        data = mariadb.get_network_interface(object_uuid)
        if data:
            result = {
                'uuid': str(data.uuid),
                'network_uuid': str(data.network_uuid),
                'instance_uuid': str(data.instance_uuid),
                'macaddr': data.macaddr,
                'ipv4': data.ipv4,
                'order': data.order,
                'model': data.model,
                'version': data.version
            }
            if result.get('version', 0) != cls.current_version:
                if not cls.upgrade_supported:
                    raise exceptions.BadObjectVersion(
                        f'Unsupported object version - {cls.object_type}: {result}')
            return result

        # Fall back to etcd for unmigrated objects
        return super()._db_get(object_uuid)

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

        # Also create initial attributes record in MariaDB
        attrs = NetworkInterfaceAttributesData(
            uuid=UUID(interface_uuid),
            floating_address=None
        )
        mariadb.create_network_interface_attributes(attrs)

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
            'network_uuid': str(self.network_uuid),
            'instance_uuid': str(self.instance_uuid),
            'macaddr': self.macaddr,
            'ipv4': self.ipv4,
            'order': self.order,
            'model': self.model
        })

        n['floating'] = self.floating.get('floating_address')
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
        # Try MariaDB first
        attrs = mariadb.get_network_interface_attributes(self.uuid)
        if attrs:
            return {'floating_address': attrs.floating_address}
        # Fall back to etcd for unmigrated objects
        return self._db_get_attribute('floating')

    @floating.setter
    def floating(self, address):
        if address and self.floating.get('floating_address') is not None:
            raise exceptions.NetworkInterfaceAlreadyFloating()
        self._db_set_attribute('floating', {'floating_address': address})

        # Also update MariaDB
        attrs = mariadb.get_network_interface_attributes(self.uuid)
        if attrs:
            updated = NetworkInterfaceAttributesData(
                uuid=attrs.uuid,
                floating_address=address
            )
            mariadb.update_network_interface_attributes(updated)

    def delete(self):
        floating_address = self.floating['floating_address']
        if floating_address:
            op_type, op_uuid = nii_create_and_enqueue(
                self.network_uuid,
                self.uuid,
                floating_address,
                [nii_tasks.interface_defloat],
                priority=PRIORITY.user_facing)
            n = network.Network.from_db(self.network_uuid)
            if n:
                n.set_last_cluster_operation(op_type, op_uuid)

            fn = network.floating_network()
            fn.ipam.release(floating_address)
            self.floating = None

        n = network.Network.from_db(self.network_uuid)
        if n:
            if self.ipv4:
                n.ipam.release(self.ipv4)
            n.remove_networkinterface(self)

        self.state = dbo.STATE_DELETED

    def hard_delete(self):
        etcd.delete('macaddress', None, self.macaddr)
        mariadb.delete_network_interface_attributes(self.uuid)
        mariadb.delete_network_interface(self.uuid)
        super().hard_delete()


class NetworkInterfaces(dbo_iter):
    base_object = NetworkInterface

    def __iter__(self):
        for _, static_values in self.get_iterator():
            ni = NetworkInterface(static_values)
            if not ni:
                continue

            out = self.apply_filters(ni)
            if out:
                yield out


def instance_filter(inst, ni):
    return str(inst.uuid) == str(ni.instance_uuid)


def network_filter(network, ni):
    return str(network.uuid) == str(ni.network_uuid)


def network_uuid_filter(network_uuid, ni):
    return str(network_uuid) == str(ni.network_uuid)


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
