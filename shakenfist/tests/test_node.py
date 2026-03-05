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

from unittest import mock
from uuid import UUID

from pydantic import ValidationError
import testtools

from shakenfist.exceptions import NoSuchDaemon
from shakenfist.exceptions import NoSuchDaemonState
from shakenfist.node import Node
from shakenfist.schema.node_attributes import NodeAttributesData
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
        self.assertFalse(data.is_etcd_master)
        self.assertFalse(data.is_hypervisor)
        self.assertFalse(data.is_network_node)
        self.assertFalse(data.is_eventlog_node)
        self.assertEqual(data.instances, [])
        self.assertEqual(data.daemons, [])
        self.assertEqual(data.daemon_states, {})
        self.assertIsNone(data.qemu_version)

    def test_mutable(self):
        """Test that NodeAttributesData is mutable (not frozen)."""
        data = NodeAttributesData(uuid=TEST_UUID)
        data.last_seen = 9999.0
        self.assertEqual(data.last_seen, 9999.0)

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

    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    @mock.patch(
        'shakenfist.baseobject.get_minimum_object_version',
        return_value=Node.current_version)
    @mock.patch(
        'shakenfist.mariadb.get_state',
        return_value=State(value='created', update_time=1234567890.0))
    def test_load_attributes_returns_defaults_when_none(
            self, mock_get_state, mock_get_min,
            mock_get_attrs):
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

    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    def test_get_daemon_state_no_attributes(self, mock_get_attrs):
        """Test get_daemon_state when no attributes exist."""
        mock_get_attrs.return_value = None
        n = self._make_node()

        state = n.get_daemon_state('api')
        self.assertIsNone(state.value)
        self.assertEqual(state.update_time, 0)

    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    def test_get_daemon_state_with_value(self, mock_get_attrs):
        """Test get_daemon_state returns correct state."""
        attrs = NodeAttributesData(uuid=TEST_UUID)
        attrs.daemon_states['api'] = {
            'value': 'daemon-running',
            'update_time': 1234567890.0,
            'message': None
        }
        mock_get_attrs.return_value = attrs
        n = self._make_node()

        state = n.get_daemon_state('api')
        self.assertEqual(state.value, 'daemon-running')
        self.assertEqual(state.update_time, 1234567890.0)

    def test_get_daemon_state_invalid_daemon(self):
        """Test get_daemon_state raises for invalid daemon."""
        n = self._make_node()
        self.assertRaises(
            NoSuchDaemon, n.get_daemon_state, 'bogus')

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
            mock_create_attrs, mock_update_attrs):
        """Test registering a daemon adds it to the list."""
        attrs = NodeAttributesData(uuid=TEST_UUID)
        mock_get_attrs.return_value = attrs
        mock_lock.return_value = mock.MagicMock()
        mock_update_attrs.return_value = True

        n = self._make_node()
        n.register_daemon('api')

        self.assertIn('api', attrs.daemons)

    def test_register_invalid_daemon(self):
        """Test registering invalid daemon raises."""
        n = self._make_node()
        self.assertRaises(
            NoSuchDaemon, n.register_daemon, 'bogus')

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
            mock_update_attrs):
        """Test deregistering a daemon removes it."""
        attrs = NodeAttributesData(uuid=TEST_UUID)
        attrs.daemons.append('api')
        attrs.daemon_states['api'] = {
            'value': 'daemon-running',
            'update_time': 1234567890.0,
            'message': None
        }
        mock_get_attrs.return_value = attrs
        mock_lock.return_value = mock.MagicMock()
        mock_update_attrs.return_value = True

        n = self._make_node()
        n.deregister_daemon('api')

        self.assertNotIn('api', attrs.daemons)
        self.assertNotIn('api', attrs.daemon_states)


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
    @mock.patch('shakenfist.node.mariadb.create_node_attributes')
    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    def test_add_instance(
            self, mock_lock, mock_get_attrs,
            mock_create_attrs, mock_update_attrs):
        """Test adding an instance to a node."""
        attrs = NodeAttributesData(uuid=TEST_UUID)
        mock_get_attrs.return_value = attrs
        mock_lock.return_value = mock.MagicMock()
        mock_update_attrs.return_value = True

        n = self._make_node()
        inst_uuid = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        n.add_instance(inst_uuid)

        self.assertIn(inst_uuid, attrs.instances)

    @mock.patch('shakenfist.node.mariadb.update_node_attributes')
    @mock.patch('shakenfist.node.mariadb.create_node_attributes')
    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    def test_add_instance_idempotent(
            self, mock_lock, mock_get_attrs,
            mock_create_attrs, mock_update_attrs):
        """Test adding the same instance twice doesn't duplicate."""
        attrs = NodeAttributesData(uuid=TEST_UUID)
        mock_get_attrs.return_value = attrs
        mock_lock.return_value = mock.MagicMock()
        mock_update_attrs.return_value = True

        n = self._make_node()
        inst_uuid = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        n.add_instance(inst_uuid)
        # Invalidate so second add re-reads (but mock returns same)
        n._Node__attributes_loaded = False
        n.add_instance(inst_uuid)

        count = attrs.instances.count(inst_uuid)
        self.assertEqual(count, 1)

    @mock.patch('shakenfist.node.mariadb.update_node_attributes')
    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    def test_remove_instance(
            self, mock_lock, mock_get_attrs,
            mock_update_attrs):
        """Test removing an instance from a node."""
        attrs = NodeAttributesData(uuid=TEST_UUID)
        inst_uuid = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        attrs.instances.append(inst_uuid)
        mock_get_attrs.return_value = attrs
        mock_lock.return_value = mock.MagicMock()
        mock_update_attrs.return_value = True

        n = self._make_node()
        n.remove_instance(inst_uuid)

        self.assertNotIn(inst_uuid, attrs.instances)


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

    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    def test_no_degraded_when_all_running(self, mock_get_attrs):
        """Test no degraded daemons when all are running."""
        attrs = NodeAttributesData(uuid=TEST_UUID)
        attrs.daemons = ['api', 'database']
        attrs.daemon_states = {
            'api': {
                'value': 'daemon-running',
                'update_time': 1234567890.0,
                'message': None
            },
            'database': {
                'value': 'daemon-running',
                'update_time': 1234567890.0,
                'message': None
            }
        }
        mock_get_attrs.return_value = attrs

        n = self._make_node()
        degraded = n.get_degraded_daemons()
        self.assertEqual(degraded, [])

    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    def test_degraded_when_daemon_stopped(self, mock_get_attrs):
        """Test daemon shows as degraded when stopped."""
        attrs = NodeAttributesData(uuid=TEST_UUID)
        attrs.daemons = ['api', 'database']
        attrs.daemon_states = {
            'api': {
                'value': 'daemon-running',
                'update_time': 1234567890.0,
                'message': None
            },
            'database': {
                'value': 'daemon-stopped',
                'update_time': 1234567890.0,
                'message': None
            }
        }
        mock_get_attrs.return_value = attrs

        n = self._make_node()
        degraded = n.get_degraded_daemons()
        self.assertIn('database', degraded)
        self.assertNotIn('api', degraded)

    @mock.patch('shakenfist.node.mariadb.get_node_attributes')
    def test_degraded_when_no_state(self, mock_get_attrs):
        """Test daemon shows as degraded when it has no state."""
        attrs = NodeAttributesData(uuid=TEST_UUID)
        attrs.daemons = ['api']
        attrs.daemon_states = {}
        mock_get_attrs.return_value = attrs

        n = self._make_node()
        degraded = n.get_degraded_daemons()
        self.assertIn('api', degraded)
