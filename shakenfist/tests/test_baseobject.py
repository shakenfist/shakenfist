import time
from unittest import mock

import testtools
from shakenfist import baseobject
from shakenfist import exceptions
from shakenfist.artifact import Artifact
from shakenfist.baseobject import DatabaseBackedObject
from shakenfist.baseobject import State
from shakenfist.blob import Blob
from shakenfist.managed_executables.dnsmasq import DnsMasq
from shakenfist.namespace import Namespace
from shakenfist.node import Node
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB
from shakenfist.upload import Upload


class MaintainVersionCacheTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.mariadb.get_node')
    @mock.patch('shakenfist.mariadb.get_state',
                return_value=State(value=DatabaseBackedObject.STATE_CREATED,
                                   update_time=10))
    @mock.patch('shakenfist.mariadb.get_all_node_metrics')
    def test_refresh_does_not_fetch_node_objects(
            self, mock_all_metrics, mock_state, mock_get_node):
        # The version cache is fed from node metrics (which already carry
        # fqdn); it must not fan out a per-node get_node any more.
        mock_all_metrics.return_value = [{
            'node_uuid': '12345678-1234-4321-8234-123456789012',
            'fqdn': 'sf-1',
            'timestamp': time.time(),
            'metrics': {'object_version_node': 3},
        }]
        # Force a refresh rather than relying on the TTL.
        baseobject.VERSION_CACHE_MINIMUM = None
        baseobject.VERSION_CACHE_MAXIMUM = None
        baseobject.VERSION_CACHE_AGE = 0

        baseobject._maintain_version_cache(0)

        mock_get_node.assert_not_called()
        mock_all_metrics.assert_called_once()


class DatabaseBackedObjectTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.mariadb.get_state',
                side_effect=[
                    State(value=None, update_time=2),
                    State(value=DatabaseBackedObject.STATE_INITIAL, update_time=4),
                    State(value=DatabaseBackedObject.STATE_CREATED, update_time=10),
                ])
    def test_state(self, mock_mariadb_get_state):
        d = DatabaseBackedObject('12345678-1234-4321-8234-123456789012')
        self.assertEqual(d.state, State(value=None, update_time=2))
        self.assertEqual(d.state,
                         State(value=DatabaseBackedObject.STATE_INITIAL,
                               update_time=4))
        self.assertEqual(d.state,
                         State(value=DatabaseBackedObject.STATE_CREATED,
                               update_time=10))

    def test_unique_label_uuid_is_str(self):
        """unique_label() must return a str uuid, not a uuid.UUID.

        The tuple is logged as event 'extra' (baseoperation.defer's
        waiting_on list) and compared against str uuids (the unroute
        API); a raw uuid.UUID is not JSON serialisable and never
        compares equal to its string form (issue 3573).
        """
        d = DatabaseBackedObject('12345678-1234-4321-8234-123456789012')
        object_type, object_uuid = d.unique_label()
        self.assertIsInstance(object_uuid, str)
        self.assertEqual('12345678-1234-4321-8234-123456789012', object_uuid)

    def test_property_state_object_full(self):
        s = State(value='state1', update_time=3.0)

        self.assertEqual(s.value, 'state1')
        self.assertEqual(s.update_time, 3.0)

        self.assertEqual(s.obj_dict(), {
            'value': 'state1',
            'update_time': 3.0,
        })

        self.assertEqual(s, State(value='state1', update_time=3.0))
        self.assertEqual(str(s),
                         "State({'value': 'state1', 'update_time': 3.0})")

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.set_state', return_value=True)
    @mock.patch('shakenfist.mariadb.get_state',
                side_effect=[
                    State(value=DatabaseBackedObject.STATE_INITIAL, update_time=4,
                          message='stale message'),
                    State(value=DatabaseBackedObject.STATE_ERROR, update_time=4,
                          message='bad error'),
                    State(value=DatabaseBackedObject.STATE_INITIAL, update_time=4),
                    State(value=DatabaseBackedObject.STATE_ERROR, update_time=4),
                ])
    def test_property_error_msg(self, mock_mariadb_get_state,
                                mock_mariadb_set_state, mock_add_event):
        d = DatabaseBackedObject('12345678-1234-4321-8234-123456789012')

        # No message outside an error state, even if the state row has one
        self.assertEqual(d.error, None)

        # The message is read from the state row while in an error state
        self.assertEqual(d.error, 'bad error')

        # Setting a message is refused outside an error state...
        with testtools.ExpectedException(exceptions.InvalidStateException):
            d.error = 'real bad'
        mock_mariadb_set_state.assert_not_called()

        # ... and is persisted onto the state row inside one (issue 3899:
        # the previous attribute write was silently discarded for every
        # object type except Instance)
        d.error = 'real bad'
        mock_mariadb_set_state.assert_called_once()
        written = mock_mariadb_set_state.call_args.args[2]
        self.assertEqual(DatabaseBackedObject.STATE_ERROR, written.value)
        self.assertEqual('real bad', written.message)

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.set_state', return_value=True)
    @mock.patch('shakenfist.mariadb.get_state',
                return_value=State(value=DatabaseBackedObject.STATE_ERROR,
                                   update_time=4, message='bad error'))
    def test_property_error_msg_unchanged_message_not_rewritten(
            self, mock_mariadb_get_state, mock_mariadb_set_state,
            mock_add_event):
        d = DatabaseBackedObject('12345678-1234-4321-8234-123456789012')
        d.error = 'bad error'
        mock_mariadb_set_state.assert_not_called()


class ErrorMessageRoundTripTestCase(base.ShakenFistTestCase):
    """The error message must survive a database round trip for object
    types without an attribute persistence override. Before issue 3899
    every type except Instance silently discarded it."""

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

        add_event = mock.patch('shakenfist.eventlog.add_event')
        add_event.start()
        self.addCleanup(add_event.stop)

    def test_agent_operation_error_message_round_trips(self):
        aop = AgentOperation.new(
            'aaaabbbb-0000-4000-8000-00000000000a', 'ci',
            'aaaabbbb-0000-4000-8000-00000000000b',
            [{'command': 'execute'}])
        aop.state = AgentOperation.STATE_ERROR
        aop.error = 'preflight failure, blob missing'

        refetched = AgentOperation.from_db(
            'aaaabbbb-0000-4000-8000-00000000000a')
        self.assertEqual('preflight failure, blob missing', refetched.error)

        # Grouping references requires a flask request context, which is
        # not what this test is about
        with mock.patch(
                'shakenfist.operations.agentoperation.'
                'references_to_grouped_dict', return_value={}):
            self.assertEqual(
                'preflight failure, blob missing',
                refetched.external_view()['error_message'])

    def test_agent_operation_error_message_cleared_by_transition(self):
        aop = AgentOperation.new(
            'aaaabbbb-0000-4000-8000-00000000000c', 'ci',
            'aaaabbbb-0000-4000-8000-00000000000d',
            [{'command': 'execute'}])
        aop.state = AgentOperation.STATE_ERROR
        aop.error = 'went wrong'
        aop.state = AgentOperation.STATE_DELETED

        refetched = AgentOperation.from_db(
            'aaaabbbb-0000-4000-8000-00000000000c')
        self.assertIsNone(refetched.error)


class MissingObjectTestObject(DatabaseBackedObject):
    @classmethod
    def _db_get(cls, object_uuid):
        return None


class FromDbMissingObjectTestCase(base.ShakenFistTestCase):
    """A missing object is an ordinary outcome of from_db(), so the audit
    event it emits must not be logged as an error. All unsuppressed call
    sites share the one 'attempt to lookup non-existent object' log
    signature, which made it untriageable at ERROR (issue 3906)."""

    @mock.patch('shakenfist.eventlog.add_event')
    def test_missing_object_audit_is_not_an_error(self, mock_add_event):
        self.assertIsNone(MissingObjectTestObject.from_db(
            '12345678-1234-4321-8234-123456789012'))

        mock_add_event.assert_called_once()
        args, kwargs = mock_add_event.call_args
        self.assertEqual('attempt to lookup non-existent object', args[3])
        self.assertIn('caller', kwargs.get('extra', {}))
        self.assertFalse(kwargs.get('log_as_error', False))

    @mock.patch('shakenfist.eventlog.add_event')
    def test_missing_object_audit_can_be_suppressed(self, mock_add_event):
        self.assertIsNone(MissingObjectTestObject.from_db(
            '12345678-1234-4321-8234-123456789012',
            suppress_failure_audit=True))

        mock_add_event.assert_not_called()

    def test_overridden_from_db_audit_is_not_an_error(self):
        """Blob, Artifact, Namespace, Upload, Node and DnsMasq carry their
        own copies of the from_db() missing-object audit, so they must not
        log it as an error either (issue 3906)."""
        for cls, add_event_target in (
                (Blob, 'shakenfist.blob.add_event'),
                (Artifact, 'shakenfist.artifact.add_event'),
                (Namespace, 'shakenfist.namespace.add_event'),
                (Upload, 'shakenfist.eventlog.add_event'),
                (Node, 'shakenfist.node.add_event'),
                (DnsMasq, 'shakenfist.eventlog.add_event')):
            with mock.patch.object(cls, '_db_get', return_value=None), \
                    mock.patch(add_event_target) as mock_add_event:
                self.assertIsNone(cls.from_db(
                    '12345678-1234-4321-8234-123456789012'))

                mock_add_event.assert_called_once()
                args, kwargs = mock_add_event.call_args
                self.assertEqual(
                    'attempt to lookup non-existent object', args[3],
                    cls.__name__)
                self.assertFalse(
                    kwargs.get('log_as_error', False), cls.__name__)


class InMemoryStateTestObject(DatabaseBackedObject):
    state_targets = {
        None: (DatabaseBackedObject.STATE_CREATED, ),
        DatabaseBackedObject.STATE_CREATED: (
            DatabaseBackedObject.STATE_DELETED,
            DatabaseBackedObject.STATE_ERROR),
        DatabaseBackedObject.STATE_ERROR: (),
        DatabaseBackedObject.STATE_DELETED: (),
    }


class InMemoryOnlyStateTestCase(base.ShakenFistTestCase):
    """In-memory only objects must never read or write primary state in
    MariaDB. A state row written by an in-memory object leaks forever:
    hard_delete() early-returns for in-memory objects and state-driven
    iterators skip objects with no static row, so nothing can remove it
    (issue 3532)."""

    @mock.patch('shakenfist.mariadb.set_state')
    @mock.patch('shakenfist.mariadb.get_state')
    def test_in_memory_state_never_touches_mariadb(
            self, mock_get_state, mock_set_state):
        d = InMemoryStateTestObject(
            '12345678-1234-4321-8234-123456789012', in_memory_only=True)
        self.assertIsNone(d.state.value)

        d.state = DatabaseBackedObject.STATE_CREATED
        self.assertEqual(DatabaseBackedObject.STATE_CREATED, d.state.value)

        d.state = DatabaseBackedObject.STATE_DELETED
        self.assertEqual(DatabaseBackedObject.STATE_DELETED, d.state.value)

        mock_get_state.assert_not_called()
        mock_set_state.assert_not_called()

    @mock.patch('shakenfist.mariadb.set_state')
    @mock.patch('shakenfist.mariadb.get_state')
    def test_in_memory_state_still_validates_transitions(
            self, mock_get_state, mock_set_state):
        d = InMemoryStateTestObject(
            '12345678-1234-4321-8234-123456789012', in_memory_only=True)

        with testtools.ExpectedException(exceptions.InvalidStateException):
            d.state = DatabaseBackedObject.STATE_DELETED

        mock_get_state.assert_not_called()
        mock_set_state.assert_not_called()

    @mock.patch('shakenfist.mariadb.set_state')
    @mock.patch('shakenfist.mariadb.get_state')
    def test_in_memory_error_never_touches_mariadb(
            self, mock_get_state, mock_set_state):
        d = InMemoryStateTestObject(
            '12345678-1234-4321-8234-123456789012', in_memory_only=True)
        d.state = DatabaseBackedObject.STATE_CREATED
        d.state = DatabaseBackedObject.STATE_ERROR

        d.error = 'in memory error'
        self.assertEqual('in memory error', d.error)

        mock_get_state.assert_not_called()
        mock_set_state.assert_not_called()
