# Copyright 2026 Michael Still and contributors
"""An instance whose network has failed should be able to tell.

Failed network operations record an ErrorReport, can move the network
to STATE_ERROR, and the maintenance pass has a per-network circuit
breaker which quiesces a network whose last K repairs all errored. All
of that is recorded on the network and nowhere else, so an instance
sitting on a network which cannot be brought up still reads `created`
and healthy, and its owner has no way to find out from the object they
are actually looking at (issue 520).

The signal is an event rather than a state change. The instance has
not failed -- its guest is running and its disks are fine -- so
erroring it would be a lie.
"""

from functools import partial
from unittest import mock
from uuid import UUID

from shakenfist import baseobject
from shakenfist.baseobject import State
from shakenfist.daemons.network import maintain
from shakenfist.network.network import Network
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


NETWORK_UUID = 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa'
INSTANCE_ONE = 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb'
INSTANCE_TWO = 'cccccccc-3333-4333-8333-cccccccccccc'


def _interface(instance_uuid):
    ni = mock.MagicMock()
    ni.instance_uuid = instance_uuid
    return ni


class SignalInstancesTestCase(base.ShakenFistTestCase):
    def _network(self):
        n = mock.MagicMock()
        n.uuid = NETWORK_UUID
        n.signal_instances_of_failure = partial(
            Network.signal_instances_of_failure, n)
        return n

    def _signal(self, interfaces, reason='because'):
        # The interfaces come from the network's own pushed-down query.
        # Iterating NetworkInterfaces with a Python filter would read
        # every active interface in the cluster to find these, so it
        # is made to fail rather than merely left unused.
        n = self._network()
        n.networkinterfaces = interfaces
        with mock.patch(
                'shakenfist.network.network.interface.NetworkInterfaces',
                side_effect=AssertionError('cluster-wide interface scan')):
            with mock.patch(
                    'shakenfist.network.network.add_event_multi') as events:
                signalled = n.signal_instances_of_failure(reason)
        return signalled, events

    def test_every_attached_instance_is_told(self):
        signalled, events = self._signal(
            [_interface(INSTANCE_ONE), _interface(INSTANCE_TWO)])

        self.assertEqual([INSTANCE_ONE, INSTANCE_TWO], signalled)
        events.assert_called_once()
        self.assertEqual(
            [(ObjectType.INSTANCE, INSTANCE_ONE),
             (ObjectType.INSTANCE, INSTANCE_TWO)],
            events.call_args[0][1])

    def test_the_reason_and_the_network_travel_with_it(self):
        # An event which says only "something is wrong" sends the owner
        # back to the object they could not read in the first place.
        _, events = self._signal([_interface(INSTANCE_ONE)], reason='on fire')

        extra = events.call_args.kwargs['extra']
        self.assertEqual(NETWORK_UUID, extra['network_uuid'])
        self.assertEqual('on fire', extra['reason'])

    def test_an_interface_with_no_instance_is_skipped(self):
        # A network interface can exist without an instance attached.
        signalled, events = self._signal(
            [_interface(None), _interface(INSTANCE_ONE)])

        self.assertEqual([INSTANCE_ONE], signalled)

    def test_each_instance_is_named_once(self):
        # An instance may hold more than one interface on the same
        # network.
        signalled, _ = self._signal(
            [_interface(INSTANCE_ONE), _interface(INSTANCE_ONE)])

        self.assertEqual([INSTANCE_ONE], signalled)

    def test_an_empty_network_records_nothing(self):
        signalled, events = self._signal([])

        self.assertEqual([], signalled)
        events.assert_not_called()


class EnterErrorStateTestCase(base.ShakenFistTestCase):
    """Erroring a network tells the instances on it."""

    class _Network(Network):
        """A Network with only the pieces this behaviour touches.

        A real instance rather than a mock, because the override calls
        super() and mock cannot stand in for a class in that call.
        """

        def __init__(self, current_state):
            self._current_state = current_state
            self.signalled = []

        @property
        def uuid(self):
            return NETWORK_UUID

        @property
        def state(self):
            return State(value=self._current_state, update_time=10)

        def signal_instances_of_failure(self, reason):
            self.signalled.append(reason)
            return [INSTANCE_ONE]

    def test_erroring_a_network_signals_its_instances(self):
        n = self._Network(Network.STATE_CREATED)
        with mock.patch(
                'shakenfist.baseobject.DatabaseBackedObject.'
                'enter_error_state'):
            n.enter_error_state(Network.STATE_ERROR, 'no floating gateway')

        self.assertEqual(['no floating gateway'], n.signalled)

    def test_an_already_errored_network_does_not_signal_again(self):
        # The move from one error state to another is bookkeeping. The
        # owner has been told, and repeating it is noise on an object
        # they are already looking at.
        n = self._Network(Network.STATE_ERROR)
        with mock.patch(
                'shakenfist.baseobject.DatabaseBackedObject.'
                'enter_error_state'):
            n.enter_error_state(Network.STATE_ERROR, 'still broken')

        self.assertEqual([], n.signalled)

    def test_the_state_is_written_before_the_signal(self):
        # If the write fails there is nothing to tell anyone about.
        n = self._Network(Network.STATE_CREATED)
        with mock.patch(
                'shakenfist.baseobject.DatabaseBackedObject.'
                'enter_error_state', side_effect=RuntimeError('no')):
            self.assertRaises(
                RuntimeError, n.enter_error_state,
                Network.STATE_ERROR, 'no floating gateway')

        self.assertEqual([], n.signalled)

    def test_a_missing_reason_is_not_passed_on_empty(self):
        # The instances are told the same reason the network records,
        # and an empty one tells them nothing.
        n = self._Network(Network.STATE_CREATED)
        with mock.patch(
                'shakenfist.baseobject.DatabaseBackedObject.'
                'enter_error_state') as mock_super:
            with mock.patch('shakenfist.baseobject.LOG') as mock_log:
                n.enter_error_state(Network.STATE_ERROR, '')

        self.assertEqual([baseobject.NO_REASON_RECORDED], n.signalled)
        mock_super.assert_called_once_with(
            Network.STATE_ERROR, baseobject.NO_REASON_RECORDED,
            skip_transition_validation=False)
        caller = mock_log.with_fields.call_args[0][0]['caller']
        self.assertIn('test_a_missing_reason_is_not_passed_on_empty()',
                      caller)

    def test_the_write_is_passed_straight_through(self):
        n = self._Network(Network.STATE_CREATED)
        with mock.patch(
                'shakenfist.baseobject.DatabaseBackedObject.'
                'enter_error_state') as mock_super:
            n.enter_error_state(
                Network.STATE_ERROR, 'a reason',
                skip_transition_validation=True)

        mock_super.assert_called_once_with(
            Network.STATE_ERROR, 'a reason',
            skip_transition_validation=True)


class QuiesceSignalTestCase(base.ShakenFistTestCase):
    """The circuit breaker is the common case, and it used to be silent."""

    def setUp(self):
        super().setUp()
        maintain.QUIESCE_REPORTED.clear()
        self.addCleanup(maintain.QUIESCE_REPORTED.clear)

    def _guard(self, history_states):
        m = mock.MagicMock()
        m._reconciliation_guard_fired = partial(
            maintain.Job._reconciliation_guard_fired, m)

        n = mock.MagicMock()
        n.uuid = NETWORK_UUID
        n.signal_instances_of_failure.return_value = [INSTANCE_ONE]

        circuit_k = len(history_states)
        history = [('net_op', state, 0.0) for state in history_states]

        with mock.patch.object(
                maintain.config, 'MAINTAIN_RECONCILE_CIRCUIT_K', circuit_k):
            with mock.patch.object(
                    maintain.config,
                    'MAINTAIN_RECONCILE_COOLDOWN_SECONDS', 0):
                with mock.patch.object(
                        maintain.mariadb,
                        'get_recent_terminal_op_states_for_target',
                        side_effect=lambda **kwargs: (
                            history[:kwargs['limit']])):
                    fired = m._reconciliation_guard_fired(n, 'net_op')
        return fired, n

    def test_a_quiesced_network_tells_its_instances(self):
        fired, n = self._guard(['error', 'error', 'error'])

        self.assertTrue(fired)
        n.signal_instances_of_failure.assert_called_once()
        self.assertIn(
            'quiesced', n.signal_instances_of_failure.call_args[0][0])

    def test_it_is_said_once_per_episode_not_once_per_pass(self):
        # This branch is reached every 30 seconds for as long as the
        # network stays quiesced.
        self._guard(['error', 'error', 'error'])
        _, n = self._guard(['error', 'error', 'error'])

        n.signal_instances_of_failure.assert_not_called()

    def test_a_network_which_recovers_is_reported_again_if_it_breaks(self):
        self._guard(['error', 'error', 'error'])
        # A pass where the breaker does not fire clears the episode.
        self._guard(['complete', 'error', 'error'])
        _, n = self._guard(['error', 'error', 'error'])

        n.signal_instances_of_failure.assert_called_once()

    def test_a_healthy_network_is_not_quiesced_and_says_nothing(self):
        fired, n = self._guard(['complete', 'complete', 'complete'])

        self.assertFalse(fired)
        n.signal_instances_of_failure.assert_not_called()

    def test_a_network_no_pass_visits_is_forgotten(self):
        # Deleted while quiesced: it never reconciles cleanly, so only
        # the end-of-pass prune removes it.
        self._guard(['error', 'error', 'error'])
        self.assertIn(NETWORK_UUID, maintain.QUIESCE_REPORTED)

        maintain.forget_unvisited_quiesces(['some-other-network'])
        self.assertNotIn(NETWORK_UUID, maintain.QUIESCE_REPORTED)

    def test_a_network_a_pass_visits_is_remembered(self):
        self._guard(['error', 'error', 'error'])
        maintain.forget_unvisited_quiesces(
            [UUID(NETWORK_UUID), 'some-other-network'])
        self.assertIn(NETWORK_UUID, maintain.QUIESCE_REPORTED)
