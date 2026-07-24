# Tests for Node class and related functionality.
#
# This module tests:
# - NodeData Pydantic model
# - NodeAttributesData Pydantic model
# - Node class initialization from NodeData
# - Node.from_db() lookup by UUID and FQDN
# - Node.new() creating new nodes
# - Daemon state management
# - Lazy-load attribute pattern
# - Node.blobs property
# - UUID persistence (this_node, observe_this_node, _load_persisted_uuid,
#   _persist_uuid)

import os
import tempfile
from unittest import mock
from uuid import UUID

from cryptography.x509.oid import NameOID
from pydantic import ValidationError
import testtools

from shakenfist import node as node_module
from shakenfist.exceptions import NoSuchDaemon
from shakenfist.exceptions import NoSuchDaemonState
from shakenfist.node import Node
from shakenfist.schema.node_attributes import NodeAttributesData
from shakenfist.schema.node_daemon_state import NodeDaemonStateData
from shakenfist.schema.node_data import NodeData
from shakenfist.schema.object_reference import ObjectReference
from shakenfist.schema.object_state import State
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.tests import base


TEST_UUID = UUID('12345678-1234-4321-8234-123456789012')
TEST_UUID_STR = '12345678-1234-4321-8234-123456789012'
TEST_FQDN = 'node1.example.com'
TEST_IP = '10.0.0.1'


class NodeDataTestCase(base.ShakenFistTestCase):
    """Tests for the NodeData Pydantic model."""

    def test_create_from_kwargs(self):
        """Test creating NodeData from keyword arguments."""
        data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=11
        )
        self.assertEqual(str(data.uuid), TEST_UUID_STR)
        self.assertEqual(data.fqdn, TEST_FQDN)
        self.assertEqual(data.ip, TEST_IP)
        self.assertEqual(data.version, 11)

    def test_create_from_uuid_object(self):
        """Test creating NodeData with a UUID object."""
        data = NodeData(
            uuid=TEST_UUID, fqdn=TEST_FQDN,
            ip=TEST_IP, version=11
        )
        self.assertEqual(data.uuid, TEST_UUID)

    def test_immutable(self):
        """Test that NodeData is immutable (frozen)."""
        data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=11
        )
        with testtools.ExpectedException(ValidationError):
            data.fqdn = 'other.example.com'

    def test_invalid_uuid(self):
        """Test that invalid UUID raises ValidationError."""
        with testtools.ExpectedException(ValidationError):
            NodeData(
                uuid='not-a-uuid', fqdn=TEST_FQDN,
                ip=TEST_IP, version=11
            )

    def test_model_dump(self):
        """Test that model_dump() produces expected output."""
        data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=11
        )
        dumped = data.model_dump()
        self.assertEqual(dumped['fqdn'], TEST_FQDN)
        self.assertEqual(dumped['ip'], TEST_IP)
        self.assertEqual(dumped['version'], 11)
        self.assertEqual(str(dumped['uuid']), TEST_UUID_STR)


class NodeAttributesDataTestCase(base.ShakenFistTestCase):
    """Tests for the NodeAttributesData Pydantic model."""

    def test_defaults(self):
        """Test that attributes have sensible defaults."""
        data = NodeAttributesData(uuid=TEST_UUID)
        self.assertEqual(data.last_seen, 0.0)
        self.assertIsNone(data.installed_version)
        self.assertIsNone(data.spice_server_cert_subject)
        self.assertFalse(data.is_etcd_master)
        self.assertFalse(data.is_hypervisor)
        self.assertFalse(data.is_network_node)
        self.assertFalse(data.is_eventlog_node)
        self.assertFalse(data.is_database_node)
        self.assertEqual(data.instances, [])
        self.assertEqual(data.daemons, [])
        self.assertEqual(data.daemon_states, {})
        self.assertIsNone(data.qemu_version)

    def test_mutable(self):
        """Test that NodeAttributesData is mutable (not frozen)."""
        data = NodeAttributesData(uuid=TEST_UUID)
        data.last_seen = 9999.0
        self.assertEqual(data.last_seen, 9999.0)

    def test_database_node_flag_round_trip(self):
        """The live database-tier flag round-trips through the model."""
        data = NodeAttributesData(uuid=TEST_UUID)
        data.is_database_node = True
        self.assertTrue(data.is_database_node)
        dumped = data.model_dump()
        self.assertTrue(dumped['is_database_node'])
        # The vestigial flags stay present (and default False) until
        # their scheduled removal.
        self.assertIn('is_etcd_master', dumped)
        self.assertIn('is_eventlog_node', dumped)

    def test_spice_server_cert_subject_round_trip(self):
        """The SPICE cert subject round-trips through the model."""
        data = NodeAttributesData(uuid=TEST_UUID)
        self.assertIsNone(data.spice_server_cert_subject)
        data.spice_server_cert_subject = 'C=US,O=Shaken Fist,CN=hv1'
        dumped = data.model_dump()
        self.assertEqual(
            dumped['spice_server_cert_subject'], 'C=US,O=Shaken Fist,CN=hv1')

    def test_spice_server_cert_subject_proto_round_trip(self):
        """The SPICE cert subject survives the proto conversion, and its
        optionality (None vs a value) is preserved via the has_ flag."""
        from shakenfist import mariadb

        with_subject = NodeAttributesData(
            uuid=TEST_UUID, spice_server_cert_subject='CN=hv1')
        back = mariadb._node_attrs_from_proto(
            mariadb._node_attrs_to_proto(with_subject))
        self.assertEqual(back.spice_server_cert_subject, 'CN=hv1')

        without = NodeAttributesData(uuid=TEST_UUID)
        back = mariadb._node_attrs_from_proto(
            mariadb._node_attrs_to_proto(without))
        self.assertIsNone(back.spice_server_cert_subject)

    def test_daemon_states_dict(self):
        """Test daemon_states as a nested dict."""
        data = NodeAttributesData(uuid=TEST_UUID)
        data.daemon_states['api'] = {
            'value': 'daemon-running',
            'update_time': 1234567890.0,
            'message': None
        }
        self.assertEqual(
            data.daemon_states['api']['value'], 'daemon-running')


class NodeClassTestCase(base.ShakenFistTestCase):
    """Tests for the Node class initialization."""

    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(value='created', update_time=1234567890.0))
    def test_init_from_node_data(self, mock_get_state, mock_get_min):
        """Test creating Node from NodeData."""
        data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=Node.current_version
        )
        n = Node(data)
        self.assertEqual(n.uuid, TEST_UUID)
        self.assertEqual(n.fqdn, TEST_FQDN)
        self.assertEqual(n.ip, TEST_IP)

    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(value='created', update_time=1234567890.0))
    def test_lazy_attributes_not_loaded_on_init(
            self, mock_get_state, mock_get_min):
        """Test that attributes aren't loaded until first access."""
        data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=Node.current_version
        )
        n = Node(data)
        # The private flag should be False - not loaded yet
        self.assertFalse(n._Node__attributes_loaded)

    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(value='created', update_time=1234567890.0))
    def test_load_attributes_called_on_access(
            self, mock_get_state, mock_get_min,
            mock_get_attrs):
        """Test that accessing a property triggers attribute load."""
        attrs = NodeAttributesData(uuid=TEST_UUID, last_seen=5555.0)
        mock_get_attrs.return_value = attrs

        data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=Node.current_version
        )
        n = Node(data)

        # Access last_seen, which should trigger _load_attributes
        result = n.last_seen
        self.assertEqual(result, 5555.0)
        mock_get_attrs.assert_called_once_with(TEST_UUID)

    @mock.patch(
        'shakenfist.node.mariadb.get_references_from', return_value=[])
    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(value='created', update_time=1234567890.0))
    def test_load_attributes_returns_defaults_when_none(
            self, mock_get_state, mock_get_min,
            mock_get_attrs, mock_get_refs):
        """Test defaults when no attributes record exists."""
        mock_get_attrs.return_value = None

        data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=Node.current_version
        )
        n = Node(data)

        self.assertEqual(n.last_seen, 0)
        self.assertIsNone(n.installed_version)
        self.assertEqual(n.instances, [])


class NodeFromDbTestCase(base.ShakenFistTestCase):
    """Tests for Node.from_db() lookup methods."""

    @mock.patch('shakenfist.node.add_event')
    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(value='created', update_time=1234567890.0))
    @mock.patch('shakenfist.node.mariadb.get_node')
    def test_from_db_by_uuid(
            self, mock_get_node, mock_get_state,
            mock_get_min, mock_add_event):
        """Test from_db() with a UUID object."""
        node_data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=Node.current_version
        )
        mock_get_node.return_value = node_data

        n = Node.from_db(TEST_UUID)
        self.assertIsNotNone(n)
        self.assertEqual(n.fqdn, TEST_FQDN)
        mock_get_node.assert_called_once_with(TEST_UUID)

    @mock.patch('shakenfist.node.add_event')
    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(value='created', update_time=1234567890.0))
    @mock.patch('shakenfist.node.mariadb.get_node_by_fqdn')
    @mock.patch('shakenfist.node.mariadb.get_node')
    def test_from_db_by_fqdn(
            self, mock_get_node, mock_get_by_fqdn,
            mock_get_state, mock_get_min, mock_add_event):
        """Test from_db() with an FQDN string."""
        node_data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=Node.current_version
        )
        # UUID parse will fail for FQDN, so get_node won't be called
        mock_get_node.return_value = None
        mock_get_by_fqdn.return_value = node_data

        n = Node.from_db(TEST_FQDN)
        self.assertIsNotNone(n)
        self.assertEqual(n.fqdn, TEST_FQDN)
        mock_get_by_fqdn.assert_called_once_with(TEST_FQDN)

    @mock.patch('shakenfist.node.add_event')
    @mock.patch('shakenfist.node.mariadb.get_node_by_fqdn')
    @mock.patch('shakenfist.node.mariadb.get_node')
    def test_from_db_not_found(
            self, mock_get_node, mock_get_by_fqdn,
            mock_add_event):
        """Test from_db() returns None when not found."""
        mock_get_node.return_value = None
        mock_get_by_fqdn.return_value = None

        n = Node.from_db(TEST_FQDN)
        self.assertIsNone(n)

    def test_from_db_none_identifier(self):
        """Test from_db() returns None for empty identifier."""
        n = Node.from_db('')
        self.assertIsNone(n)

    @mock.patch('shakenfist.node.add_event')
    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(value='created', update_time=1234567890.0))
    @mock.patch('shakenfist.node.mariadb.get_node')
    def test_from_db_by_uuid_string(
            self, mock_get_node, mock_get_state,
            mock_get_min, mock_add_event):
        """Test from_db() with a UUID as a string."""
        node_data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=Node.current_version
        )
        mock_get_node.return_value = node_data

        n = Node.from_db(TEST_UUID_STR)
        self.assertIsNotNone(n)
        self.assertEqual(n.fqdn, TEST_FQDN)


class NodeNewTestCase(base.ShakenFistTestCase):
    """Tests for Node.new() creating new nodes."""

    @mock.patch('shakenfist.node.add_event')
    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch('shakenfist.mariadb.set_state')
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(
            value='initial', update_time=1234567890.0))
    @mock.patch('shakenfist.node.mariadb.get_node_by_fqdn')
    @mock.patch('shakenfist.node.mariadb.get_node')
    @mock.patch('shakenfist.node.mariadb.create_node')
    def test_new_creates_node(
            self, mock_create, mock_get_node,
            mock_get_by_fqdn, mock_get_state,
            mock_set_state, mock_get_min,
            mock_add_event):
        """Test Node.new() creates a new node when none exists."""
        # First from_db(FQDN) returns None (FQDN not a UUID, so
        # get_node_by_fqdn is called instead of get_node)
        mock_get_by_fqdn.return_value = None
        # Second from_db(UUID str) parses as UUID, calls get_node
        mock_get_node.return_value = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=Node.current_version
        )
        mock_create.return_value = True

        with mock.patch('shakenfist.node.uuid.uuid4',
                        return_value=TEST_UUID):
            n = Node.new(TEST_FQDN, TEST_IP)

        self.assertIsNotNone(n)
        mock_create.assert_called_once()

    @mock.patch('shakenfist.node.add_event')
    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(value='created', update_time=1234567890.0))
    @mock.patch('shakenfist.node.mariadb.get_node_by_fqdn')
    @mock.patch('shakenfist.node.mariadb.get_node')
    def test_new_returns_existing_node(
            self, mock_get_node, mock_get_by_fqdn,
            mock_get_state, mock_get_min, mock_add_event):
        """Test Node.new() returns existing node if FQDN matches."""
        existing = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=Node.current_version
        )
        mock_get_node.return_value = None
        mock_get_by_fqdn.return_value = existing

        n = Node.new(TEST_FQDN, TEST_IP)
        self.assertIsNotNone(n)
        self.assertEqual(n.fqdn, TEST_FQDN)


class NodeDaemonStateTestCase(base.ShakenFistTestCase):
    """Tests for daemon state management."""

    def _make_node(self):
        """Helper to create a Node with mocked dependencies."""
        n = Node.__new__(Node)
        n._Node__fqdn = TEST_FQDN
        n._Node__ip = TEST_IP
        n._Node__attributes = None
        n._Node__attributes_loaded = False
        n._DatabaseBackedObject__uuid = TEST_UUID
        n._DatabaseBackedObject__version = Node.current_version
        n._DatabaseBackedObject__in_memory_only = False
        return n

    @mock.patch('shakenfist.node.mariadb.get_node_daemon_state')
    def test_get_daemon_state_no_attributes(self, mock_get_state):
        """Test get_daemon_state when no row exists."""
        mock_get_state.return_value = None
        n = self._make_node()

        state = n.get_daemon_state('api')
        self.assertIsNone(state.value)
        self.assertEqual(state.update_time, 0)

    @mock.patch('shakenfist.node.mariadb.get_node_daemon_state')
    def test_get_daemon_state_with_value(self, mock_get_state):
        """Test get_daemon_state returns correct state."""
        mock_get_state.return_value = NodeDaemonStateData(
            node_uuid=TEST_UUID,
            daemon='api',
            value='daemon-running',
            update_time=1234567890.0,
            message=None,
        )
        n = self._make_node()

        state = n.get_daemon_state('api')
        self.assertEqual(state.value, 'daemon-running')
        self.assertEqual(state.update_time, 1234567890.0)

    def test_get_daemon_state_invalid_daemon(self):
        """Test get_daemon_state raises for invalid daemon."""
        n = self._make_node()
        self.assertRaises(
            NoSuchDaemon, n.get_daemon_state, 'bogus')

    @mock.patch('shakenfist.node.mariadb.get_all_node_daemon_states',
                return_value=[])
    @mock.patch('shakenfist.node.mariadb.set_node_daemon_state',
                return_value=True)
    @mock.patch('shakenfist.node.mariadb.update_node_attributes')
    @mock.patch('shakenfist.node.mariadb.create_node_attributes')
    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    @mock.patch('shakenfist.node.add_event')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    @mock.patch('shakenfist.mariadb.set_state')
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(
            value='created', update_time=1234567890.0))
    def test_register_daemon(
            self, mock_get_state, mock_set_state,
            mock_lock, mock_add_event, mock_get_attrs,
            mock_create_attrs, mock_update_attrs,
            mock_set_daemon_state, mock_get_all_states):
        """Test registering a daemon adds it to the list."""
        attrs = NodeAttributesData(uuid=TEST_UUID)
        mock_get_attrs.return_value = attrs
        mock_lock.return_value = mock.MagicMock()
        mock_update_attrs.return_value = True

        n = self._make_node()
        n.register_daemon('api')

        self.assertIn('api', attrs.daemons)
        mock_set_daemon_state.assert_called_once()

    @mock.patch('shakenfist.node.mariadb.get_all_node_daemon_states',
                return_value=[])
    @mock.patch('shakenfist.node.mariadb.set_node_daemon_state',
                return_value=True)
    @mock.patch('shakenfist.node.mariadb.update_node_attributes')
    @mock.patch('shakenfist.node.mariadb.create_node_attributes')
    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    @mock.patch('shakenfist.node.add_event')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    @mock.patch('shakenfist.mariadb.set_state')
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(
            value='created', update_time=1234567890.0))
    def test_register_daemon_already_registered_is_noop(
            self, mock_get_state, mock_set_state,
            mock_lock, mock_add_event, mock_get_attrs,
            mock_create_attrs, mock_update_attrs,
            mock_set_daemon_state, mock_get_all_states):
        """Re-registering an existing daemon must not reset its state.

        A deploy re-runs register-daemon every pass; with restart-on-change the
        daemon is not restarted when nothing changed. Resetting a running
        daemon's state to STOPPED here would leave it stuck stopped and the node
        wrongly degraded, so re-registration must be a true no-op.
        """
        attrs = NodeAttributesData(uuid=TEST_UUID)
        attrs.daemons.append('api')
        mock_get_attrs.return_value = attrs
        mock_lock.return_value = mock.MagicMock()
        mock_update_attrs.return_value = True

        n = self._make_node()
        n.register_daemon('api')

        # Still registered exactly once, and no state reset / event emitted.
        self.assertEqual(['api'], attrs.daemons)
        mock_set_daemon_state.assert_not_called()
        mock_add_event.assert_not_called()

    def test_register_invalid_daemon(self):
        """Test registering invalid daemon raises."""
        n = self._make_node()
        self.assertRaises(
            NoSuchDaemon, n.register_daemon, 'bogus')

    @mock.patch('shakenfist.node.mariadb.delete_node_daemon_state',
                return_value=True)
    @mock.patch('shakenfist.node.mariadb.update_node_attributes')
    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    @mock.patch('shakenfist.node.add_event')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(
            value='created', update_time=1234567890.0))
    def test_deregister_daemon(
            self, mock_get_state, mock_lock,
            mock_add_event, mock_get_attrs,
            mock_update_attrs, mock_delete_daemon_state):
        """Test deregistering a daemon removes it."""
        attrs = NodeAttributesData(uuid=TEST_UUID)
        attrs.daemons.append('api')
        mock_get_attrs.return_value = attrs
        mock_lock.return_value = mock.MagicMock()
        mock_update_attrs.return_value = True

        n = self._make_node()
        n.deregister_daemon('api')

        self.assertNotIn('api', attrs.daemons)
        mock_delete_daemon_state.assert_called_once_with(TEST_UUID, 'api')


class NodeSetDaemonStateTestCase(base.ShakenFistTestCase):
    """Tests for set_daemon_state()."""

    def _make_node(self):
        """Helper to create a Node with mocked dependencies."""
        n = Node.__new__(Node)
        n._Node__fqdn = TEST_FQDN
        n._Node__ip = TEST_IP
        n._Node__attributes = None
        n._Node__attributes_loaded = False
        n._DatabaseBackedObject__uuid = TEST_UUID
        n._DatabaseBackedObject__version = Node.current_version
        n._DatabaseBackedObject__in_memory_only = False
        return n

    def test_set_daemon_state_invalid_daemon(self):
        """Test set_daemon_state raises for invalid daemon."""
        n = self._make_node()
        self.assertRaises(
            NoSuchDaemon, n.set_daemon_state,
            'bogus', Node.DAEMON_STATE_RUNNING)

    def test_set_daemon_state_invalid_state(self):
        """Test set_daemon_state raises for invalid state."""
        n = self._make_node()
        self.assertRaises(
            NoSuchDaemonState, n.set_daemon_state,
            'api', 'bogus-state')

    @mock.patch('shakenfist.mariadb.set_node_daemon_state')
    def test_error_node_with_degraded_daemon_stays_error(
            self, mock_set):
        """A resource-health error must not be downgraded to degraded (which
        is schedulable) when a daemon on the failed node reports stopped."""
        n = self._make_node()
        n.add_event = mock.MagicMock()
        with mock.patch.object(
                Node, 'get_degraded_daemons', return_value=['queues']), \
                mock.patch.object(
                    Node, 'state', new_callable=mock.PropertyMock) as mock_state:
            mock_state.return_value = mock.Mock(value=Node.STATE_ERROR)
            n.set_daemon_state('queues', Node.DAEMON_STATE_STOPPED)
        # The node state was read but never reassigned to degraded.
        self.assertNotIn(
            mock.call(Node.STATE_DEGRADED), mock_state.mock_calls)

    @mock.patch('shakenfist.mariadb.set_node_daemon_state')
    def test_created_node_with_degraded_daemon_goes_degraded(
            self, mock_set):
        """The normal reconcile is unchanged: a created node with a degraded
        daemon still transitions to degraded."""
        n = self._make_node()
        n.add_event = mock.MagicMock()
        with mock.patch.object(
                Node, 'get_degraded_daemons', return_value=['queues']), \
                mock.patch.object(
                    Node, 'state', new_callable=mock.PropertyMock) as mock_state:
            mock_state.return_value = mock.Mock(value=Node.STATE_CREATED)
            n.set_daemon_state('queues', Node.DAEMON_STATE_STOPPED)
        self.assertIn(
            mock.call(Node.STATE_DEGRADED), mock_state.mock_calls)


class NodeSetLifecycleStateTestCase(base.ShakenFistTestCase):
    """set_lifecycle_state() must not walk a resource-health error back to a
    schedulable state on a sentinel start/stop handshake."""

    def _make_node(self):
        n = Node.__new__(Node)
        n._Node__fqdn = TEST_FQDN
        n._Node__ip = TEST_IP
        n._Node__attributes = None
        n._Node__attributes_loaded = False
        n._DatabaseBackedObject__uuid = TEST_UUID
        n._DatabaseBackedObject__version = Node.current_version
        n._DatabaseBackedObject__in_memory_only = False
        n.add_event = mock.MagicMock()
        return n

    def test_errored_node_is_not_moved(self):
        n = self._make_node()
        with mock.patch.object(
                Node, 'state', new_callable=mock.PropertyMock) as mock_state:
            mock_state.return_value = mock.Mock(value=Node.STATE_ERROR)
            result = n.set_lifecycle_state(Node.STATE_CREATED)
        self.assertFalse(result)
        self.assertNotIn(
            mock.call(Node.STATE_CREATED), mock_state.mock_calls)
        n.add_event.assert_called_once()

    def test_non_errored_node_is_moved(self):
        n = self._make_node()
        with mock.patch.object(
                Node, 'state', new_callable=mock.PropertyMock) as mock_state:
            mock_state.return_value = mock.Mock(value=Node.STATE_STOPPED)
            result = n.set_lifecycle_state(Node.STATE_CREATED)
        self.assertTrue(result)
        self.assertIn(
            mock.call(Node.STATE_CREATED), mock_state.mock_calls)


class NodeInstanceManagementTestCase(base.ShakenFistTestCase):
    """Tests for instance add/remove on nodes."""

    def _make_node(self):
        """Helper to create a Node with mocked dependencies."""
        n = Node.__new__(Node)
        n._Node__fqdn = TEST_FQDN
        n._Node__ip = TEST_IP
        n._Node__attributes = None
        n._Node__attributes_loaded = False
        n._DatabaseBackedObject__uuid = TEST_UUID
        n._DatabaseBackedObject__version = Node.current_version
        n._DatabaseBackedObject__in_memory_only = False
        return n

    @mock.patch('shakenfist.node.mariadb.update_node_attributes')
    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    @mock.patch('shakenfist.node.mariadb.record_relationship')
    def test_add_instance(
            self, mock_record, mock_lock, mock_get_attrs,
            mock_update_attrs):
        """Adding an instance records a reference and dual-writes JSON."""
        attrs = NodeAttributesData(uuid=TEST_UUID)
        mock_get_attrs.return_value = attrs
        mock_lock.return_value = mock.MagicMock()
        mock_update_attrs.return_value = True

        n = self._make_node()
        inst_uuid = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        n.add_instance(inst_uuid)

        mock_record.assert_called_once_with(
            ObjectType.NODE, str(TEST_UUID),
            RelationshipType.INSTANCE_LOCATION, None,
            ObjectType.INSTANCE, inst_uuid)
        self.assertIn(inst_uuid, attrs.instances)
        mock_update_attrs.assert_called_once_with(
            attrs, fields=['instances'])

    @mock.patch('shakenfist.node.mariadb.update_node_attributes')
    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    @mock.patch('shakenfist.node.mariadb.remove_relationship')
    def test_remove_instance(
            self, mock_remove, mock_lock, mock_get_attrs,
            mock_update_attrs):
        """Removing an instance removes the reference and JSON entry."""
        inst_uuid = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        attrs = NodeAttributesData(uuid=TEST_UUID)
        attrs.instances.append(inst_uuid)
        mock_get_attrs.return_value = attrs
        mock_lock.return_value = mock.MagicMock()
        mock_update_attrs.return_value = True

        n = self._make_node()
        n.remove_instance(inst_uuid)

        mock_remove.assert_called_once_with(
            ObjectType.NODE, str(TEST_UUID),
            RelationshipType.INSTANCE_LOCATION, None,
            ObjectType.INSTANCE, inst_uuid)
        self.assertNotIn(inst_uuid, attrs.instances)
        mock_update_attrs.assert_called_once_with(
            attrs, fields=['instances'])

    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    @mock.patch('shakenfist.node.mariadb.get_references_from')
    def test_instances_property_unions_legacy(
            self, mock_get_refs, mock_get_attrs):
        """Node.instances unions references with the legacy column."""
        ref_uuid = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        legacy_uuid = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
        mock_get_refs.return_value = [
            ObjectReference(
                source_object_type=ObjectType.NODE,
                source_uuid=str(TEST_UUID),
                relationship=RelationshipType.INSTANCE_LOCATION,
                relationship_value=None,
                target_object_type=ObjectType.INSTANCE,
                target_uuid=ref_uuid,
                created=1234567890.0,
                last_active=1234567890.0)]
        attrs = NodeAttributesData(uuid=TEST_UUID)
        attrs.instances.extend([ref_uuid, legacy_uuid])
        mock_get_attrs.return_value = attrs

        n = self._make_node()
        self.assertEqual([ref_uuid, legacy_uuid], n.instances)
        mock_get_refs.assert_called_once_with(
            ObjectType.NODE, str(TEST_UUID),
            RelationshipType.INSTANCE_LOCATION)


class NodeBlobsTestCase(base.ShakenFistTestCase):
    """Tests for the Node.blobs property."""

    @mock.patch('shakenfist.node.mariadb.get_references_from')
    @mock.patch.object(
        Node, '__init__', lambda self, static_values: None)
    def test_blobs_returns_blob_uuids(self, mock_get_refs):
        """Test that Node.blobs returns a list of blob UUIDs."""
        blob_uuid1 = '11111111-1111-1111-1111-111111111111'
        blob_uuid2 = '22222222-2222-2222-2222-222222222222'

        mock_get_refs.return_value = [
            ObjectReference(
                source_object_type=ObjectType.NODE,
                source_uuid='node1.example.com',
                relationship=RelationshipType.BLOB_LOCATION,
                relationship_value=None,
                target_object_type=ObjectType.BLOB,
                target_uuid=blob_uuid1,
                created=1234567890.0,
                last_active=1234567890.0
            ),
            ObjectReference(
                source_object_type=ObjectType.NODE,
                source_uuid='node1.example.com',
                relationship=RelationshipType.BLOB_LOCATION,
                relationship_value=None,
                target_object_type=ObjectType.BLOB,
                target_uuid=blob_uuid2,
                created=1234567890.0,
                last_active=1234567890.0
            ),
        ]

        node = Node.__new__(Node)
        node._Node__fqdn = 'node1.example.com'

        blobs = node.blobs

        self.assertEqual(blobs, [blob_uuid1, blob_uuid2])
        mock_get_refs.assert_called_once_with(
            ObjectType.NODE, 'node1.example.com',
            RelationshipType.BLOB_LOCATION)

    @mock.patch('shakenfist.node.mariadb.get_references_from')
    @mock.patch.object(
        Node, '__init__', lambda self, static_values: None)
    def test_blobs_returns_empty_list_when_no_blobs(
            self, mock_get_refs):
        """Test that Node.blobs returns empty list when no blobs."""
        mock_get_refs.return_value = []

        node = Node.__new__(Node)
        node._Node__fqdn = 'node1.example.com'

        blobs = node.blobs

        self.assertEqual(blobs, [])
        mock_get_refs.assert_called_once_with(
            ObjectType.NODE, 'node1.example.com',
            RelationshipType.BLOB_LOCATION)


class NodeMariaDBFunctionsTestCase(base.ShakenFistTestCase):
    """Tests for mariadb node functions routing."""

    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=False)
    @mock.patch(
        'shakenfist.mariadb._direct_update_node',
        return_value=True)
    def test_update_node_direct(self, mock_direct, mock_use_db):
        """Test update_node routes to direct function."""
        from shakenfist import mariadb

        data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=11
        )
        result = mariadb.update_node(data)
        self.assertTrue(result)
        mock_direct.assert_called_once_with(data)

    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=True)
    @mock.patch(
        'shakenfist.mariadb._grpc_update_node',
        return_value=True)
    def test_update_node_grpc(self, mock_grpc, mock_use_db):
        """Test update_node routes to gRPC function."""
        from shakenfist import mariadb

        data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=11
        )
        result = mariadb.update_node(data)
        self.assertTrue(result)
        mock_grpc.assert_called_once_with(data)

    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=False)
    @mock.patch('shakenfist.mariadb._direct_get_node')
    def test_get_node_direct(self, mock_direct, mock_use_db):
        """Test get_node routes to direct function."""
        from shakenfist import mariadb

        expected = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=11
        )
        mock_direct.return_value = expected

        result = mariadb.get_node(TEST_UUID)
        self.assertEqual(result, expected)
        mock_direct.assert_called_once_with(TEST_UUID)

    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=False)
    @mock.patch('shakenfist.mariadb._direct_get_node_by_fqdn')
    def test_get_node_by_fqdn_direct(
            self, mock_direct, mock_use_db):
        """Test get_node_by_fqdn routes to direct function."""
        from shakenfist import mariadb

        expected = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=11
        )
        mock_direct.return_value = expected

        result = mariadb.get_node_by_fqdn(TEST_FQDN)
        self.assertEqual(result, expected)
        mock_direct.assert_called_once_with(TEST_FQDN)


class NodesDegradedTestCase(base.ShakenFistTestCase):
    """Tests for get_degraded_daemons()."""

    def _make_node(self):
        """Helper to create a Node with mocked dependencies."""
        n = Node.__new__(Node)
        n._Node__fqdn = TEST_FQDN
        n._Node__ip = TEST_IP
        n._Node__attributes = None
        n._Node__attributes_loaded = False
        n._DatabaseBackedObject__uuid = TEST_UUID
        n._DatabaseBackedObject__version = Node.current_version
        n._DatabaseBackedObject__in_memory_only = False
        return n

    @mock.patch('shakenfist.node.mariadb.get_all_node_daemon_states')
    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    def test_no_degraded_when_all_running(
            self, mock_get_attrs, mock_get_all_states):
        """Test no degraded daemons when all are running."""
        attrs = NodeAttributesData(uuid=TEST_UUID)
        attrs.daemons = ['api', 'database']
        mock_get_attrs.return_value = attrs
        mock_get_all_states.return_value = [
            NodeDaemonStateData(
                node_uuid=TEST_UUID, daemon='api',
                value='daemon-running', update_time=1234567890.0),
            NodeDaemonStateData(
                node_uuid=TEST_UUID, daemon='database',
                value='daemon-running', update_time=1234567890.0),
        ]

        n = self._make_node()
        degraded = n.get_degraded_daemons()
        self.assertEqual(degraded, [])

    @mock.patch('shakenfist.node.mariadb.get_all_node_daemon_states')
    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    def test_degraded_when_daemon_stopped(
            self, mock_get_attrs, mock_get_all_states):
        """Test daemon shows as degraded when stopped."""
        attrs = NodeAttributesData(uuid=TEST_UUID)
        attrs.daemons = ['api', 'database']
        mock_get_attrs.return_value = attrs
        mock_get_all_states.return_value = [
            NodeDaemonStateData(
                node_uuid=TEST_UUID, daemon='api',
                value='daemon-running', update_time=1234567890.0),
            NodeDaemonStateData(
                node_uuid=TEST_UUID, daemon='database',
                value='daemon-stopped', update_time=1234567890.0),
        ]

        n = self._make_node()
        degraded = n.get_degraded_daemons()
        self.assertIn('database', degraded)
        self.assertNotIn('api', degraded)

    @mock.patch('shakenfist.node.mariadb.get_all_node_daemon_states',
                return_value=[])
    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    def test_degraded_when_no_state(
            self, mock_get_attrs, mock_get_all_states):
        """Test daemon shows as degraded when it has no state."""
        attrs = NodeAttributesData(uuid=TEST_UUID)
        attrs.daemons = ['api']
        mock_get_attrs.return_value = attrs

        n = self._make_node()
        degraded = n.get_degraded_daemons()
        self.assertIn('api', degraded)


class NodeUUIDPersistenceTestCase(base.ShakenFistTestCase):
    """Tests for node UUID persistence (_load_persisted_uuid,
    _persist_uuid)."""

    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(
            lambda: os.path.exists(self.tmpdir) and
            __import__('shutil').rmtree(self.tmpdir))

    @mock.patch('shakenfist.node.config')
    def test_persist_uuid_creates_file(self, mock_config):
        """Test _persist_uuid writes UUID to the expected file."""
        mock_config.STORAGE_PATH = self.tmpdir
        Node._persist_uuid(TEST_UUID)

        path = os.path.join(self.tmpdir, 'node_uuid')
        self.assertTrue(os.path.exists(path))
        with open(path, 'r') as f:
            self.assertEqual(f.read(), TEST_UUID_STR)

    @mock.patch('shakenfist.node.config')
    def test_load_persisted_uuid_from_file(self, mock_config):
        """Test _load_persisted_uuid reads from local file."""
        mock_config.STORAGE_PATH = self.tmpdir
        mock_config.NODE_UUID = None

        path = os.path.join(self.tmpdir, 'node_uuid')
        with open(path, 'w') as f:
            f.write(TEST_UUID_STR)

        result = Node._load_persisted_uuid()
        self.assertEqual(result, TEST_UUID_STR)

    @mock.patch('shakenfist.node.config')
    def test_load_persisted_uuid_config_takes_precedence(
            self, mock_config):
        """Test NODE_UUID config overrides local file."""
        other_uuid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        mock_config.STORAGE_PATH = self.tmpdir
        mock_config.NODE_UUID = other_uuid

        # Write a different UUID to the file
        path = os.path.join(self.tmpdir, 'node_uuid')
        with open(path, 'w') as f:
            f.write(TEST_UUID_STR)

        result = Node._load_persisted_uuid()
        self.assertEqual(result, other_uuid)

    @mock.patch('shakenfist.node.config')
    def test_load_persisted_uuid_invalid_config_ignored(
            self, mock_config):
        """Test invalid NODE_UUID config falls back to file."""
        mock_config.STORAGE_PATH = self.tmpdir
        mock_config.NODE_UUID = 'not-a-uuid'

        path = os.path.join(self.tmpdir, 'node_uuid')
        with open(path, 'w') as f:
            f.write(TEST_UUID_STR)

        result = Node._load_persisted_uuid()
        self.assertEqual(result, TEST_UUID_STR)

    @mock.patch('shakenfist.node.config')
    def test_load_persisted_uuid_no_file_no_config(
            self, mock_config):
        """Test returns None when nothing is persisted."""
        mock_config.STORAGE_PATH = self.tmpdir
        mock_config.NODE_UUID = None

        result = Node._load_persisted_uuid()
        self.assertIsNone(result)

    @mock.patch('shakenfist.node.config')
    def test_load_persisted_uuid_invalid_file_content(
            self, mock_config):
        """Test invalid UUID in file returns None."""
        mock_config.STORAGE_PATH = self.tmpdir
        mock_config.NODE_UUID = None

        path = os.path.join(self.tmpdir, 'node_uuid')
        with open(path, 'w') as f:
            f.write('garbage-content')

        result = Node._load_persisted_uuid()
        self.assertIsNone(result)

    @mock.patch('shakenfist.node.config')
    def test_persist_uuid_handles_missing_directory(
            self, mock_config):
        """Test _persist_uuid creates parent dirs if needed."""
        nested = os.path.join(self.tmpdir, 'sub', 'dir')
        mock_config.STORAGE_PATH = nested
        Node._persist_uuid(TEST_UUID)

        path = os.path.join(nested, 'node_uuid')
        self.assertTrue(os.path.exists(path))


class NodeThisNodeTestCase(base.ShakenFistTestCase):
    """Tests for Node.this_node() convenience method."""

    @mock.patch('shakenfist.node.add_event')
    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(
            value='created', update_time=1234567890.0))
    @mock.patch('shakenfist.node.mariadb.get_node')
    @mock.patch('shakenfist.node.Node._load_persisted_uuid')
    @mock.patch('shakenfist.node.config')
    def test_this_node_uses_persisted_uuid(
            self, mock_config, mock_load_uuid,
            mock_get_node, mock_get_state, mock_get_min,
            mock_add_event):
        """Test this_node() uses persisted UUID for lookup."""
        mock_config.NODE_NAME = TEST_FQDN
        mock_load_uuid.return_value = TEST_UUID_STR

        node_data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=Node.current_version
        )
        mock_get_node.return_value = node_data

        n = Node.this_node()
        self.assertIsNotNone(n)
        self.assertEqual(n.fqdn, TEST_FQDN)
        mock_get_node.assert_called_once_with(TEST_UUID)

    @mock.patch('shakenfist.node.add_event')
    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(
            value='created', update_time=1234567890.0))
    @mock.patch('shakenfist.node.mariadb.get_node_by_fqdn')
    @mock.patch('shakenfist.node.mariadb.get_node')
    @mock.patch('shakenfist.node.Node._load_persisted_uuid')
    @mock.patch('shakenfist.node.config')
    def test_this_node_falls_back_to_fqdn(
            self, mock_config, mock_load_uuid,
            mock_get_node, mock_get_by_fqdn,
            mock_get_state, mock_get_min, mock_add_event):
        """Test this_node() falls back to FQDN when no UUID."""
        mock_config.NODE_NAME = TEST_FQDN
        mock_load_uuid.return_value = None
        mock_get_node.return_value = None

        node_data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=Node.current_version
        )
        mock_get_by_fqdn.return_value = node_data

        n = Node.this_node()
        self.assertIsNotNone(n)
        mock_get_by_fqdn.assert_called_once_with(TEST_FQDN)

    @mock.patch('shakenfist.node.add_event')
    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(
            value='created', update_time=1234567890.0))
    @mock.patch('shakenfist.node.mariadb.get_node_by_fqdn')
    @mock.patch('shakenfist.node.mariadb.get_node')
    @mock.patch('shakenfist.node.Node._load_persisted_uuid')
    @mock.patch('shakenfist.node.config')
    def test_this_node_ignores_uuid_with_wrong_fqdn(
            self, mock_config, mock_load_uuid,
            mock_get_node, mock_get_by_fqdn,
            mock_get_state, mock_get_min, mock_add_event):
        """Test this_node() ignores persisted UUID if FQDN mismatch."""
        mock_config.NODE_NAME = 'other.example.com'
        mock_load_uuid.return_value = TEST_UUID_STR

        # Persisted UUID points to a different node
        wrong_node = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=Node.current_version
        )
        mock_get_node.return_value = wrong_node

        # FQDN lookup finds the correct node
        correct_uuid = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
        correct_node = NodeData(
            uuid=correct_uuid, fqdn='other.example.com',
            ip='10.0.0.2', version=Node.current_version
        )
        mock_get_by_fqdn.return_value = correct_node

        n = Node.this_node()
        self.assertIsNotNone(n)
        self.assertEqual(n.fqdn, 'other.example.com')
        mock_get_by_fqdn.assert_called_once_with(
            'other.example.com')


class NodeObserveThisNodeTestCase(base.ShakenFistTestCase):
    """Tests for Node.observe_this_node()."""

    @mock.patch('shakenfist.node.Node._save_attributes')
    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    @mock.patch('shakenfist.node.mariadb.create_node_attributes')
    @mock.patch('shakenfist.node.add_event')
    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(
            value='created', update_time=1234567890.0))
    @mock.patch('shakenfist.node.mariadb.get_node')
    @mock.patch('shakenfist.node.Node._load_persisted_uuid')
    @mock.patch('shakenfist.node.config')
    def test_observe_uses_persisted_uuid(
            self, mock_config, mock_load_uuid,
            mock_get_node, mock_get_state, mock_get_min,
            mock_add_event, mock_create_attrs,
            mock_get_attrs, mock_save_attrs):
        """Test observe_this_node uses this_node() for lookup."""
        mock_config.NODE_NAME = TEST_FQDN
        mock_config.NODE_MESH_IP = TEST_IP
        mock_config.NODE_IS_HYPERVISOR = True
        mock_config.NODE_IS_NETWORK_NODE = False
        mock_load_uuid.return_value = TEST_UUID_STR

        node_data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=Node.current_version
        )
        mock_get_node.return_value = node_data

        attrs = NodeAttributesData(uuid=TEST_UUID)
        mock_get_attrs.return_value = attrs

        Node.observe_this_node()

        self.assertTrue(attrs.is_hypervisor)
        self.assertFalse(attrs.is_etcd_master)
        mock_save_attrs.assert_called_once()

    @mock.patch(
        'shakenfist.node.read_spice_server_cert_subject',
        return_value='CN=node1.example.com')
    @mock.patch('shakenfist.node.Node._save_attributes')
    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    @mock.patch('shakenfist.node.mariadb.create_node_attributes')
    @mock.patch('shakenfist.node.add_event')
    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(
            value='created', update_time=1234567890.0))
    @mock.patch('shakenfist.node.mariadb.get_node')
    @mock.patch('shakenfist.node.Node._load_persisted_uuid')
    @mock.patch('shakenfist.node.config')
    def test_observe_records_spice_cert_subject(
            self, mock_config, mock_load_uuid,
            mock_get_node, mock_get_state, mock_get_min,
            mock_add_event, mock_create_attrs,
            mock_get_attrs, mock_save_attrs, mock_read_subject):
        """observe_this_node records the SPICE cert subject and names it
        among the fields it persists."""
        mock_config.NODE_NAME = TEST_FQDN
        mock_config.NODE_MESH_IP = TEST_IP
        mock_config.NODE_IS_HYPERVISOR = True
        mock_config.NODE_IS_NETWORK_NODE = False
        mock_load_uuid.return_value = TEST_UUID_STR

        mock_get_node.return_value = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=Node.current_version)
        attrs = NodeAttributesData(uuid=TEST_UUID)
        mock_get_attrs.return_value = attrs

        Node.observe_this_node()

        self.assertEqual(
            attrs.spice_server_cert_subject, 'CN=node1.example.com')
        _, kwargs = mock_save_attrs.call_args
        self.assertIn('spice_server_cert_subject', kwargs['fields'])

    @mock.patch('shakenfist.node.Node._persist_uuid')
    @mock.patch('shakenfist.node.Node._save_attributes')
    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    @mock.patch('shakenfist.node.mariadb.create_node_attributes')
    @mock.patch('shakenfist.node.add_event')
    @mock.patch('shakenfist.mariadb.set_state')
    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(
            value='initial', update_time=1234567890.0))
    @mock.patch('shakenfist.node.mariadb.get_node_by_fqdn')
    @mock.patch('shakenfist.node.mariadb.get_node')
    @mock.patch('shakenfist.node.mariadb.create_node')
    @mock.patch('shakenfist.node.Node._load_persisted_uuid')
    @mock.patch('shakenfist.node.config')
    def test_observe_creates_and_persists_new_node(
            self, mock_config, mock_load_uuid,
            mock_create_node, mock_get_node,
            mock_get_by_fqdn, mock_get_state,
            mock_get_min, mock_set_state,
            mock_add_event, mock_create_attrs,
            mock_get_attrs, mock_save_attrs,
            mock_persist_uuid):
        """Test observe_this_node creates node and persists UUID."""
        mock_config.NODE_NAME = TEST_FQDN
        mock_config.NODE_MESH_IP = TEST_IP
        mock_config.NODE_IS_HYPERVISOR = False
        mock_config.NODE_IS_NETWORK_NODE = False
        mock_load_uuid.return_value = None

        # this_node() returns None (no persisted UUID, FQDN not found)
        mock_get_node.return_value = None
        mock_get_by_fqdn.return_value = None

        # After Node.new() creates the node, from_db(uuid_str) finds it
        node_data = NodeData(
            uuid=TEST_UUID_STR, fqdn=TEST_FQDN,
            ip=TEST_IP, version=Node.current_version
        )

        def get_node_side_effect(u):
            if str(u) == TEST_UUID_STR:
                return node_data
            return None
        mock_get_node.side_effect = get_node_side_effect

        attrs = NodeAttributesData(uuid=TEST_UUID)
        mock_get_attrs.return_value = attrs
        mock_create_node.return_value = True

        with mock.patch('shakenfist.node.uuid.uuid4',
                        return_value=TEST_UUID):
            Node.observe_this_node()

        mock_persist_uuid.assert_called_once()
        mock_save_attrs.assert_called_once()


def _make_spice_cert(name_attrs):
    """Build a self-signed certificate whose subject carries exactly the
    given (oid, value) attributes in order, and return its PEM bytes."""
    import datetime

    from cryptography import x509 as _x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = ed25519.Ed25519PrivateKey.generate()
    subject = _x509.Name(
        [_x509.NameAttribute(oid, value) for oid, value in name_attrs])
    cert = (
        _x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(_x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1))
        .not_valid_after(datetime.datetime(2030, 1, 1))
        .sign(key, None))
    return cert.public_bytes(serialization.Encoding.PEM)


class NodeSpiceCertSubjectTestCase(base.ShakenFistTestCase):
    """Tests for reading the SPICE server certificate subject."""

    def _subject_for(self, name_attrs):
        pem = _make_spice_cert(name_attrs)
        with tempfile.NamedTemporaryFile(
                suffix='.pem', delete=False) as f:
            f.write(pem)
            path = f.name
        self.addCleanup(os.unlink, path)
        with mock.patch('shakenfist.node.SPICE_SERVER_CERT_PATH', path):
            return node_module.read_spice_server_cert_subject()

    def test_renders_attributes_in_certificate_order(self):
        """The subject is rendered in the certificate's attribute order
        with OpenSSL short names, matching the verifier's expectation."""
        subject = self._subject_for([
            (NameOID.COUNTRY_NAME, 'US'),
            (NameOID.ORGANIZATION_NAME, 'Shaken Fist'),
            (NameOID.COMMON_NAME, 'hv1'),
        ])
        self.assertEqual(subject, 'C=US,O=Shaken Fist,CN=hv1')

    def test_common_name_only(self):
        """The stock SF PKI mints a CN-only certificate."""
        subject = self._subject_for([(NameOID.COMMON_NAME, 'sf-1')])
        self.assertEqual(subject, 'CN=sf-1')

    def test_escapes_comma_and_backslash_in_values(self):
        subject = self._subject_for([
            (NameOID.ORGANIZATION_NAME, 'Acme, Inc'),
            (NameOID.COMMON_NAME, 'hv'),
        ])
        self.assertEqual(subject, 'O=Acme\\, Inc,CN=hv')

    def test_unnameable_attribute_disables_pinning(self):
        """A subject attribute with no accepted short name yields None so
        we never emit a partial (and therefore mismatching) subject."""
        subject = self._subject_for([
            (NameOID.COMMON_NAME, 'hv1'),
            (NameOID.SERIAL_NUMBER, '12345'),
        ])
        self.assertIsNone(subject)

    def test_missing_certificate_returns_none(self):
        with mock.patch(
                'shakenfist.node.SPICE_SERVER_CERT_PATH',
                '/nonexistent/server-cert.pem'):
            self.assertIsNone(node_module.read_spice_server_cert_subject())

    def test_unparseable_certificate_returns_none(self):
        with tempfile.NamedTemporaryFile(
                suffix='.pem', delete=False) as f:
            f.write(b'not a certificate')
            path = f.name
        self.addCleanup(os.unlink, path)
        with mock.patch('shakenfist.node.SPICE_SERVER_CERT_PATH', path):
            self.assertIsNone(node_module.read_spice_server_cert_subject())
