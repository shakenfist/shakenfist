# Copyright 2020 Michael Still
import random
from typing import List, Optional
from uuid import UUID
from uuid import uuid4

from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import FLOATING_NETWORK_UUID
from shakenfist import ipam
from shakenfist import mariadb
from shakenfist.network import interface
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.network_attributes import NetworkAttributesData
from shakenfist.schema.network_data import NetworkData
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectWithOperations as dbowo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.constants import get_object_class
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.net_op \
    import create_and_enqueue as net_create_and_enqueue
from shakenfist.schema.operations.net_op \
    import model_tasks as net_tasks
from shakenfist.schema.operations import net_ip_op as net_ip_op_schema
from shakenfist.schema.operations.net_macaddr_ip_op \
    import create_and_enqueue as nmi_create_and_enqueue
from shakenfist.schema.operations.net_macaddr_ip_op \
    import model_tasks as nmi_tasks
from shakenfist.schema.operations.node_net_op \
    import create_and_enqueue as nn_create_and_enqueue
from shakenfist.schema.operations.node_net_op \
    import model_tasks as nn_tasks
from shakenfist.eventlog import add_event_multi
from shakenfist import exceptions
from shakenfist.exceptions import CannotAssignFloatingGateway
from shakenfist.managed_executables import dnsmasq
from shakenfist.node import Node
from shakenfist.schema.object_filter import ObjectFilterCriteria
from shakenfist.schema.object_types import ObjectType
from shakenfist.util import general as util_general
from shakenfist.util import network as util_network


LOG, _ = logs.setup(__name__)


class Network(dbowo):
    object_type = ObjectType.NETWORK
    initial_version = 8
    current_version = 9

    # docs/developer_guide/state_machine.md has a description of these states.
    state_targets = {
        None: (dbo.STATE_INITIAL, ),
        dbo.STATE_INITIAL: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_CREATED: (dbo.STATE_DELETED, dbo.STATE_DELETE_WAIT, dbo.STATE_ERROR),
        dbo.STATE_DELETE_WAIT: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_ERROR: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_DELETED: (),
    }

    def __init__(self, static_values):
        self.upgrade(static_values)

        super().__init__(static_values.get('uuid'), static_values.get('version'))

        self.__ipam = ipam.IPAM.from_db(
            static_values['uuid'], suppress_failure_audit=True)
        if not self.__ipam:
            in_memory_only = self.state.value == dbo.STATE_DELETED
            self.__ipam = ipam.IPAM.new(
                static_values['uuid'], static_values['namespace'],
                static_values['uuid'], static_values.get('netblock'),
                in_memory_only=in_memory_only)

        self.__name = static_values.get('name')
        self.__namespace = static_values.get('namespace')
        self.__netblock = static_values.get('netblock')
        self.__provide_dhcp = static_values.get('provide_dhcp')
        self.__provide_nat = static_values.get('provide_nat')
        self.__provide_dns = static_values.get('provide_dns', False)
        self.__vxid = static_values.get('vxid')

        self.egress_nic = static_values.get('egress_nic') or config.NODE_EGRESS_NIC
        self.mesh_nic = static_values.get('mesh_nic') or config.NODE_MESH_NIC

        self.__ipblock = self.ipam.network_address
        self.__router = self.ipam.get_address_at_index(1)
        self.__dhcp_start = self.ipam.get_address_at_index(2)
        self.__netmask = self.ipam.netmask
        self.__broadcast = self.ipam.broadcast_address
        self.__network_address = self.ipam.network_address

        # Lazy-load attributes from MariaDB
        self.__attributes: Optional[NetworkAttributesData] = None
        self.__attributes_loaded: bool = False

    @classmethod
    def _upgrade_step_8_to_9(cls, static_values):
        ...

    @classmethod
    def _db_create(cls, object_uuid, metadata):
        """Create a Network record in both etcd and MariaDB."""
        # Write to etcd (base class behavior)
        super()._db_create(object_uuid, metadata)

        # Also write static values to MariaDB
        _uuid = object_uuid if isinstance(object_uuid, UUID) else UUID(object_uuid)
        data = NetworkData(
            uuid=_uuid,
            name=metadata.get('name', ''),
            namespace=metadata.get('namespace'),
            netblock=metadata.get('netblock', ''),
            provide_dhcp=metadata.get('provide_dhcp', False),
            provide_nat=metadata.get('provide_nat', False),
            provide_dns=metadata.get('provide_dns', False),
            vxid=metadata.get('vxid', 0),
            egress_nic=metadata.get('egress_nic'),
            mesh_nic=metadata.get('mesh_nic'),
            version=metadata.get('version', cls.current_version)
        )
        if not mariadb.create_network(data):
            raise RuntimeError(f'Failed to create network {object_uuid} in MariaDB')

        # Create initial attributes record in MariaDB
        attrs = NetworkAttributesData(uuid=_uuid)
        if not mariadb.create_network_attributes(attrs):
            raise RuntimeError(f'Failed to create network attributes {object_uuid} in MariaDB')

    @staticmethod
    def _static_values_to_dict(data):
        """Convert NetworkData to the dict format used internally."""
        return {
            'uuid': str(data.uuid),
            'name': data.name,
            'namespace': data.namespace,
            'netblock': data.netblock,
            'provide_dhcp': data.provide_dhcp,
            'provide_nat': data.provide_nat,
            'provide_dns': data.provide_dns,
            'vxid': data.vxid,
            'egress_nic': data.egress_nic,
            'mesh_nic': data.mesh_nic,
            'version': data.version,
        }

    @classmethod
    def _db_get(cls, object_uuid) -> Optional[dict]:
        """Get Network static values from MariaDB."""
        if not isinstance(object_uuid, UUID):
            object_uuid = UUID(str(object_uuid))
        data = mariadb.get_network(object_uuid)
        if not data:
            return None

        result = cls._static_values_to_dict(data)
        if result.get('version', 0) != cls.current_version:
            from shakenfist import exceptions
            if not cls.upgrade_supported:
                raise exceptions.BadObjectVersion(
                    f'Unsupported object version - '
                    f'{cls.object_type}: {result}')
        return result

    @classmethod
    def filter(cls, filters):
        """Override base class to use MariaDB instead of etcd.

        Documented fallback: ``Network.from_db_by_ref`` is the
        live name-lookup path and pushes its predicates to SQL
        via ``find_networks``. ``filter()`` exists so the
        predicate API on ``DatabaseBackedObject.from_db_by_ref``
        keeps a usable implementation, even though no in-tree
        caller currently reaches it.
        """
        for data in mariadb.get_all_networks():  # nopushdown: fallback (see docstring)
            obj = cls(cls._static_values_to_dict(data))
            if all(f(obj) for f in filters):
                yield obj

    @classmethod
    def from_db_by_ref(cls, object_ref, namespace=None):
        """Look up a network by UUID or by name within a namespace.

        UUID lookups short-circuit to from_db. Name lookups push
        state + namespace + name down to a single indexed SQL
        query via mariadb.find_networks.

        The floating network (FLOATING_NETWORK_UUID) has namespace=None
        in the database. A tenant-scoped query (namespace != None) won't
        match it via SQL NULL semantics, so no explicit skip is required.
        """
        if object_ref and util_general.valid_uuid4(object_ref):
            return cls.from_db(object_ref)

        # namespace='system' or namespace=None means 'look across
        # all namespaces' - preserve that by omitting the namespace
        # filter. Matches baseobject.namespace_filter semantics.
        criteria_namespace = (
            namespace if namespace and namespace != 'system' else None)

        criteria = ObjectFilterCriteria(
            states=list(cls.ACTIVE_STATES),
            namespace=criteria_namespace,
            name=object_ref,
        )
        matches = mariadb.find_networks(criteria)

        if not matches:
            return None
        if len(matches) > 1:
            raise exceptions.MultipleObjects(
                f'multiple networks have the name "{object_ref}"'
                f' in namespace "{namespace}"')
        return cls(cls._static_values_to_dict(matches[0]))

    def _load_attributes(self) -> Optional[NetworkAttributesData]:
        """Load attributes from MariaDB."""
        if not self.__attributes_loaded:
            self.__attributes = mariadb.get_network_attributes(
                UUID(str(self.uuid)))
            self.__attributes_loaded = True
        return self.__attributes

    def _ensure_attributes(self) -> NetworkAttributesData:
        """Ensure attributes record exists, creating defaults
        if needed."""
        attrs = self._load_attributes()
        if attrs is None:
            attrs = NetworkAttributesData(
                uuid=UUID(str(self.uuid)))
            if not mariadb.create_network_attributes(attrs):
                # Another thread/process created the record first;
                # reload the actual data from MariaDB.
                attrs = mariadb.get_network_attributes(
                    UUID(str(self.uuid)))
            self.__attributes = attrs
        return attrs

    def _save_attributes(self, fields: Optional[List[str]] = None) -> None:
        """Persist current attributes to MariaDB.

        fields names the attributes to write; None or empty writes
        every column. Callers changing one attribute must name it:
        this object caches its attributes in memory, so an unmasked
        write pushes an arbitrarily stale snapshot of the other
        columns over any concurrent writer's committed changes (the
        cross-attribute lost update fixed for instance attributes).
        """
        if self.__attributes is not None:
            mariadb.update_network_attributes(
                self.__attributes, fields=fields)

    @staticmethod
    def allocate_vxid(net_id):
        # VXLAN ID uniqueness is enforced by the UNIQUE constraint on
        # the networks.vxid column. We just generate a random ID here;
        # the actual uniqueness check happens at insert time in
        # _db_create. If there's a collision, Network.new() retries.
        return random.randint(1, 16777215)

    @classmethod
    def new(cls, name, namespace, netblock, provide_dhcp=False,
            provide_nat=False, network_uuid=None, vxid=None,
            provide_dns=False):

        if not network_uuid:
            # uuid should only be specified in testing
            network_uuid = str(uuid4())

        if not vxid:
            vxid = Network.allocate_vxid(network_uuid)

        # Pre-create the IPAM
        ipam.IPAM.new(network_uuid, namespace, network_uuid, netblock)

        # Retry _db_create if the vxid collides with an existing network
        # (the UNIQUE constraint on networks.vxid causes an IntegrityError
        # which _db_create surfaces as a RuntimeError).
        max_vxid_attempts = 10
        for attempt in range(max_vxid_attempts):
            try:
                Network._db_create(
                    network_uuid,
                    {
                        'vxid': vxid,
                        'name': name,
                        'namespace': namespace,
                        'netblock': netblock,
                        'provide_dhcp': provide_dhcp,
                        'provide_nat': provide_nat,
                        'provide_dns': provide_dns,
                        'version': cls.current_version
                    }
                )
                break
            except RuntimeError:
                if attempt >= max_vxid_attempts - 1:
                    raise
                vxid = Network.allocate_vxid(network_uuid)

        n = Network.from_db(network_uuid)
        n.state = Network.STATE_INITIAL

        # Networks should immediately appear on the network node. Phase 6
        # of `PLAN-network-facade.md` retired the `network_deploy` composite
        # task; the explicit task list preserves the original semantic
        # (create on the network node, then ensure the mesh FDB) without
        # going through the obsolete handler.
        net_create_and_enqueue(
            n.uuid,
            [net_tasks.network_apply_create_network_node,
             net_tasks.network_ensure_mesh],
            PRIORITY.user_waiting,
            runs_after=[n.last_cluster_operation],
            request_id=util_general.get_request_id())

        return n

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        n = self._external_view()
        n.update({
            'name': self.__name,
            'namespace': self.__namespace,
            'netblock': self.__netblock,
            'provide_dhcp': self.__provide_dhcp,
            'provide_nat': self.__provide_nat,
            'provide_dns': self.__provide_dns,
            'floating_gateway': self.floating_gateway,
            'vxlan_id': self.__vxid,
            'error_message': self.error,
            'last_cluster_operation': self.last_cluster_operation
        })

        return n

    # Static values
    @property
    def ipam(self):
        return self.__ipam

    @property
    def floating_gateway(self):
        attrs = self._ensure_attributes()
        return attrs.floating_gateway

    @property
    def name(self):
        return self.__name

    @property
    def namespace(self):
        return self.__namespace

    @property
    def netblock(self):
        return self.__netblock

    @property
    def provide_dhcp(self):
        return self.__provide_dhcp

    @property
    def provide_nat(self):
        return self.__provide_nat

    @property
    def provide_dns(self):
        return self.__provide_dns

    @property
    def vxid(self):
        return self.__vxid

    # Calculated values
    @property
    def ipblock(self):
        return self.__ipblock

    @property
    def router(self):
        return self.__router

    @property
    def dhcp_start(self):
        return self.__dhcp_start

    @property
    def netmask(self):
        return self.__netmask

    @property
    def broadcast(self):
        return self.__broadcast

    @property
    def network_address(self):
        return self.__network_address

    @property
    def networkinterfaces(self):
        """Currently-attached NetworkInterface objects.

        Queried live from the network_interfaces table
        (network_uuid is an indexed column). Previously cached
        as a list of UUID strings on network_attributes; that
        column is dropped in phase 7e.
        """
        criteria = ObjectFilterCriteria(
            states=list(interface.NetworkInterface.ACTIVE_STATES),
            network_uuid=str(self.uuid),
        )
        return [
            interface.NetworkInterface(
                interface.NetworkInterface._static_values_to_dict(d))
            for d in mariadb.find_network_interfaces(criteria)
        ]

    def remove_networkinterface_lease(self, ni):
        """Release a DHCP lease held by a departing NetworkInterface.

        The row-level association is managed by the
        NetworkInterface lifecycle; all that remains for the
        owning Network is the DHCP-lease housekeeping that used
        to live alongside the list mutation.

        Callers of this method live outside the net-worker
        dispatcher: ``NetworkInterface.delete`` is invoked from
        ``node_inst_op._instance_delete`` (the node-queue worker,
        a different queue from the networknode queue that
        ``remove_dhcp_lease`` enqueues onto), and from the
        ``stray_nics`` reaper and cluster-maintainer long-running
        threads. None of those contexts share a queue with the
        enqueued ``net_macaddr_ip_op``, so blocking on the op via
        ``raise_for_error()`` is safe (no self-enqueue deadlock)
        and preserves the previous synchronous-with-exception
        semantics for genuine failures.

        An ``OperationTimeout`` is deliberately not propagated: it
        means the op is still queued, not that it failed. Under load
        the networknode queue can back up past ``API_ASYNC_WAIT``
        (parallel CI teardown has been observed queueing ops for 90+
        seconds) and the op still executes when dequeued. The lease
        removal is idempotent housekeeping, so failing the caller --
        usually an instance delete -- over queue latency wedges the
        instance for no benefit.
        """
        if ni.ipv4:
            op = self.remove_dhcp_lease(ni.ipv4, ni.macaddr)
            if op is not None:
                try:
                    op.raise_for_error()
                except exceptions.OperationTimeout:
                    self.add_event(
                        EVENT_TYPE_AUDIT,
                        'timed out waiting for dhcp lease removal, '
                        'the op remains queued and will still execute',
                        extra={'op_uuid': str(op.uuid),
                               'networkinterface': str(ni.uuid)})

    def _update_floating_gateway(self, gateway):
        attrs = self._ensure_attributes()
        if attrs.floating_gateway == gateway:
            return True
        if attrs.floating_gateway and gateway is not None:
            return False
        attrs.floating_gateway = gateway
        self._save_attributes(fields=['floating_gateway'])
        self.add_event(EVENT_TYPE_MUTATE, 'update floating gateway',
                       extra={'floating_gateway': gateway})
        return True

    def assign_floating_gateway(self):
        fn = floating_network()
        floating_gateway = fn.ipam.reserve_random_free_address(
            self.unique_label(), ReservationType.GATEWAY, '')
        if self._update_floating_gateway(floating_gateway):
            return
        fn.ipam.release(floating_gateway)

        if not self.floating_gateway:
            raise CannotAssignFloatingGateway()

    def unassign_floating_gateway(self):
        floating_gateway = self.floating_gateway
        if not floating_gateway:
            return
        fn = floating_network()
        fn.ipam.release(floating_gateway)
        self._update_floating_gateway(None)

    @property
    def _vx_veth_inner(self):
        return 'veth-%06x-i' % self.vxid

    def subst_dict(self):
        # NOTE(mikal): it should be noted that the maximum interface name length
        # on Linux is 15 user visible characters, we therefore use hex for vxids
        # where they appear in an interface name. Note that vxid does not appear
        # in an interface name and is therefore in decimal (as required by) the
        # "ip" command.
        retval = {
            'vx_id': self.vxid,
            'vx_interface': 'vxlan-%06x' % self.vxid,
            'vx_bridge': 'br-vxlan-%06x' % self.vxid,
            'vx_veth_outer': 'veth-%06x-o' % self.vxid,
            'vx_veth_inner': self._vx_veth_inner,

            'egress_bridge': 'egr-br-%s' % config.NODE_EGRESS_NIC,
            'egress_veth_outer': 'egr-%06x-o' % self.vxid,
            'egress_veth_inner': 'egr-%06x-i' % self.vxid,

            'netns': str(self.uuid),

            'ipblock': self.ipblock,
            'netmask': self.netmask,
            'router': self.router,
            'broadcast': self.broadcast,

            'dhcp_start': self.dhcp_start,
            'provide_nat': self.provide_nat,
        }

        # Hosted DNS entries
        if self.provide_dns:
            attrs = self._ensure_attributes()
            retval['hosted_dns'] = attrs.hosteddns
        else:
            retval['hosted_dns'] = {}

        return retval

    def is_okay(self):
        """Check if network is created and running."""
        if self.has_pending_cluster_operation():
            # An operation is in flight against this network. Defer
            # the maintainer's recreate path so it does not race with
            # the queue worker.
            return True

        if not self.is_created():
            self.add_event(EVENT_TYPE_STATUS, 'network not ok, is not created')
            return False

        if not config.NODE_IS_NETWORK_NODE:
            return True

        if self.provide_dhcp or self.provide_dns:
            if not self.is_dnsmasq_running():
                self.add_event(
                    EVENT_TYPE_STATUS, 'network not ok, dnsmasq not running')
                return False

        return True

    def is_created(self):
        """Attempt to ensure network has been created successfully."""

        # The floating network always exists, and would fail the vx_bridge
        # test we apply to other networks.
        if self.uuid == FLOATING_NETWORK_UUID:
            return True

        subst = self.subst_dict()
        if not util_network.check_for_interface(subst['vx_bridge'], up=True):
            self.add_event(
                EVENT_TYPE_STATUS, f'{subst["vx_bridge"]} is not up')
            return False

        return True

    def _mesh_participant_node_identifiers(self):
        """Return the deduplicated placement node identifiers for every
        instance with an interface on this network."""
        # Late-import ``instance`` to avoid the network <-> instance
        # circular import at module load.
        from shakenfist import instance  # noqa: PLC0415

        seen_instances: set[str] = set()
        identifiers: list[str] = []
        for ni in self.networkinterfaces:
            if ni.instance_uuid in seen_instances:
                continue
            seen_instances.add(ni.instance_uuid)
            inst = instance.Instance.from_db(ni.instance_uuid)
            if not inst:
                continue
            placement = inst.placement
            if not placement or not placement.get('node'):
                continue
            if placement['node'] not in identifiers:
                identifiers.append(placement['node'])
        return identifiers

    def mesh_desired_node_ips(self):
        """Return the mesh IPs this node's VXLAN FDB should flood to.

        The set contains the network node plus every node hosting an
        instance with an interface on this network, always excluding
        this node itself -- flooding back to ourselves would reflect
        duplicate packets (see bug #859). This is the shared source of
        truth for the mesh: ``BridgedVXLanNetwork._apply_ensure_mesh``
        writes it to the FDB and ``is_mesh_okay`` audits the FDB
        against it.

        NOTE(mikal): why not use DNS here? Well, DNS might be outside
        the control of the deployer if we're running in a public cloud
        as an overlay cloud...
        """
        node_ips = set()
        if config.NETWORK_NODE_IP != config.NODE_MESH_IP:
            # Always add the network node if it is not this node
            node_ips.add(config.NETWORK_NODE_IP)

        for identifier in self._mesh_participant_node_identifiers():
            n = Node.from_db(identifier)
            if n and n.ip != config.NODE_MESH_IP:
                node_ips.add(n.ip)

        return node_ips

    def is_mesh_okay(self):
        """Audit this node's VXLAN flood mesh for this network.

        Compares the flood (all-zeroes) FDB entries on the vxlan
        interface against ``mesh_desired_node_ips``. This catches the
        drift where the mesh was never (or only partially) written on a
        node -- most notably the network node, which forwards all
        floating traffic but can only reach an idle guest via a flood
        entry once learned FDB entries have aged out.
        """
        # The floating network has no vxlan mesh of its own.
        if self.uuid == FLOATING_NETWORK_UUID:
            return True

        subst = self.subst_dict()
        discovered = util_network.discover_mesh_flood_ips(
            subst['vx_interface'])
        if discovered is None:
            # There is no vxlan interface on this node. That is
            # ``is_created``'s drift to detect; the mesh audit is moot.
            return True

        desired = self.mesh_desired_node_ips()
        if discovered != desired:
            self.add_event(
                EVENT_TYPE_STATUS, 'network not ok, vxlan mesh has drifted',
                extra={
                    'desired': sorted(desired),
                    'discovered': sorted(discovered)
                })
            return False

        return True

    def is_dead(self):
        """Check if the network is deleted or being deleted, or in error.

        First, update the object model to the ensure latest configuration. Some
        callers will wait on a lock before calling this function. In this case
        we definitely need to update the in-memory object model.
        """
        return self.state.value in (self.STATE_DELETED,
                                    self.STATE_DELETE_WAIT,
                                    self.STATE_ERROR)

    def _not_on_floating_network(func):
        # Some calls don't make sense on the floating network and are ignored
        def wrapper(*args, **kwargs):
            # The first argument is "self"
            if args[0].uuid == FLOATING_NETWORK_UUID:
                return
            return func(*args, **kwargs)
        return wrapper

    @_not_on_floating_network
    def create_on_hypervisor(self):
        """Enqueue a network_apply_create_hypervisor node_net_op for this network.

        Returns the enqueued NodeNetOp loaded from the database. Callers
        wanting the previous synchronous-with-exception semantics call
        ``op.raise_for_error()``. The host-mutating work has moved to
        ``BridgedVXLanNetwork._apply_create_on_hypervisor`` and now runs in
        the net-worker dispatcher on this node.
        """
        op_type, op_uuid = nn_create_and_enqueue(
            str(config.NODE_UUID),
            self.uuid,
            [nn_tasks.network_apply_create_hypervisor],
            PRIORITY.user_facing,
        )
        return get_object_class(op_type).from_db(op_uuid)

    @_not_on_floating_network
    def create_on_network_node(self):
        """Enqueue a network_apply_create_network_node NetOp for this network.

        Returns the enqueued NetOp loaded from the database. Callers
        wanting the previous synchronous-with-exception semantics call
        ``op.raise_for_error()``. The host-mutating work has moved to
        ``BridgedVXLanNetwork._apply_create_on_network_node`` and now runs
        in the net-worker dispatcher on the elected network node.
        """
        if self.state.value == dbo.STATE_DELETED:
            self.add_event(
                EVENT_TYPE_AUDIT,
                'refusing to create deleted network on network node')
            return None
        op_type, op_uuid = net_create_and_enqueue(
            network_uuid=str(self.uuid),
            tasks=[net_tasks.network_apply_create_network_node],
            priority=PRIORITY.user_facing,
        )
        return get_object_class(op_type).from_db(op_uuid)

    def delete_on_hypervisor(self):
        """Enqueue a network_destroy node_net_op for this network on this node.

        Returns the enqueued NodeNetOp loaded from the database. Callers
        wanting the previous synchronous-with-exception semantics call
        ``op.raise_for_error()``. The host-mutating work has moved to
        ``BridgedVXLanNetwork._apply_delete_on_hypervisor`` and now runs in
        the net-worker dispatcher on this node.
        """
        op_type, op_uuid = nn_create_and_enqueue(
            str(config.NODE_UUID),
            self.uuid,
            [nn_tasks.network_destroy],
            PRIORITY.user_facing,
        )
        return get_object_class(op_type).from_db(op_uuid)

    def delete_on_network_node(self):
        """Enqueue a network_apply_delete_network_node NetOp for this network.

        Returns the enqueued NetOp loaded from the database. Callers
        wanting the previous synchronous-with-exception semantics call
        ``op.raise_for_error()``. The host-mutating work has moved to
        ``BridgedVXLanNetwork._apply_delete_on_network_node`` and now runs
        in the net-worker dispatcher on the elected network node.
        """
        op_type, op_uuid = net_create_and_enqueue(
            network_uuid=str(self.uuid),
            tasks=[net_tasks.network_apply_delete_network_node],
            priority=PRIORITY.user_facing,
        )
        return get_object_class(op_type).from_db(op_uuid)

    def hard_delete(self):
        mariadb.delete_network_attributes(UUID(str(self.uuid)))
        mariadb.delete_network(UUID(str(self.uuid)))
        super().hard_delete()

    def _get_dnsmasq_object(self):
        return dnsmasq.DnsMasq.new(self, provide_dhcp=self.provide_dhcp,
                                   provide_nat=self.provide_nat,
                                   provide_dns=self.provide_dns)

    def is_dnsmasq_running(self):
        """Determine if dnsmasq process is running for this network"""
        d = self._get_dnsmasq_object()
        is_running = d.is_running()
        if not is_running:
            self.add_event(EVENT_TYPE_STATUS, 'dnsmasq is not running')
        return is_running

    def remove_dhcp_lease(self, ipv4, macaddr):
        """Enqueue a remove_dhcp_lease NetMacaddrIPOp for this network.

        Returns the enqueued NetMacaddrIPOp loaded from the database, or
        ``None`` if this network does not run dnsmasq. Callers wanting
        the previous synchronous-with-exception semantics call
        ``op.raise_for_error()``. The host-mutating work has moved to
        ``BridgedVXLanNetwork._apply_remove_dhcp_lease``.
        """
        if not self.provide_dhcp and not self.provide_dns:
            return None

        op_type, op_uuid = nmi_create_and_enqueue(
            self.uuid, macaddr, ipv4, [nmi_tasks.remove_dhcp_lease],
            PRIORITY.user_facing,
        )
        return get_object_class(op_type).from_db(op_uuid)

    def update_dnsmasq(self):
        """Enqueue a network_apply_update_dnsmasq NetOp for this network.

        Returns the enqueued NetOp loaded from the database, or ``None``
        if this network does not run dnsmasq. Callers wanting the
        previous synchronous-with-exception semantics call
        ``op.raise_for_error()``. The host-mutating work has moved to
        ``BridgedVXLanNetwork._apply_update_dnsmasq``.
        """
        if not self.provide_dhcp and not self.provide_dns:
            return None

        op_type, op_uuid = net_create_and_enqueue(
            network_uuid=str(self.uuid),
            tasks=[net_tasks.network_apply_update_dnsmasq],
            priority=PRIORITY.user_facing_high_io,
        )
        return get_object_class(op_type).from_db(op_uuid)

    def remove_dnsmasq(self):
        """Enqueue a network_apply_remove_dnsmasq NetOp for this network.

        Returns the enqueued NetOp loaded from the database, or ``None``
        if this network does not run dnsmasq. Callers wanting the
        previous synchronous-with-exception semantics call
        ``op.raise_for_error()``. The host-mutating work has moved to
        ``BridgedVXLanNetwork._apply_remove_dnsmasq``.
        """
        if not self.provide_dhcp and not self.provide_dns:
            return None

        op_type, op_uuid = net_create_and_enqueue(
            network_uuid=str(self.uuid),
            tasks=[net_tasks.network_apply_remove_dnsmasq],
            priority=PRIORITY.user_facing,
        )
        return get_object_class(op_type).from_db(op_uuid)

    def remove_nat(self):
        """Enqueue a network_remove_nat NetOp for this network.

        Returns the enqueued NetOp loaded from the database. Callers
        wanting the previous synchronous-with-exception semantics call
        ``op.raise_for_error()``. The host-mutating work has moved to
        ``BridgedVXLanNetwork._apply_remove_nat`` and now runs in the
        net-worker dispatcher on the elected network node.
        """
        op_type, op_uuid = net_create_and_enqueue(
            network_uuid=str(self.uuid),
            tasks=[net_tasks.network_remove_nat],
            priority=PRIORITY.user_facing,
        )
        return get_object_class(op_type).from_db(op_uuid)

    def update_dns_entry(self, name, value):
        """Update a DNS entry and enqueue a dnsmasq restart.

        The attribute mutation (``attrs.hosteddns[name] = value``) is
        DB-only state and stays synchronous. The dnsmasq restart is
        enqueued as a ``network_apply_update_dnsmasq`` NetOp. Returns
        the enqueued NetOp loaded from the database, or ``None`` if
        this network does not provide DNS.
        """
        if not self.provide_dns:
            return None

        attrs = self._ensure_attributes()
        attrs.hosteddns[name] = value
        self._save_attributes(fields=['hosteddns'])
        self.add_event(EVENT_TYPE_MUTATE, 'update dns entry',
                       extra={'name': name, 'value': value})

        op_type, op_uuid = net_create_and_enqueue(
            network_uuid=str(self.uuid),
            tasks=[net_tasks.network_apply_update_dnsmasq],
            priority=PRIORITY.user_facing_high_io,
        )
        return get_object_class(op_type).from_db(op_uuid)

    def remove_dns_entry(self, name):
        """Remove a DNS entry and enqueue a dnsmasq restart.

        The attribute mutation (the ``del attrs.hosteddns[name]``) is
        DB-only state and stays synchronous. The dnsmasq restart is
        enqueued as a ``network_apply_update_dnsmasq`` NetOp regardless
        of whether the name was present (matching the pre-flip
        behaviour). Returns the enqueued NetOp loaded from the
        database, or ``None`` if this network does not provide DNS.
        """
        if not self.provide_dns:
            return None

        attrs = self._ensure_attributes()
        if name in attrs.hosteddns:
            del attrs.hosteddns[name]
            self._save_attributes(fields=['hosteddns'])
            self.add_event(EVENT_TYPE_MUTATE, 'remove dns entry',
                           extra={'name': name})

        op_type, op_uuid = net_create_and_enqueue(
            network_uuid=str(self.uuid),
            tasks=[net_tasks.network_apply_update_dnsmasq],
            priority=PRIORITY.user_facing_high_io,
        )
        return get_object_class(op_type).from_db(op_uuid)

    @_not_on_floating_network
    def ensure_mesh(self):
        """Fan an ensure-mesh NetOp out to every participating hypervisor.

        Every hypervisor with an interface on this network has to maintain
        its own VXLAN FDB entries for the mesh -- the apply method
        excludes ``self`` from the entries it writes, so a one-node run
        leaves every *other* node's FDB stale. Enqueueing only on the
        caller's node was the original behaviour and is the root cause
        of the asymmetric mesh that broke
        ``test_single_virtual_networks_work``: when an instance starts
        on hypervisor A, only A re-meshes, and hypervisor B (which
        already had an instance on the network) never learns the new
        FDB entry for A.

        The fan-out enumerates the set of nodes hosting any of this
        network's interfaces, plus the network node (which participates
        in every mesh -- see below), and enqueues one ensure_mesh op on
        each node's per-node ``network`` queue. The
        enqueue-side dedup in ``net_op.create_and_enqueue`` is keyed on
        ``target='networknode'`` only, so per-node enqueues are *not*
        collapsed across nodes -- each node's worker sees its own op
        and updates its own FDB. Worker-side coalescing on the per-node
        queue is similarly gated off (see the ``queue_is_cluster_wide``
        check in ``BaseClusterOperation.execute``).

        Returns the local-node op when this caller's node is itself a
        participant, so the caller's existing
        ``raise_for_error()``/``poll_until_terminal()`` semantics keep
        working. If the caller's node is not a participant (e.g. an
        API-only node), returns the first remote op enqueued so the
        caller still has *something* to block on.
        """
        # Enumerate participating hypervisors, then map the placement
        # node identifier to a node UUID through the node table. We need
        # the UUID because the per-node queue name is composed from it
        # (``<node_uuid>-network-...``). Skip nodes the database doesn't
        # know about (shouldn't happen in practice, but a stale
        # placement on a deleted node would land here otherwise).
        node_uuids: list[str] = []
        local_node_uuid = config.NODE_UUID
        for identifier in self._mesh_participant_node_identifiers():
            n = Node.from_db(identifier)
            if n is None:
                continue
            node_uuids.append(str(n.uuid))

        # The network node hosts the netns side of every network (DHCP,
        # DNS, NAT and the floating IP path), so it participates in
        # every mesh. The interface walk above only finds nodes hosting
        # instances though, so unless the network node happens to host
        # one itself it never re-meshes: its FDB lacks the flood entry
        # for the instance's hypervisor, and inbound floating traffic
        # then only works while a learned FDB entry for the guest
        # exists -- which ages out once the guest goes idle. Always
        # include the network node in the fan-out. Late-import
        # ``scheduler`` to avoid the circular import at module load
        # (scheduler imports instance, which imports this module).
        from shakenfist import scheduler  # noqa: PLC0415
        try:
            network_node_uuid = str(scheduler.get_network_node().uuid)
            if network_node_uuid not in node_uuids:
                node_uuids.append(network_node_uuid)
        except exceptions.NoNetworkNode:
            # A cluster mid-bootstrap may not have a network node in
            # the database yet; fan out to the instance-hosting nodes
            # only.
            pass

        # If no participating hypervisors were found, still enqueue on
        # the local node. This keeps the bootstrap case sane: an empty
        # network with no interfaces yet should still let a caller
        # observe an ensure_mesh op going to terminal state via
        # raise_for_error().
        if not node_uuids:
            node_uuids = [local_node_uuid]

        local_op_type = None
        local_op_uuid = None
        first_op_type = None
        first_op_uuid = None
        for node_uuid in node_uuids:
            op_type, op_uuid = net_create_and_enqueue(
                network_uuid=str(self.uuid),
                tasks=[net_tasks.network_ensure_mesh],
                priority=PRIORITY.user_facing,
                target=node_uuid,
                family='network',
            )
            if first_op_uuid is None:
                first_op_type, first_op_uuid = op_type, op_uuid
            if node_uuid == local_node_uuid:
                local_op_type, local_op_uuid = op_type, op_uuid

        if local_op_uuid is not None:
            return get_object_class(local_op_type).from_db(local_op_uuid)
        return get_object_class(first_op_type).from_db(first_op_uuid)

    def add_floating_ip(self, floating_address, inner_address, affected_objects):
        """Enqueue a network_add_floating_ip NetOp for this network.

        Emits a synchronous "requesting add floating IP" audit event on
        the caller-supplied ``affected_objects`` to preserve today's
        multi-target correlation for the *requesting* event, then
        enqueues a NetOp. The host-mutating work lives in
        ``BridgedVXLanNetwork._apply_add_floating_ip`` and runs in the
        net-worker dispatcher. Returns the loaded NetOp; callers may
        call ``op.raise_for_error()`` for sync-with-exception semantics.
        """
        affected_objects.append(self)
        affected_objects.append(('network', FLOATING_NETWORK_UUID))
        add_event_multi(
            EVENT_TYPE_AUDIT, affected_objects,
            'requesting add floating IP',
            extra={
                'floating': floating_address,
                'inner': inner_address
            })
        op_type, op_uuid = net_create_and_enqueue(
            network_uuid=str(self.uuid),
            tasks=[net_tasks.network_add_floating_ip],
            priority=PRIORITY.user_facing,
            floating_address=floating_address,
            inner_address=inner_address,
        )
        return get_object_class(op_type).from_db(op_uuid)

    def remove_floating_ip(self, floating_address, inner_address, affected_objects):
        """Enqueue a network_remove_floating_ip NetOp for this network.

        Emits a synchronous "requesting remove floating IP" audit event
        on the caller-supplied ``affected_objects`` to preserve today's
        multi-target correlation for the *requesting* event, then
        enqueues a NetOp. The host-mutating work lives in
        ``BridgedVXLanNetwork._apply_remove_floating_ip``. Returns the
        loaded NetOp; callers may call ``op.raise_for_error()``.
        """
        affected_objects.append(self)
        affected_objects.append(('network', FLOATING_NETWORK_UUID))
        add_event_multi(
            EVENT_TYPE_AUDIT, affected_objects,
            'requesting remove floating IP',
            extra={
                'floating': floating_address,
                'inner': inner_address
            })
        op_type, op_uuid = net_create_and_enqueue(
            network_uuid=str(self.uuid),
            tasks=[net_tasks.network_remove_floating_ip],
            priority=PRIORITY.user_facing,
            floating_address=floating_address,
            inner_address=inner_address,
        )
        return get_object_class(op_type).from_db(op_uuid)

    def route_address(self, floating_address):
        """Enqueue a route_address NetIPOp for this network.

        Emits a synchronous "requesting route floating ip" audit
        event on this Network, then enqueues a NetIPOp. The host-
        mutating work lives in ``BridgedVXLanNetwork._apply_route_address``
        and emits its own "routing floating ip to network" event at
        apply time. Returns the loaded NetIPOp; callers may call
        ``op.raise_for_error()``.

        The "requesting ..." naming mirrors the floating-IP
        ``add_floating_ip`` / ``remove_floating_ip`` pair above:
        the enqueue-side event proves the request was accepted, the
        apply-side event proves the work landed. Using the same
        message string on both sides would emit a duplicate audit
        line and lose that distinction.
        """
        self.add_event(
            EVENT_TYPE_AUDIT, 'requesting route floating ip',
            extra={'floating': floating_address})
        op_type, op_uuid = net_ip_op_schema.create_and_enqueue(
            network_uuid=str(self.uuid),
            ip=floating_address,
            tasks=[net_ip_op_schema.model_tasks.route_address],
            priority=PRIORITY.user_facing,
        )
        return get_object_class(op_type).from_db(op_uuid)

    def unroute_address(self, floating_address):
        """Enqueue an unroute_address NetIPOp for this network.

        Emits a synchronous "requesting unroute floating ip" audit
        event on this Network, then enqueues a NetIPOp. The host-
        mutating work lives in
        ``BridgedVXLanNetwork._apply_unroute_address`` and emits its
        own "unrouting floating ip to network" event at apply time.
        Returns the loaded NetIPOp; callers may call
        ``op.raise_for_error()``. See ``route_address`` for the
        naming rationale.
        """
        self.add_event(
            EVENT_TYPE_AUDIT, 'requesting unroute floating ip',
            extra={'floating': floating_address})
        op_type, op_uuid = net_ip_op_schema.create_and_enqueue(
            network_uuid=str(self.uuid),
            ip=floating_address,
            tasks=[net_ip_op_schema.model_tasks.unroute_address],
            priority=PRIORITY.user_facing,
        )
        return get_object_class(op_type).from_db(op_uuid)


class Networks(dbo_iter):
    base_object = Network

    def _resolve_prefilter_to_states(self):
        # Preserve the pre-phase-4 Networks override behaviour: when
        # no prefilter is set, do not filter on state (return every
        # network and let predicate filters scope). The base class
        # default of ACTIVE_STATES is kept for other inheritors.
        if self.prefilter is None:
            return set()
        return super()._resolve_prefilter_to_states()

    def _find(self, criteria):
        return mariadb.find_networks(criteria)

    def _to_static_values(self, data):
        return Network._static_values_to_dict(data)

    def __iter__(self):
        for _, static_values in self.get_iterator():
            if static_values['uuid'] == str(FLOATING_NETWORK_UUID):
                continue

            n = Network(static_values)
            if not n:
                continue

            filtered = self.apply_filters(n)
            if filtered:
                yield filtered


# Convenience helpers
def networks_in_namespace(namespace):
    return Networks(namespace=namespace)


def floating_network():
    fn = Network.from_db(FLOATING_NETWORK_UUID, suppress_failure_audit=True)
    if not fn:
        Network.new(network_uuid=FLOATING_NETWORK_UUID,
                    vxid=0,
                    netblock=config.FLOATING_NETWORK,
                    provide_dhcp=False,
                    provide_nat=False,
                    provide_dns=False,
                    namespace=None,
                    name='floating')
        fn = Network.from_db(FLOATING_NETWORK_UUID)
    return fn
