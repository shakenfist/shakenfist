# Copyright 2020 Michael Still
from typing import Any
from typing import Optional
from uuid import UUID
from uuid import uuid4

from shakenfist_utilities import logs  # noreorder

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.constants import EVENT_TYPE_MUTATE
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
    initial_version = 4
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
    def _upgrade_step_4_to_5(cls, static_values):
        # Static values and attributes migration to MariaDB is handled by the
        # database daemon data migrations.
        ...

    @classmethod
    def _db_create(cls, object_uuid: str, metadata: dict[str, Any]) -> None:
        """Create a NetworkInterface record in MariaDB.

        Raises NetworkInterfaceCollision when a UNIQUE constraint refuses
        the row, and lets DatabaseUnavailable through when the database
        could not be asked. The base class's audit event is written only
        once the row exists: new() retries a refused insert, and each
        refusal used to leave a 'db record created' event behind for an
        interface which never was.
        """
        # Also write static values to MariaDB. Ensure UUIDs are uuid.UUID
        # objects as required by SQLAlchemy's native UUID type. Values may
        # already be UUID objects (e.g. from baseobject.uuid property).
        _uuid = object_uuid if isinstance(object_uuid, UUID) else UUID(object_uuid)
        _net_uuid = metadata['network_uuid']
        if not isinstance(_net_uuid, UUID):
            _net_uuid = UUID(_net_uuid)
        _inst_uuid = metadata['instance_uuid']
        if not isinstance(_inst_uuid, UUID):
            _inst_uuid = UUID(_inst_uuid)

        data = NetworkInterfaceData(
            uuid=_uuid,
            network_uuid=_net_uuid,
            instance_uuid=_inst_uuid,
            macaddr=metadata['macaddr'],
            ipv4=metadata['ipv4'],
            order=metadata['order'],
            model=metadata['model'],
            version=metadata['version']
        )
        if not mariadb.create_network_interface(data):
            raise exceptions.NetworkInterfaceCollision(
                f'Network interface {object_uuid} was refused by a UNIQUE '
                f'constraint: MAC {metadata["macaddr"]} or device index '
                f'{metadata["order"]} is already in use')

        super()._db_create(object_uuid, metadata)

    @staticmethod
    def _static_values_to_dict(data):
        """Convert NetworkInterfaceData to the dict format used internally."""
        return {
            'uuid': str(data.uuid),
            'network_uuid': str(data.network_uuid),
            'instance_uuid': str(data.instance_uuid),
            'macaddr': data.macaddr,
            'ipv4': data.ipv4,
            'order': data.order,
            'model': data.model,
            'version': data.version,
        }

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

        # Object not found in MariaDB
        return None

    @classmethod
    def next_order(cls, instance_uuid):
        """The next free device index on an instance.

        Read immediately before the insert which uses it, and read
        again when that insert loses the UNIQUE constraint on
        (instance_uuid, order, active). Reading it once and trusting it
        is what issue 4946 was: two concurrent hot plugs both saw the
        same highest index and both took the next one. The constraint
        is the arbiter; this is only how a caller finds its next guess.

        The guess only converges if every index the constraint is
        holding is visible to this read, so the read must be a superset
        of the constraint's key: every row on the instance, whatever its
        state. It must not be filtered on state. A row takes its slot
        in the constraint the moment it is inserted, but new() gives it
        a state only after the insert loop, so a state-filtered read
        cannot see a sibling's freshly won index and offers the same
        losing guess again until the retry budget is gone -- which is
        what the merge CI run for this fix did, ten guesses in 90ms.

        Soft-deleted rows are counted too. That means an index freed by
        a delete is not handed out again until the row is reaped, so
        repeated plug and unplug cycles leave the indexes sparse. The
        constraint only needs them unique, but they are guest-visible:
        the config drive's network_data.json, rebuilt on every power on,
        names each link eth{order}, so a second NIC can be published as
        eth7. cloud-init matches links on ethernet_mac_address rather
        than that name, which is why sparse is acceptable there.

        An empty list is also what the underlying read returns when it
        fails, and that reads here as index 0. That is safe rather than
        right: on an instance which does have live interfaces the insert
        then loses the constraint and the caller's retry budget runs
        out, so a broken read fails the create rather than duplicating
        an index.
        """
        orders = [
            d.order for d in mariadb.get_network_interfaces_by_instance(
                UUID(str(instance_uuid)))]
        if not orders:
            return 0
        return max(orders) + 1

    @classmethod
    def new(cls, interface_uuid, netdesc, instance_uuid, order=None):
        # If the caller supplied a MAC, honour it exactly. A collision
        # against the UNIQUE constraint on network_interfaces.macaddr
        # must surface as an error rather than be silently replaced with
        # a different address: the caller has no way to observe the swap
        # and downstream config (cloud-init, test assertions, guest
        # udev rules) expects the requested MAC.
        caller_supplied_mac = bool(netdesc.get('macaddress'))
        if not caller_supplied_mac:
            netdesc['macaddress'] = util_network.random_macaddr()

        if not interface_uuid:
            # uuid should only be specified in testing
            interface_uuid = str(uuid4())

        # An order of None means "whatever index is free on this
        # instance", which is what hot plug wants: it cannot safely pick
        # a slot in a separate step from the insert. Instance create
        # passes an explicit index, because there the index is the
        # position the caller listed the network in.
        auto_order = order is None

        # Only auto-generated MACs are retried on collision. A caller-
        # supplied MAC is attempted exactly once and the
        # NetworkInterfaceCollision from the UNIQUE constraint is left to
        # propagate -- unless the order is ours to choose, in which case
        # the insert may have lost on the order rather than on the MAC,
        # and is worth another attempt with the requested MAC untouched.
        # Which constraint refused the row is not asked: over gRPC it
        # could only be read out of the driver's error text, and the
        # cost of not knowing is a few fast failed inserts on a request
        # which was going to be refused anyway.
        #
        # DatabaseUnavailable is not caught. A database which cannot be
        # reached is not a collision, and retrying it here would only
        # spend the budget before reporting the wrong cause.
        max_attempts = 10 if (auto_order or not caller_supplied_mac) else 1
        for attempt in range(max_attempts):
            if auto_order:
                order = cls.next_order(instance_uuid)
            try:
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
                break
            except exceptions.NetworkInterfaceCollision:
                if attempt >= max_attempts - 1:
                    raise
                if not caller_supplied_mac:
                    netdesc['macaddress'] = util_network.random_macaddr()
                LOG.with_fields({
                    'interface_uuid': interface_uuid,
                    'instance_uuid': str(instance_uuid),
                    'order': order
                }).info('Network interface create collided, retrying '
                        'with MAC %s' % netdesc['macaddress'])

        ni = NetworkInterface.from_db(interface_uuid)

        # Create initial attributes record in MariaDB
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
        # The NetworkInterface row itself is the source of truth for
        # the association; the network-side list is queried live.
        n.add_event(EVENT_TYPE_MUTATE, 'add networkinterface',
                    extra={'networkinterface': str(ni.uuid)})

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
        attrs = mariadb.get_network_interface_attributes(self.uuid)
        if not attrs:
            _uuid = self.uuid if isinstance(self.uuid, UUID) else UUID(str(self.uuid))
            attrs = NetworkInterfaceAttributesData(uuid=_uuid, floating_address=None)
            if not mariadb.create_network_interface_attributes(attrs):
                # Another thread created the record; re-read it
                attrs = mariadb.get_network_interface_attributes(self.uuid)
        return {'floating_address': attrs.floating_address}

    @floating.setter
    def floating(self, address):
        if address and self.floating.get('floating_address') is not None:
            raise exceptions.NetworkInterfaceAlreadyFloating()

        _uuid = self.uuid if isinstance(self.uuid, UUID) else UUID(str(self.uuid))
        attrs = mariadb.get_network_interface_attributes(self.uuid)
        if not attrs:
            attrs = NetworkInterfaceAttributesData(uuid=_uuid, floating_address=address)
            if not mariadb.create_network_interface_attributes(attrs):
                # Another thread created the record; update instead
                updated = NetworkInterfaceAttributesData(
                    uuid=_uuid, floating_address=address)
                mariadb.update_network_interface_attributes(updated)
        else:
            updated = NetworkInterfaceAttributesData(
                uuid=attrs.uuid, floating_address=address)
            mariadb.update_network_interface_attributes(updated)

        self.add_event(EVENT_TYPE_MUTATE, 'set attribute',
                       extra={'attribute': 'floating',
                              'floating_address': address})

    def delete(self):
        floating_address = self.floating['floating_address']
        if floating_address:
            nii_create_and_enqueue(
                self.network_uuid,
                self.uuid,
                floating_address,
                [nii_tasks.interface_defloat],
                priority=PRIORITY.user_facing)

            fn = network.floating_network()
            fn.ipam.release(floating_address)
            self.floating = None

        n = network.Network.from_db(self.network_uuid)
        if n:
            if self.ipv4:
                n.ipam.release(self.ipv4)
            n.remove_networkinterface_lease(self)
            n.add_event(EVENT_TYPE_MUTATE, 'remove networkinterface',
                        extra={'networkinterface': str(self.uuid)})

        self.state = dbo.STATE_DELETED


class NetworkInterfaces(dbo_iter):
    base_object = NetworkInterface

    def _resolve_prefilter_to_states(self):
        # Preserve what this iterator did before it moved onto the SQL
        # pushdown base class: when no prefilter is set, do not filter on
        # state (return every network interface and let predicate filters
        # scope). The base class default of ACTIVE_STATES is kept for other
        # inheritors.
        if self.prefilter is None:
            return set()
        return super()._resolve_prefilter_to_states()

    def _find(self, criteria):
        return mariadb.find_network_interfaces(criteria)

    def _to_static_values(self, data):
        return NetworkInterface._static_values_to_dict(data)

    def __iter__(self):
        for _, static_values in self.get_iterator():
            ni = NetworkInterface(static_values)
            if not ni:
                continue
            filtered = self.apply_filters(ni)
            if filtered:
                yield filtered


def network_filter(network, ni):
    return str(network.uuid) == str(ni.network_uuid)


def network_uuid_filter(network_uuid, ni):
    return str(network_uuid) == str(ni.network_uuid)
